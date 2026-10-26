"""启动预加载一次；所有库操作在同一线程内，超时不伪称任务已终止。"""
from concurrent.futures import Future, TimeoutError as FutureTimeout
from copy import deepcopy
from dataclasses import dataclass, field
import queue
import threading
import time

from .backend import StoreBackend
from .config import RuntimeOptions
from .errors import BackendError, RetrievalError, RetrievalTimeout


@dataclass
class _Job:
    operation: str
    arguments: dict
    queued_deadline: float
    future: Future = field(default_factory=Future)
    started: threading.Event = field(default_factory=threading.Event)
    execution_deadline: float = 0.0


class RagKnowledgeBase:
    name = "guance-rag-local"

    def __init__(self, options=None, *, backend_factory=StoreBackend):
        self.options = options or RuntimeOptions()
        self._queue = queue.Queue(self.options.queue_capacity)
        self._stop = threading.Event()
        self._ready = Future()
        self._state_lock = threading.Lock()
        self._state = "starting"
        self._close_error = None
        self._thread = threading.Thread(target=self._worker, args=(backend_factory,), name="guance-rag-store", daemon=True)
        self._thread.start()
        try:
            self._ready.result(timeout=self.options.startup_timeout)
        except FutureTimeout:
            self._stop.set()
            raise RetrievalTimeout("RAG启动未在时限内完成，后台清理结束前不能重开同一路径") from None

    def _worker(self, factory):
        backend = None
        try:
            backend = factory(self.options)
            with self._state_lock:
                self._state = "ready" if not self._stop.is_set() else "closing"
            self._ready.set_result(True)
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    job = self._queue.get(timeout=.1)
                except queue.Empty:
                    continue
                if not job.future.set_running_or_notify_cancel():
                    job.started.set()
                    self._queue.task_done()
                    continue
                job.execution_deadline = time.monotonic() + self.options.execution_timeout
                job.started.set()
                try:
                    if time.monotonic() > job.queued_deadline:
                        raise RetrievalTimeout("RAG请求排队超时")
                    result = getattr(backend, job.operation)(**job.arguments, deadline=job.execution_deadline)
                    if time.monotonic() > job.execution_deadline:
                        raise RetrievalTimeout("RAG执行超时，底层任务已在返回前收尾")
                    if job.operation == "stats":
                        result.update(worker_thread_id=threading.get_ident(), runtime_instance_id=id(self), queue_size=self._queue.qsize())
                    job.future.set_result(result)
                except RetrievalError as exc:
                    job.future.set_exception(exc)
                except Exception as exc:
                    job.future.set_exception(BackendError("RAG后端操作失败：" + type(exc).__name__))
                finally:
                    self._queue.task_done()
        except Exception as exc:
            error = exc if isinstance(exc, RetrievalError) else BackendError("RAG启动失败：" + type(exc).__name__)
            if not self._ready.done():
                self._ready.set_exception(error)
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception as exc:
                    self._close_error = BackendError("RAG关闭未成功：" + type(exc).__name__)
            with self._state_lock:
                self._state = "closed"

    def _submit(self, operation, **arguments):
        # 只复制本次请求参数；调用方后续修改列表不能改变排队任务，索引仍由库线程共享。
        job = _Job(operation, deepcopy(arguments), time.monotonic() + self.options.queue_timeout)
        with self._state_lock:
            if self._state != "ready":
                raise BackendError("RAG尚未就绪或已关闭")
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                raise RetrievalTimeout("RAG有界队列已满") from None
        if not job.started.wait(self.options.queue_timeout):
            job.future.cancel()  # 只能取消尚未执行的请求，不中断已经运行的库操作。
            raise RetrievalTimeout("RAG请求排队超时")
        try:
            return job.future.result(timeout=max(0, job.execution_deadline - time.monotonic()))
        except FutureTimeout:
            if job.future.done():
                raise
            job.future.cancel()
            raise RetrievalTimeout("RAG执行等待超时；库线程仍拥有资源直到实际任务结束") from None

    def search(self, query, area=None, fyp=None, top_k=8, *, source_types=None, planids=None):
        return self._submit("search", query=query, area=area, fyp=fyp, top_k=top_k,
                            source_types=source_types, planids=planids)

    def find_by_title(self, title, *, area=None, fyp=None):
        """按标题精确定位当前发布中的文档，返回候选与真实匹配数量。"""
        return self._submit("find_by_title", title=title, area=area, fyp=fyp)

    def read(self, planid, section_idx=None, max_chars=None, *, chunk_id=None, source_id=None, offset=0,
             document_id=None, generation_id=None):
        return self._submit("read", planid=planid, section_idx=section_idx,
                            max_chars=self.options.max_read_chars if max_chars is None else max_chars,
                            chunk_id=chunk_id, source_id=source_id, offset=offset, document_id=document_id, generation_id=generation_id)

    def get_chunk_source(self, chunk_id, *, document_id=None, generation_id=None):
        """程序/原文回看用途，允许完整长块；不应直接无预算塞给模型。"""
        return self._submit("get_chunk_source", chunk_id=chunk_id, document_id=document_id, generation_id=generation_id)

    def verify_quote(self, chunk_id, quote, observed_ranges, *, document_id=None, generation_id=None):
        return self._submit("verify_quote", chunk_id=chunk_id, quote=quote, observed_ranges=observed_ranges,
                            document_id=document_id, generation_id=generation_id)

    def stats(self):
        return self._submit("stats")

    def read_image(self, source_id, *, variant="original", document_id=None, generation_id=None):
        return self._submit("read_image", source_id=source_id, variant=variant, document_id=document_id, generation_id=generation_id)

    def verify_media_quote(self, source_id, quote, observed_ranges, *, document_id=None, generation_id=None):
        return self._submit("verify_media_quote", source_id=source_id, quote=quote, observed_ranges=observed_ranges,
                            document_id=document_id, generation_id=generation_id)

    def close(self, timeout=None):
        with self._state_lock:
            if self._state != "closed":
                self._state = "closing"
            self._stop.set()
        self._thread.join(self.options.close_timeout if timeout is None else timeout)
        if self._thread.is_alive():
            raise RetrievalTimeout("RAG工作线程仍在执行，尚未释放库路径")
        if self._close_error:
            raise self._close_error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
