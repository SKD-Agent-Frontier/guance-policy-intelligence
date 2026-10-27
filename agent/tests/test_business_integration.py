import asyncio
import json
import time
from dataclasses import replace
from datetime import date

import pytest

from guance_agent import business
from guance_agent.business.contract import BusinessError, BusinessOutcome
from guance_agent.main import submit
from guance_agent.schemas import ChunkSpan, Hit, Passage

BODY = "该政策支持农业发展，安排资金100万元。"
QUOTE = "该政策支持农业发展，安排资金100万元。"


class FakeKB:
    name = "business-test"

    def __init__(self, *, empty=False, ambiguous=False, verify_delay=0):
        self.empty = empty
        self.ambiguous = ambiguous
        self.verify_delay = verify_delay
        self.read_calls = []
        self.search_calls = []
        self.documents = {
            "T001": {"title": "甲政策", "body": BODY},
            "T002": {"title": "乙政策", "body": BODY},
        }

    def _hit(self, planid):
        doc = self.documents[planid]
        return Hit(planid=planid, chunk_id=planid + "-chunk", title=doc["title"],
                   area="甲市", fyp="第十四个五年规划", admin_level="市",
                   plantype_label="专项规划", section_idx=0, section_path="主要任务",
                   content=doc["body"], char_start=0, char_end=len(doc["body"]),
                   chunk_char_start=0, chunk_char_end=len(doc["body"]),
                   section_char_start=0, section_char_end=len(doc["body"]),
                   score=1.0)

    def search(self, query, area, fyp, top_k, *, planids=None):
        self.search_calls.append((query, area, fyp, top_k, planids))
        if self.empty:
            return []
        ids = list(planids) if planids is not None else list(self.documents)
        return [self._hit(pid) for pid in ids if pid in self.documents][:top_k]

    def find_by_title(self, title, *, area=None, fyp=None):
        matches = [pid for pid, doc in self.documents.items() if title in doc["title"]]
        if self.ambiguous and matches:
            matches = ["T001", "T002"]
        return ([{"planid": pid} for pid in matches], len(matches))

    def read(self, planid, section_idx, max_chars, *, chunk_id=None, offset=0,
             document_id=None, generation_id=None):
        self.read_calls.append((planid, section_idx, chunk_id, offset))
        if planid not in self.documents:
            from guance_agent.kb import KBNotFound
            raise KBNotFound(planid)
        body = self.documents[planid]["body"]
        hit = self._hit(planid)
        return Passage(planid=planid, title=hit.title, area=hit.area, fyp=hit.fyp,
                       admin_level=hit.admin_level, plantype_label=hit.plantype_label,
                       section_idx=0, section_path=hit.section_path, content=body,
                       char_start=0, char_end=len(body), read_scope="chunk",
                       chunk_id=hit.chunk_id, chunk_char_start=0,
                       chunk_char_end=len(body), chunks=[ChunkSpan(
                           chunk_id=hit.chunk_id, char_start=0, char_end=len(body),
                           chunk_char_start=0, chunk_char_end=len(body), complete=True,
                           section_char_start=0, section_char_end=len(body),
                           section_path=hit.section_path)])

    def get_chunk_source(self, chunk_id, *, document_id=None, generation_id=None):
        planid = chunk_id.split("-", 1)[0]
        hit = self._hit(planid)
        return Passage(planid=planid, title=hit.title, area=hit.area, fyp=hit.fyp,
                       admin_level=hit.admin_level, plantype_label=hit.plantype_label,
                       section_idx=0, section_path=hit.section_path, content=hit.content,
                       char_start=0, char_end=len(hit.content), chunk_id=chunk_id,
                       chunk_char_start=0, chunk_char_end=len(hit.content))

    def verify_quote(self, chunk_id, quote, observed_ranges, *,
                     document_id=None, generation_id=None):
        time.sleep(self.verify_delay)
        return quote in self.get_chunk_source(chunk_id).content


