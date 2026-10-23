from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from guance_rag import RagKnowledgeBase, RuntimeOptions
from guance_rag.errors import BackendError, RetrievalTimeout


class ControlledBackend:
    def __init__(self, options):
        self.owner = threading.get_ident()
        self.entered, self.release, self.closed = threading.Event(), threading.Event(), threading.Event()
        self.executed = []

    def read(self, planid, *, deadline, **kwargs):
        assert threading.get_ident() == self.owner
        self.executed.append(planid)
        if planid == "slow":
            self.entered.set()
            self.release.wait(3)
        return planid

    def stats(self, *, deadline):
        return {"owner": self.owner}

    def close(self):
        assert threading.get_ident() == self.owner
        self.closed.set()


def test_running_timeout_keeps_resource_until_actual_completion():
    backend = ControlledBackend(None)
    def factory(options):
        backend.owner = threading.get_ident()
        return backend
    options = RuntimeOptions(execution_timeout=.08, queue_timeout=.08, queue_capacity=1)
    kb = RagKnowledgeBase(options, backend_factory=factory)
    with ThreadPoolExecutor(3) as pool:
        slow = pool.submit(kb.read, "slow")
        assert backend.entered.wait(1)
        queued = pool.submit(kb.read, "queued")
        # 等待队列满的可观察状态，不依赖固定睡眠决定请求是否已入队。
        until = time.monotonic() + 1
        while kb._queue.empty() and time.monotonic() < until:
            time.sleep(.001)
        with pytest.raises(RetrievalTimeout, match="队列已满"):
            kb.read("overflow")
        with pytest.raises(RetrievalTimeout):
            slow.result(1)
        with pytest.raises(RetrievalTimeout, match="排队超时"):
            queued.result(1)
        assert not backend.closed.is_set()
        with pytest.raises(RetrievalTimeout, match="尚未释放"):
            kb.close(timeout=.01)
        backend.release.set()
    kb.close(timeout=1)
    assert backend.executed == ["slow"] and backend.closed.is_set()
    with pytest.raises(BackendError):
        kb.read("closed")


def test_startup_failure_is_not_reported_ready():
    def factory(options):
        raise ValueError("private URL must not leak")
    with pytest.raises(BackendError, match="ValueError") as error:
        RagKnowledgeBase(backend_factory=factory)
    assert "private" not in str(error.value)


def test_startup_timeout_eventually_closes_on_owner_thread():
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    class Backend:
        def __init__(self, options):
            self.owner = threading.get_ident()
            entered.set()
            release.wait(2)
        def close(self):
            assert threading.get_ident() == self.owner
            closed.set()
    try:
        with pytest.raises(RetrievalTimeout, match="启动"):
            RagKnowledgeBase(RuntimeOptions(startup_timeout=.05), backend_factory=Backend)
        assert entered.is_set() and not closed.is_set()
    finally:
        release.set()
    assert closed.wait(1)
