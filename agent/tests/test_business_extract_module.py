import json
from types import SimpleNamespace

import pytest

from guance_agent.business.contract import BusinessError, BusinessOutcome
from guance_agent.business.extract import ExtractModule
from guance_agent.extract import extraction_query
from guance_agent.schemas import Hit, Passage, TaskRequest, ToolResult


def _task(**updates):
    values = {"run_id": "r", "session_id": "s", "question": "提取科技创新政策要素",
              "created_at": "now", "task_type": "extract"}
    values.update(updates)
    return TaskRequest(**values)


def _hit(planid="P1", chunk_id="C1", title="政策一"):
    return Hit(planid=planid, chunk_id=chunk_id, title=title, area="甲市", fyp="十四五",
               admin_level=None, plantype_label=None, section_idx=0, section_path="第一章",
               content="政策通过补贴支持企业。", char_start=0, char_end=12, score=1.0)


def _passage(hit):
    return Passage(planid=hit.planid, title=hit.title, area=hit.area, fyp=hit.fyp,
                   admin_level=None, plantype_label=None, section_idx=hit.section_idx,
                   section_path=hit.section_path, content=hit.content,
                   char_start=0, char_end=len(hit.content), read_scope="chunk",
                   chunk_id=hit.chunk_id, chunk_char_start=0,
                   chunk_char_end=len(hit.content))


class FakeServices:
    def __init__(self, hits, *, search_status="ok", read_status="ok", raw=None):
        self.hits = hits
        self.search_status = search_status
        self.read_status = read_status
        self.raw = raw or json.dumps({
            "answer": "通过补贴支持企业。",
            "claims": [{"text": "通过补贴支持企业。", "dimension": "政策工具",
                        "chunk_ids": [hits[0].chunk_id if hits else "C1"],
                        "anchors": ["补贴"]}],
        }, ensure_ascii=False)
        self.search_calls = []
        self.read_calls = []
        self.model_calls = []

    async def search(self, query):
        self.search_calls.append(query)
        return ToolResult(status=self.search_status,
                          data=self.hits if self.search_status == "ok" else None)

    async def read(self, planid, *, chunk_id):
        self.read_calls.append((planid, chunk_id))
        hit = next(item for item in self.hits if item.chunk_id == chunk_id)
        return ToolResult(status=self.read_status,
                          data=_passage(hit) if self.read_status == "ok" else None)

    async def call_model(self, messages):
        self.model_calls.append(messages)
        return self.raw


def _context():
    return SimpleNamespace(tool_calls=[])


async def test_extract_module_reuses_bounded_search_read_and_model_flow():
    hits = [_hit("P1", "C1"), _hit("P2", "C2", "政策二"),
            _hit("P3", "C3", "政策三")]
    services = FakeServices(hits)

    result = await ExtractModule()(_task(dimensions=["政策工具", "责任主体"]),
                                   _context(), services)

    assert isinstance(result, BusinessOutcome)
    assert services.search_calls == ["提取科技创新政策要素"]
    assert services.read_calls == [("P1", "C1"), ("P2", "C2")]
    assert len(services.model_calls) == 1
    assert len(services.model_calls[0][0]["content"]) <= 7500
    assert result.claims[0].chunk_ids == ["C1"]
    assert result.structured["dimensions"] == ["政策工具", "责任主体"]
    assert result.structured["documents"] == [{"planid": "P1", "title": "政策一",
                                                "area": "甲市", "fyp": "十四五"}]
    assert result.structured["missing"] == [
        {"dimension": "责任主体", "reason": "not_retrieved"}]


@pytest.mark.parametrize("status", ["empty", "timeout", "backend_error"])
async def test_extract_module_returns_insufficient_without_model_when_search_fails(status):
    services = FakeServices([], search_status=status)

    result = await ExtractModule()(_task(dimensions=["期限"]), _context(), services)

    assert result.insufficient is True
    assert result.claims == []
    reason = "retrieval_unavailable" if status in {"timeout", "backend_error"} else "not_retrieved"
    assert result.structured["missing"] == [{"dimension": "期限", "reason": reason}]
    assert services.model_calls == []


@pytest.mark.parametrize("status,reason", [
    ("no_text", "not_retrieved"),
    ("timeout", "retrieval_unavailable"),
    ("backend_error", "retrieval_unavailable"),
])
async def test_extract_module_returns_insufficient_when_all_reads_fail(status, reason):
    services = FakeServices([_hit()], read_status=status)

    result = await ExtractModule()(_task(dimensions=["期限"]), _context(), services)

    assert result.insufficient is True
    assert result.claims == []
    assert result.structured["missing"] == [{"dimension": "期限", "reason": reason}]
    assert services.model_calls == []


@pytest.mark.parametrize(("quote", "expected"), [
    ("政策通过补贴支持企业。", ["政策通过补贴支持企业。"]),
    ("补贴政策大力支持企业发展", []),
    (None, ["政策通过补贴支持企业。"]),
])
async def test_extract_module_never_invents_quotes_and_keeps_candidates(quote, expected):
    payload = {"answer": "通过补贴支持企业。", "claims": [{
        "text": "通过补贴支持企业。", "dimension": "政策工具", "chunk_ids": ["C1"],
        "anchors": ["补贴"], "quotes": [quote] if quote is not None else [],
    }]}
    services = FakeServices([_hit()], raw=json.dumps(payload, ensure_ascii=False))

    result = await ExtractModule()(_task(), _context(), services)

    assert len(result.claims) == 1
    assert result.claims[0].quotes == expected
    assert result.claims[0].anchors == ["补贴"]


async def test_extract_module_maps_unparseable_model_output_to_business_error():
    services = FakeServices([_hit()], raw="not json")

    with pytest.raises(BusinessError) as error:
        await ExtractModule()(_task(), _context(), services)

    assert error.value.code == "OUTPUT_PARSE"


@pytest.mark.parametrize(("question", "dimensions", "expected"), [
    ("从《某省年度报告》中提取当年为企业降低成本的量化目标，并引用原文。", ["量化目标"], "企业降低成本"),
    ("请提取《服务业政策》中的2025年贷款贴息的期限，请给出原文依据。", ["期限"], "2025年贷款贴息"),
    ("planid A123", ["适用对象", "期限"], "适用对象 期限"),
    ("请提取为民服务的责任主体", ["责任主体"], "为民服务"),
])
def test_scoped_query_separates_document_identity_from_subject(question, dimensions, expected):
    assert extraction_query(question, dimensions, scoped=True) == expected
    assert extraction_query(question, dimensions, scoped=False) == question