class Module:
    def __init__(self, name="compare", *, claims=False, bad_quote=False, delay=0):
        self.name = name
        self.claims = claims
        self.bad_quote = bad_quote
        self.delay = delay
        self.calls = []
        self.statuses = []

    async def __call__(self, task, ctx, services):
        self.calls.append((list(task.targets or []), ctx.scope, task.question))
        if self.delay:
            await asyncio.sleep(self.delay)
        claim_list = []
        if self.claims:
            search = await services.search("农业发展")
            assert search.status == "ok"
            hit = search.data[0]
            read = await services.read(hit.planid, chunk_id=hit.chunk_id)
            self.statuses.append(read.status)
            claim_list = [dict(text="政策支持农业发展，资金100万元。",
                               chunk_ids=[hit.chunk_id],
                               quotes=["不存在于原文的句子。" if self.bad_quote else QUOTE])]
        return BusinessOutcome(answer="政策支持农业发展，资金100万元。",
                               claims=claim_list,
                               structured={"summary": "农业支持", "cells": [1]})


class OpenTopic(Module):
    requires_targets = False

    def __init__(self):
        super().__init__("organize")
        self.raw_targets = "unset"

    async def __call__(self, task, ctx, services):
        self.raw_targets = task.targets
        return await super().__call__(task, ctx, services)


@pytest.fixture
def setup(env, monkeypatch):
    from guance_agent import orchestrator, runtime

    settings = replace(env[0], llm_base_url="https://example.invalid/v1",
                       llm_api_key="offline-test-key")
    kb = FakeKB()
    monkeypatch.setattr(business, "_MODULES", {})
    monkeypatch.setattr(orchestrator, "get_kb", lambda _: kb)
    monkeypatch.setattr(runtime, "get_client", lambda _: object())
    return settings, kb


def register(module):
    business.register(module)
    return module


@pytest.mark.parametrize("structured_output", [False, True])
async def test_t01_registered_module_runs_once_with_complete_explicit_targets(
        setup, structured_output):
    settings, _ = setup
    settings = replace(settings, structured_output=structured_output)
    module = register(Module())
    result = await submit("请比较两份政策", settings=settings,
                          task_type="compare", targets=["T001", "T002"])
    assert result.status == "completed" and result.task_type == "compare"
    assert module.calls == [(["T001", "T002"], ("T001", "T002"), "请比较两份政策")]
    assert not result.claims and result.structured == {}
    assert "农业支持" not in result.answer


@pytest.mark.parametrize("task_type", [None, [], {}, "not-registered"])
async def test_t02_unknown_or_malformed_task_type_rejected_before_kb(setup, monkeypatch, task_type):
    settings, _ = setup
    from guance_agent import orchestrator

    def forbidden(_):
        pytest.fail("unknown task_type reached KB loading")

    monkeypatch.setattr(orchestrator, "get_kb", forbidden)
    result = await submit("比较政策", settings=settings, task_type=task_type,
                          targets=["T001", "T002"])
    assert result.status == "failed" and result.error.code == "INVALID_INPUT"


@pytest.mark.parametrize("targets", [[], [True], [1], [" "], ["x" * 65],
                                      ["T001", "T001"], [f"T{i:03}" for i in range(9)]])
async def test_targets_validation_rejects_invalid_values(setup, targets):
    settings, _ = setup
    result = await submit("比较政策", settings=settings, task_type="qa", targets=targets)
    assert result.status == "failed" and result.error.code == "INVALID_INPUT"


async def test_invalid_business_targets_preserve_task_type_at_entry(setup):
    settings, _ = setup
    module = register(Module("organize"))
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["BAD/ID"])
    assert result.status == "failed" and result.error.code == "INVALID_INPUT"
    assert result.task_type == "organize" and module.calls == []


async def test_registered_compare_requires_two_targets_but_unregistered_does_not(setup):
    settings, _ = setup
    one = await submit("比较政策", settings=settings, task_type="compare", targets=["T001"])
    assert one.error.code == "INVALID_INPUT"
    other = await submit("比较政策", settings=settings, task_type="other", targets=["T001"])
    assert other.error.code == "INVALID_INPUT" and "不支持" in other.answer


