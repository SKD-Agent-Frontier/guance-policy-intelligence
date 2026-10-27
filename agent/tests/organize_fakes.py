"""Offline mock fixtures only; neither this KB nor model output is real acceptance."""

import json
import time
from types import SimpleNamespace

from guance_agent.business import BusinessError
from guance_agent.config import Settings
from guance_agent.context import RunContext
from guance_agent.kb import KBNoText, KBNotFound
from guance_agent.routing import decide
from guance_agent.schemas import ChunkSpan, Hit, Passage, TaskRequest, ToolResult


class MultiDocKB:
    name = "organize-mock"

    def __init__(self, count=4, chunks=3):
        self.search_calls, self.read_calls = [], []
        self.no_text, self.fail_reads, self.bad_coordinates = set(), set(), set()
        self.search_statuses = []
        self.hits = []
        for i in range(count):
            pid = f"P{i:03}"
            for j in range(chunks):
                body = f"建设科技创新平台{i * 10 + j + 20}个。培养科研人才{i * 10 + j + 100}名。完善成果转化服务体系。"
                self.hits.append(Hit(
                    planid=pid, chunk_id=f"{pid}-c{j}", title=f"模拟政策{2020 + i}年",
                    area="北京市", fyp="第十四个五年规划", admin_level="市",
                    plantype_label="专项规划", section_idx=j, section_path=f"章节{j}",
                    content=body, char_start=0, char_end=len(body),
                    chunk_char_start=0, chunk_char_end=len(body),
                    section_char_start=0, section_char_end=len(body), score=100 - j,
                ))

    def search(self, query, area, fyp, top_k, *, planids=None):
        self.search_calls.append((query, area, fyp, top_k, planids))
        if self.search_statuses:
            status = self.search_statuses.pop(0)
            if status == "backend_error":
                raise RuntimeError("mock backend failure")
            if status == "empty":
                return []
        return [h for h in self.hits if (planids is None or h.planid in planids)
                and (not area or h.area == area) and (not fyp or h.fyp == fyp)][:top_k]

    def find_by_title(self, title, *, area=None, fyp=None):
        ids = list(dict.fromkeys(h.planid for h in self.hits if title in h.title))
        return ([{"planid": pid} for pid in ids], len(ids))

    def get_chunk_source(self, chunk_id, *, document_id=None, generation_id=None):
        hit = next((h for h in self.hits if h.chunk_id == chunk_id), None)
        if hit is None:
            raise KBNotFound(chunk_id)
        span = ChunkSpan(chunk_id=hit.chunk_id, char_start=hit.char_start,
                         char_end=hit.char_end, chunk_char_start=hit.chunk_char_start,
                         chunk_char_end=hit.chunk_char_end, complete=True,
                         section_char_start=hit.section_char_start,
                         section_char_end=hit.section_char_end, section_path=hit.section_path)
        return Passage(**{k: v for k, v in hit.model_dump().items()
                          if k in Passage.model_fields}, read_scope="chunk", chunks=[span])

    def read(self, planid, section_idx, max_chars, *, chunk_id=None, offset=0):
        self.read_calls.append((planid, chunk_id, offset))
        if planid in self.no_text:
            raise KBNoText(planid, "mock document has no text")
        if planid in self.fail_reads:
            raise KBNotFound("mock document not found")
        passage = self.get_chunk_source(chunk_id)
        if passage.planid != planid:
            raise KBNotFound(planid)
        content = passage.content[offset:offset + max_chars]
        return passage.model_copy(update={
            "content": content, "char_start": offset,
            "char_end": offset + len(content) + (1 if planid in self.bad_coordinates else 0),
        })

    def verify_quote(self, chunk_id, quote, observed_ranges, *,
                     document_id=None, generation_id=None):
        source = self.get_chunk_source(chunk_id)
        base = source.chunk_char_start or 0
        return bool(quote) and any(quote in source.content[max(0, a - base):max(0, b - base)]
                                   for a, b in observed_ranges)


def mock_context(question="梳理十四五时期的科技创新政策", *, kb=None, targets=None,
                 dimensions=None, settings=None):
    settings = settings or Settings()
    kb = kb or MultiDocKB()
    task = TaskRequest(run_id="mock-run", session_id="mock-session", question=question,
                       created_at="2026-10-28T00:00:00Z", task_type="organize",
                       targets=targets, dimensions=dimensions or [])
    ctx = RunContext(settings, kb, task.run_id, task.session_id, question=question)
    ctx.route, ctx.scope = decide(question), tuple(targets) if targets is not None else None
    return task, ctx


class MockServices:
    """Deterministic service double; integration tests use real BusinessServices."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.deadline = time.monotonic() + 180
        self.queries, self.reads, self.messages = [], [], []
        self.statuses, self.read_statuses = [], []
        self.read_error = None
        self.register_reads = True

    async def search(self, query, **kwargs):
        self.queries.append((query, kwargs))
        status = self.statuses.pop(0) if self.statuses else "ok"
        hits = self.ctx.kb.search(query, None, None, 1000, planids=self.ctx.scope)
        return ToolResult(status=status, data=hits if status == "ok" else None)

    async def read(self, planid, *, chunk_id):
        self.reads.append((planid, chunk_id))
        if self.read_error:
            raise BusinessError(self.read_error)
        status = self.read_statuses.pop(0) if self.read_statuses else "ok"
        if status != "ok":
            return ToolResult(status=status)
        passage = self.ctx.kb.read(planid, None, 6000, chunk_id=chunk_id)
        if self.register_reads:
            self.ctx.evidence.add_result(passage, "kb_read")
        return ToolResult(status="ok", data=passage)


def mock_payload(kb):
    policies = []
    for pid in dict.fromkeys(h.planid for h in kb.hits):
        hit = next(h for h in kb.hits if h.planid == pid)
        policies.append(dict(planid=pid, relevance="high", points=[dict(
            topic="创新平台", text=hit.content.split("。")[0], chunk_id=hit.chunk_id,
            anchors=["科技创新", "平台"])]))
    return {"policies": policies}


def mock_model_response(payload):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps(payload, ensure_ascii=False)), finish_reason="stop")], usage=None)