async def test_compare_without_targets_still_prompts_for_two_objects(setup):
    settings, _ = setup
    module = register(Module("compare"))
    result = await submit("比较农业科技创新政策", settings=settings, task_type="compare")
    assert result.status == "completed" and result.error is None
    assert "至少需要两个" in result.answer
    assert result.route["targets"] is None
    assert module.calls == []


async def test_explicit_targets_override_other_ids_and_query_length_for_business(setup):
    settings, _ = setup
    module = register(Module())
    question = "planid WRONG " + "需要分析" * 35
    result = await submit(question, settings=settings, task_type="compare",
                          targets=["T001", "T002"])
    assert result.status == "completed"
    assert module.calls[0][0:2] == (["T001", "T002"], ("T001", "T002"))


async def test_qa_explicit_targets_keep_existing_long_query_block(setup, monkeypatch):
    settings, _ = setup
    from guance_agent import orchestrator

    async def forbidden(*args, **kwargs):
        pytest.fail("blocked QA request reached execution")

    monkeypatch.setattr(orchestrator, "_execute_once", forbidden)
    result = await submit("农业" * 110, settings=settings, task_type="qa",
                          targets=["T001"])
    assert result.status == "completed" and result.error is None
    assert result.evidence_status == "none"


async def test_existing_qa_path_uses_explicit_targets_over_question_ids(setup, monkeypatch):
    settings, _ = setup
    from guance_agent import orchestrator
    from guance_agent.runner import ExecOutcome

    seen = {}

    async def no_evidence(task, s, ctx, rt, deadline):
        seen["scope"] = ctx.scope
        return ExecOutcome(json.dumps({"answer": "暂无证据", "claims": [],
                                       "insufficient": True}, ensure_ascii=False), None)

    monkeypatch.setattr(orchestrator, "_execute_once", no_evidence)
    result = await submit("planid T002 农业", settings=settings, task_type="qa",
                          targets=["T001"])
    assert result.status == "completed" and seen["scope"] == ("T001",)


async def test_no_targets_resolves_all_titles_before_single_business_call(setup):
    settings, _ = setup
    module = register(Module())
    result = await submit("比较《甲政策》和《乙政策》", settings=settings,
                          task_type="compare")
    assert result.status == "completed"
    assert module.calls[0][0:2] == (["T001", "T002"], ("T001", "T002"))


@pytest.mark.parametrize("question", [
    "比较 planid T001 与 planid T002 在北京市和上海市的农业政策",
    "比较 planid T001 与 planid T002 在十四五和十五五期间的农业政策",
])
async def test_natural_planids_keep_complete_scope_with_multiple_filters(setup, question):
    settings, _ = setup
    module = register(Module("compare"))
    result = await submit(question, settings=settings, task_type="compare")
    assert result.status == "completed"
    assert module.calls == [(["T001", "T002"], ("T001", "T002"), question)]


@pytest.mark.parametrize("structured_output", [False, True])
async def test_open_or_unresolved_business_request_prompts_without_execution(
        setup, structured_output):
    settings, _ = setup
    settings = replace(settings, structured_output=structured_output)
    module = register(Module("organize"))
    result = await submit("请分析政策", settings=settings, task_type="organize")
    assert result.status == "completed" and result.error is None
    assert "planid" in result.answer and module.calls == []
    missing = await submit("分析《不存在政策》", settings=settings, task_type="organize")
    assert missing.status == "completed" and "唯一定位" in missing.answer
    assert module.calls == []


async def test_open_topic_module_executes_once_with_none_scope_and_recognized_filters(setup):
    settings, _ = setup
    module = register(OpenTopic())
    result = await submit("北京市第十四个五年规划科技创新", settings=settings,
                          task_type="organize")
    assert result.status == "completed" and result.error is None
    assert module.calls == [([], None, "北京市第十四个五年规划科技创新")]
    assert module.raw_targets is None and result.route["targets"] is None
    assert result.route["kind"] == "open"
    assert result.route["areas"] == ["北京市"]
    assert result.route["fyps"] == ["第十四个五年规划"]
    assert result.structured == {} and "科技创新" not in result.answer


async def test_open_topic_search_returns_only_real_documents_in_recognized_scope(setup, monkeypatch):
    settings, _ = setup
    from guance_agent import orchestrator

    class FilteredKB(FakeKB):
        def _hit(self, planid):
            hit = super()._hit(planid)
            if planid == "T001":
                return hit.model_copy(update={"area": "北京市", "fyp": "第十四个五年规划"})
            if planid == "T002":
                return hit.model_copy(update={"area": "上海市", "fyp": "第十五个五年规划"})
            return hit

        def search(self, query, area, fyp, top_k, *, planids=None):
            hits = super().search(query, area, fyp, top_k, planids=planids)
            return [hit for hit in hits if (area is None or hit.area == area)
                    and (fyp is None or hit.fyp == fyp)]

    kb = FilteredKB()
    monkeypatch.setattr(orchestrator, "get_kb", lambda _: kb)

    class SearchOpenTopic(OpenTopic):
        async def __call__(self, task, ctx, services):
            self.raw_targets = task.targets
            self.scope = ctx.scope
            found = await services.search("科技创新")
            self.search_status = found.status
            self.hits = found.data
            return BusinessOutcome(answer="北京市相关政策已全面覆盖。", claims=[],
                                   structured={"summary": "全部事实均已核实",
                                               "documents": [{"planid": "FAKE"}]})

    module = register(SearchOpenTopic())
    question = "梳理十四五时期北京市科技创新领域的相关政策。"
    result = await submit(question, settings=settings, task_type="organize")
    assert result.status == "completed" and result.error is None
    assert module.raw_targets is None and module.scope is None
    assert module.search_status == "ok"
    assert [hit.planid for hit in module.hits] == ["T001"]
    assert kb.search_calls == [("科技创新", "北京市", "第十四个五年规划",
                                settings.top_k_default, None)]
    assert result.structured == {
        "documents": [{"planid": "T001", "title": "甲政策", "area": "北京市",
                        "fyp": "第十四个五年规划", "admin_level": "市",
                        "plantype_label": "专项规划", "chunk_ids": ["T001-chunk"]}],
        "verification": {"policy_facts": "unverified", "reason": "no_valid_claims"},
    }
    assert not result.claims and not result.evidence_refs
    assert "全面覆盖" not in result.answer and "FAKE" not in str(result.structured)


async def test_open_topic_long_question_bypasses_route_query_limit(setup):
    settings, _ = setup
    module = register(OpenTopic())
    question = "北京市第十四个五年规划科技创新" + "有关背景和目标" * 35
    result = await submit(question, settings=settings, task_type="organize")
    assert result.status == "completed" and result.error is None
    assert len(module.calls) == 1 and module.calls[0][0:2] == ([], None)


@pytest.mark.parametrize("question", [
    "北京市和上海市十四五科技创新",
    "十四五和十五五科技创新",
])
async def test_open_topic_multiple_single_value_filters_require_narrowing(setup, question):
    settings, _ = setup
    module = register(OpenTopic())
    result = await submit(question, settings=settings, task_type="organize")
    assert result.status == "completed" and "不能同时限定多个" in result.answer
    assert module.calls == []


async def test_compare_open_filters_prompts_for_targets_and_narrower_scope(setup):
    settings, _ = setup
    module = register(Module("compare"))
    result = await submit("比较北京市和上海市十四五的科技创新政策",
                          settings=settings, task_type="compare")
    assert result.status == "completed"
    assert "至少需要两个" in result.answer
    assert result.route["targets"] is None and module.calls == []


async def test_opted_out_module_does_not_fall_back_from_ambiguous_explicit_title(setup):
    settings, kb = setup
    kb.ambiguous = True
    module = register(OpenTopic())
    result = await submit("分析《甲政策》并讨论农业", settings=settings,
                          task_type="organize")
    assert result.status == "completed" and "消除歧义" in result.answer
    assert module.calls == []


async def test_ambiguous_or_partially_unresolved_titles_never_execute_subset(setup, monkeypatch):
    settings, kb = setup

    kb.ambiguous = True
    module = register(Module("organize"))
    ambiguous = await submit("分析《甲政策》", settings=settings, task_type="organize")
    assert ambiguous.status == "completed" and "消除歧义" in ambiguous.answer
    assert module.calls == []

    kb.ambiguous = False
    def partly_known(title, *, area=None, fyp=None):
        if title == "甲政策":
            return ([{"planid": "T001"}], 1)
        return ([], 0)

    monkeypatch.setattr(kb, "find_by_title", partly_known)
    partial = await submit("比较《甲政策》和《未知政策》", settings=settings,
                           task_type="organize")
    assert partial.status == "completed" and "无法唯一定位" in partial.answer
    assert module.calls == []


async def test_natural_language_target_overflow_is_rejected_before_module_call(setup):
    settings, _ = setup
    module = register(Module("organize"))
    question = "比较 " + " ".join(f"planid T{i:03}" for i in range(9))
    result = await submit(question, settings=settings, task_type="organize")
    assert result.status == "failed" and result.error.code == "INVALID_INPUT"
    assert "最多支持 8 个" in result.answer
    assert module.calls == []


async def test_no_claim_answer_and_module_documents_are_discarded_without_search(setup):
    settings, _ = setup

    class NoEvidence(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            return BusinessOutcome(
                answer="已全面覆盖农业政策，所有对象均符合。", claims=[],
                structured={"documents": [{"planid": "FAKE"}],
                            "coverage": "complete", "summary": "全量事实"})

    register(NoEvidence())
    result = await submit("分析农业政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert not result.claims and not result.evidence_refs
    assert result.structured == {}
    assert "已全面覆盖" not in result.answer and "FAKE" not in str(result.structured)
    assert "未经核验" in " ".join(result.warnings)


async def test_no_claim_result_keeps_only_documents_from_successful_search(setup):
    settings, kb = setup

    class SearchOnly(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            found = await services.search("农业")
            assert found.status == "ok"
            return BusinessOutcome(answer="农业全覆盖且已核实。", claims=[],
                                   structured={"documents": [{"planid": "FAKE"}],
                                               "coverage": "complete"})

    register(SearchOnly())
    result = await submit("分析农业政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.structured["verification"] == {
        "policy_facts": "unverified", "reason": "no_valid_claims"}
    assert {doc["planid"] for doc in result.structured["documents"]} == {"T001"}
    assert result.structured["documents"][0]["chunk_ids"] == ["T001-chunk"]
    assert result.structured["documents"][0]["title"] == "甲政策"
    assert set(result.structured) == {"documents", "verification"}
    assert not result.claims and not result.evidence_refs
    assert "农业全覆盖" not in result.answer and "FAKE" not in str(result.structured)
    assert kb.search_calls


@pytest.mark.parametrize("structured", [
    {"nested": {1: "non-string key"}},
    {"value": float("nan")},
    {"value": {"invalid"}},
    {"value": object()},
    {"value": date(2026, 10, 27)},
])
async def test_non_json_structured_result_fails_with_output_parse(setup, structured):
    settings, _ = setup

    class InvalidStructured(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            return BusinessOutcome(answer="摘要", claims=[], structured=structured)

    register(InvalidStructured())
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.status == "failed" and result.error.code == "OUTPUT_PARSE"
    assert result.task_type == "organize" and result.route["targets"] == ["T001"]
    assert result.structured == {}


async def test_structured_tuples_are_normalized_to_json_lists(setup):
    settings, _ = setup

    class TupleStructured(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            found = await services.search("农业")
            hit = found.data[0]
            await services.read(hit.planid, chunk_id=hit.chunk_id)
            return BusinessOutcome(answer="政策支持农业发展，资金100万元。",
                                   claims=[{"text": "政策支持农业发展，资金100万元。",
                                            "chunk_ids": [hit.chunk_id], "quotes": [QUOTE]}],
                                   structured={"rows": (1, 2)})

    register(TupleStructured())
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.claims and result.evidence_refs
    assert result.structured == {"rows": [1, 2]}


async def test_circular_structured_result_fails_with_output_parse(setup):
    settings, _ = setup

    class CircularStructured(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            outcome = BusinessOutcome(answer="摘要", claims=[])
            circular = {}
            circular["self"] = circular
            object.__setattr__(outcome, "structured", circular)
            return outcome

    register(CircularStructured())
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.status == "failed" and result.error.code == "OUTPUT_PARSE"
    assert result.task_type == "organize" and result.structured == {}


async def test_invalid_structured_output_failure_preserves_business_telemetry(setup, monkeypatch):
    settings, _ = setup
    from guance_agent.business.services import BusinessServices

    async def call_model(services, messages, max_tokens=None):
        services.telemetry.update(model_calls=1, input_tokens=3, output_tokens=2)
        services.telemetry["per_call"].append({"status": "ok"})
        return "model answer"

    monkeypatch.setattr(BusinessServices, "call_model", call_model)

    class InvalidAfterModel(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            await services.call_model([{"role": "user", "content": "analyze"}])
            return BusinessOutcome(answer="摘要", claims=[], structured={"bad": object()})

    register(InvalidAfterModel())
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.status == "failed" and result.error.code == "OUTPUT_PARSE"
    assert result.task_type == "organize" and result.route["targets"] == ["T001"]
    assert result.meta["business_telemetry"]["model_calls"] == 1
    assert result.meta["business_telemetry"]["per_call"] == [{"status": "ok"}]


async def test_t03_qa_uses_existing_agent_path(setup, monkeypatch):
    settings, _ = setup
    from guance_agent import orchestrator

    from .fake_model import FakeModel, answer, tool

    model = FakeModel([[tool("kb_search", query="农业", top_k=1)],
                       [tool("kb_read", planid="T001", section_idx=0)],
                       answer("政策支持农业发展",
                              [{"text": "政策支持农业发展",
                                "chunk_ids": ["T001-chunk"]}])])
    monkeypatch.setattr(orchestrator, "chat_model", lambda s, client: model)
    result = await submit("planid T001 农业", settings=settings)
    assert result.status == "completed" and result.task_type == "qa"
    assert result.claims and result.evidence_status == "verified"
    assert result.claims[0].quotes == []
    assert len(model.inputs) == 3


async def test_t04_extract_keeps_existing_path_and_structured_shape(setup, monkeypatch):
    settings, _ = setup
    from guance_agent import orchestrator
    from guance_agent.runner import ExecOutcome

    seen = {}

    async def extract(task, s, ctx, rt, builder):
        seen["scope"] = ctx.scope
        seen["dimensions"] = task.dimensions
        return ExecOutcome(json.dumps({"answer": "没有足够要素", "claims": [],
                                       "insufficient": True}, ensure_ascii=False), None)

    monkeypatch.setattr(orchestrator, "_run_oneshot", extract)
    result = await submit("planid T001 农业", settings=settings, task_type="extract",
                          dimensions=["适用对象", "期限"])
    assert result.status == "completed" and result.task_type == "extract"
    assert seen == {"scope": ("T001",), "dimensions": ["适用对象", "期限"]}
    assert result.structured["dimensions"] == ["适用对象", "期限"]
    assert set(result.structured) == {"dimensions", "documents", "elements", "missing"}


async def test_extract_anchors_are_backfilled_before_structured_attach_and_use_extract_budget(
        setup, monkeypatch):
    settings, kb = setup
    from guance_agent import orchestrator
    from guance_agent.runner import ExecOutcome
    from guance_agent.verify import quote_budget
    from guance_agent.verify import verify_quotes as real_verify_quotes

    seen = {}

    async def extract(task, s, ctx, rt, builder):
        ctx.evidence.add_result(kb.search("农业", None, None, 1, planids=ctx.scope), "kb_search")
        return ExecOutcome(json.dumps({"answer": "农业资金100万元",
                                       "claims": [{"dimension": "量化目标",
                                                   "text": "安排资金100万元",
                                                   "chunk_ids": ["T001-chunk"],
                                                   "anchors": ["该政策支持农业发展"]}],
                                       "insufficient": False}, ensure_ascii=False), None)

    async def verify(result, ctx):
        seen["task_type"] = result.task_type
        seen["quote_budget"] = quote_budget(result)
        return await real_verify_quotes(result, ctx)

    monkeypatch.setattr(orchestrator, "_run_oneshot", extract)
    monkeypatch.setattr(orchestrator, "verify_quotes", verify)
    result = await submit("planid T001 农业", settings=settings, task_type="extract",
                          dimensions=["量化目标"])
    assert seen == {"task_type": "extract", "quote_budget": 120}
    assert result.claims[0].quotes == [QUOTE]
    assert result.structured["elements"][0]["quotes"] == [QUOTE]


async def test_business_claim_without_quotes_or_anchors_is_preserved(setup):
    settings, _ = setup

    class Unquoted(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            found = await services.search("农业")
            hit = found.data[0]
            await services.read(hit.planid, chunk_id=hit.chunk_id)
            return BusinessOutcome(answer="农业得到支持", claims=[{
                "text": "农业得到支持", "chunk_ids": [hit.chunk_id]}],
                structured={"claim_count": 1})

    register(Unquoted())
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.claims and result.claims[0].quotes == []
    assert result.structured == {"claim_count": 1}


async def test_t06_search_and_read_use_tool_chain_and_register_evidence(setup):
    settings, kb = setup
    module = register(Module(claims=True))
    result = await submit("分析政策", settings=settings, task_type="compare",
                          targets=["T001", "T002"])
    assert module.statuses == ["ok"]
    assert len(kb.search_calls) == 1 and kb.search_calls[0][-1] == ("T001", "T002")
    assert len(kb.read_calls) == 1
    assert result.claims and result.evidence_refs
    assert result.evidence_status == "verified"


async def test_t07_business_read_cannot_escape_target_scope(setup):
    settings, kb = setup

    class Escape(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            outside = await services.read("OUTSIDE", section_idx=0)
            assert outside.status == "invalid_argument"
            return BusinessOutcome(answer="无结论", insufficient=True)

    register(Escape())
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.status == "completed" and result.error is None
    assert any(call["status"] == "invalid_argument" for call in result.meta["tool_calls"])
    assert not kb.read_calls


@pytest.mark.parametrize("structured_output", [False, True])
async def test_t08_valid_claim_is_verified_and_structured_is_preserved(
        setup, structured_output):
    settings, _ = setup
    settings = replace(settings, structured_output=structured_output)
    register(Module("organize", claims=True))
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.claims and result.claims[0].quotes == [QUOTE]
    assert result.evidence_status == "verified"
    assert result.structured["summary"] == "农业支持"


async def test_t09_t10_unverified_claim_is_removed_and_structured_cleared(setup):
    settings, _ = setup
    register(Module("organize", claims=True, bad_quote=True))
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert not result.claims and result.structured == {} and not result.evidence_refs
    assert "answer_rebuilt" in result.meta
    assert "政策支持农业发展，资金100万元。" not in result.answer
    assert "100万元" not in result.answer


async def test_build_result_drops_unregistered_claim_and_clears_mixed_structured(setup):
    settings, _ = setup

    class Mixed(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            found = await services.search("农业")
            hit = found.data[0]
            await services.read(hit.planid, chunk_id=hit.chunk_id)
            return BusinessOutcome(answer="未登记结论与有效结论都在原答复",
                                   claims=[{"text": "未登记的100万元", "chunk_ids": ["forged"]},
                                           {"text": "农业得到支持", "chunk_ids": [hit.chunk_id]}],
                                   structured={"rows": ["未登记的100万元", "农业得到支持"]})

    register(Mixed())
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert len(result.claims) == 1 and result.claims[0].text == "农业得到支持"
    assert result.structured == {} and len(result.evidence_refs) == 1
    assert "未登记的100万元" not in result.answer and "未登记结论与有效结论都在原答复" not in result.answer


async def test_t11_business_error_maps_to_existing_code(setup):
    settings, _ = setup

    class Failing(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            raise BusinessError("MODEL_UNAVAILABLE")

    register(Failing())
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.status == "failed" and result.error.code == "MODEL_UNAVAILABLE"
    assert result.task_type == "organize" and result.structured == {}
    assert result.route["targets"] == ["T001"]


async def test_unexpected_business_runtime_error_maps_to_internal(setup):
    settings, _ = setup

    class Broken(Module):
        def __init__(self):
            super().__init__("organize")

        async def __call__(self, task, ctx, services):
            raise RuntimeError("unexpected business bug")

    register(Broken())
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.status == "failed" and result.error.code == "INTERNAL"
    assert result.task_type == "organize" and result.structured == {}
    assert result.route["targets"] == ["T001"]


async def test_t12_business_timeout_is_limit_reached(setup):
    settings, _ = setup
    settings = replace(settings, run_timeout=0.02)
    register(Module("organize", delay=0.2))
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.status == "limit_reached" and result.error.code == "RUN_TIMEOUT"
    assert result.task_type == "organize" and result.route["targets"] == ["T001"]
    assert result.structured == {}


async def test_business_verification_timeout_discards_unverified_output(setup):
    settings, kb = setup
    settings = replace(settings, run_timeout=0.2)
    kb.verify_delay = 0.4
    register(Module("organize", claims=True))
    result = await submit("分析政策", settings=settings, task_type="organize",
                          targets=["T001"])
    assert result.status == "limit_reached" and result.error.code == "RUN_TIMEOUT"
    assert result.task_type == "organize" and result.structured == {}
    assert not result.claims and not result.evidence_refs


@pytest.mark.parametrize("question,ambiguous,empty,expected_status,expected_error", [
    ("甲" * 201, False, False, "completed", None),
    ("比较《甲政策》", True, False, "completed", None),
    ("planid T001 农业", False, True, "completed", None),
])
async def test_t13_existing_blocked_ambiguous_no_evidence_semantics(
        setup, monkeypatch, question, ambiguous, empty, expected_status, expected_error):
    settings, kb = setup
    kb.ambiguous, kb.empty = ambiguous, empty
    if empty:
        from guance_agent import orchestrator
        from guance_agent.runner import ExecOutcome

        async def no_evidence(*args, **kwargs):
            return ExecOutcome(json.dumps({"answer": "暂无证据", "claims": [],
                                           "insufficient": True}, ensure_ascii=False), None)

        monkeypatch.setattr(orchestrator, "_execute_once", no_evidence)
    result = await submit(question, settings=settings)
    assert result.status == expected_status
    assert (result.error.code if result.error else None) == expected_error
    assert result.task_type == "qa"


@pytest.mark.parametrize("code,status", [("MODEL_UNAVAILABLE", "failed"),
                                           ("RUN_TIMEOUT", "limit_reached"),
                                           ("TOOL_BUDGET", "limit_reached")])
async def test_t13_all_failed_objects_preserve_failure_status(setup, monkeypatch, code, status):
    settings, _ = setup
    from guance_agent import orchestrator
    from guance_agent.output import to_markdown
    from guance_agent.runner import ExecOutcome

    async def fail(*args, **kwargs):
        return ExecOutcome(None, code)

    monkeypatch.setattr(orchestrator, "_execute_once", fail)
    result = await submit("planid T001 农业", settings=settings)
    assert result.status == status and result.error.code == code
    assert result.evidence_status == "none"
    assert to_markdown(result) == result.answer and "系统内部错误" not in to_markdown(result)


async def test_t13_no_budget_is_limit_not_insufficient(setup, monkeypatch):
    settings, _ = setup
    from guance_agent import orchestrator
    from guance_agent.runner import ExecOutcome

    async def no_budget(*args, **kwargs):
        return ExecOutcome(None, "RUN_TIMEOUT")

    monkeypatch.setattr(orchestrator, "_execute_once", no_budget)
    settings = replace(settings, run_timeout=0.01)
    result = await submit("planid T001 农业", settings=settings)
    assert result.status == "limit_reached" and result.error.code == "RUN_TIMEOUT"


async def test_t14_business_without_claims_discards_structured_facts(setup):
    settings, _ = setup
    register(Module())
    result = await submit("比较政策", settings=settings, task_type="compare",
                          targets=["T001", "T002"])
    assert result.task_type == "compare"
    assert result.structured == {} and not result.claims
    assert "农业支持" not in result.answer
