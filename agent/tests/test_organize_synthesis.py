"""S4/S5 mock tests; quotations and indexes are checked against the shown source."""

import json
from copy import deepcopy

import pytest

from guance_agent.business.organize.discovery import discover
from guance_agent.business.organize.prompt import build_messages
from guance_agent.business.organize.reading import EvidenceWindow, PolicyReading, Reading
from guance_agent.business.organize.scope import parse_scope
from guance_agent.business.organize.synthesis import (
    assemble,
    clean_topic,
    parse_and_validate,
    parse_output,
)
from guance_agent.verify import NUMBER

from .organize_fakes import MockServices, MultiDocKB, mock_context, mock_payload


async def prepare_mock(count=4, question="梳理十四五时期的科技创新政策"):
    task, ctx = mock_context(question, kb=MultiDocKB(count=count))
    scope = parse_scope(task, ctx)
    found = await discover(scope, MockServices(ctx))
    reading = Reading()
    for hit in ctx.kb.hits:
        passage = ctx.kb.get_chunk_source(hit.chunk_id)
        ctx.evidence.add_result(passage, "kb_read")
        reading.windows.append(EvidenceWindow(hit.planid, hit.chunk_id, passage))
        reading.policies.setdefault(hit.planid, PolicyReading("read_no_valid_points"))
        reading.policies[hit.planid].chunks_read += 1
    build_messages(scope, reading)
    return ctx, scope, found, reading, mock_payload(ctx.kb)


def validate(payload, reading, ctx, scope):
    return parse_and_validate(json.dumps(payload, ensure_ascii=False), reading, ctx, scope)


@pytest.mark.parametrize(("level", "field", "value"), [
    ("policy", "planid", "forged"), ("policy", "planid", []),
    ("point", "chunk_id", "forged"), ("point", "chunk_id", "P001-c0"),
    ("point", "anchors", ["原文中不存在的短语"]), ("point", "anchors", []),
    ("point", "anchors", "科技创新"), ("point", "anchors", [None, 1, "科"]),
    ("point", "text", "建设科技创新平台9999个"),
    ("point", "text", "建设科技创新平台20个并培养科研人才100名"),
    ("point", "text", "短"), ("point", "text", "长文本" * 40),
    ("point", "text", {}), ("point", "topic", "平台2026"),
    ("point", "topic", "平台２０２６"), ("point", "topic", "《创新平台》"),
    ("point", "topic", "创新平台建设科研发展规划实施方案"),
])
async def test_mock_invalid_point_dropped_without_poisoning_other_policies(level, field, value):
    ctx, scope, found, reading, payload = await prepare_mock()
    entry = payload["policies"][0]
    (entry if level == "policy" else entry["points"][0])[field] = value
    parsed = validate(payload, reading, ctx, scope)
    result = assemble(scope, found, reading, parsed)
    assert len(result.claims) == 3
    assert all(p.planid != "P000" for p in parsed.points)
    assert result.structured["coverage"]["documents_summarized"] == 3


async def test_mock_only_shown_content_can_be_used_and_half_sentence_is_skipped():
    ctx, scope, _, reading, payload = await prepare_mock(count=1)
    window = reading.windows[0]
    window.shown = "前面的残句。" + window.shown
    window.passage.char_start = 10
    window.passage.char_end = 10 + len(window.passage.content)
    # Direct quote location checks avoid inventing a different registered read range.
    from guance_agent.business.organize.synthesis import _locate_quote
    assert _locate_quote(window, "前面的残句", ["残句"]) is None
    assert _locate_quote(window, "建设科技创新平台20个", ["创新平台"]) == "建设科技创新平台20个。"
    window.shown = "建设科技创新平台20个。"
    point = payload["policies"][0]["points"][0]
    point.update(text="培养科研人才100名", anchors=["科研人才"])
    assert not validate(payload, reading, ctx, scope).points


async def test_mock_numeric_substring_and_duplicate_quote_rejected():
    ctx, scope, _, reading, payload = await prepare_mock(count=1)
    first = payload["policies"][0]["points"][0]
    first.update(text="培养科研人才10名", anchors=["科研人才"])
    assert not validate(payload, reading, ctx, scope).points  # source says 100, not 10
    first.update(text="培养科研人才100名")
    payload["policies"][0]["points"].append(deepcopy(first))
    assert len(validate(payload, reading, ctx, scope).points) == 1


@pytest.mark.parametrize(("label", "expected"), [
    (" 创新，平台 ", "创新平台"), ("A.I.平台", "AI平台"),
    ("创新一平台", "创新一平台"), ("创新²平台", None), ("单", None),
])
def test_mock_topic_cleaning(label, expected):
    assert clean_topic(label) == expected


async def test_mock_category_membership_and_irrelevance():
    ctx, scope, _, reading, payload = await prepare_mock(
        question="梳理科技创新，按平台建设和人才培养分类")
    payload["policies"][0]["points"][0]["topic"] = "平台建设"
    payload["policies"][1]["points"][0]["topic"] = "其他"
    payload["policies"][2]["relevance"] = "irrelevant"
    parsed = validate(payload, reading, ctx, scope)
    assert [p.planid for p in parsed.points] == ["P000", "P001"]


async def test_mock_limits_round_robin_and_at_most_eight_topics():
    ctx, scope, found, reading, payload = await prepare_mock(count=6)
    labels = ["平台建设", "人才培养", "资金支持", "成果转化", "产业升级", "国际合作",
              "科技金融", "基础研究", "知识产权", "产业生态", "科技服务", "其他"]
    for i, policy in enumerate(payload["policies"]):
        policy["relevance"] = "high" if i % 2 == 0 else "medium"
        policy["points"] = []
        for j in range(3):
            hit = ctx.kb.hits[i * 3 + j]
            policy["points"].append(dict(topic=labels[(i * 3 + j) % len(labels)],
                                          text=hit.content.split("。")[0], chunk_id=hit.chunk_id,
                                          anchors=["科技创新"]))
    parsed = validate(payload, reading, ctx, scope)
    assert len(parsed.points) == 16
    assert [p.planid for p in parsed.points[:6]] == ["P000", "P002", "P004", "P001", "P003", "P005"]
    assert all(len(p.claim.quotes) == 1 for p in parsed.points)
    result = assemble(scope, found, reading, parsed)
    assert len(result.structured["groups"]) == 8
    assert "其他" in [g["topic"] for g in result.structured["groups"]]
    for policy in result.structured["policies"]:
        assert len(policy["claim_indexes"]) <= 3
        assert policy["summary"] == "；".join(result.claims[i].text for i in policy["claim_indexes"])


@pytest.mark.parametrize("raw", [
    '{} {}', '{"policies":[]} {', '{"policies": [', '{"policies": {}}',
    '[{"policies":[]}]', '{"outer": {"policies":[]}', 'plain text',
])
def test_mock_multiple_or_incomplete_json_rejected(raw):
    with pytest.raises(ValueError):
        parse_output(raw)


def test_mock_think_and_fenced_json_allowed():
    assert parse_output('<think>{"noise": 1}</think>\n```json\n{"policies":[]}\n```') == {"policies": []}


@pytest.mark.parametrize("question", [
    "梳理十四五时期的科技创新政策", "梳理２０２６年科技创新政策", "梳理科技2026创新政策",
    "梳理相关政策" * 20 + "2026年",  # fallback topic truncation may split a numeric token
])
async def test_mock_answer_numbers_bound_and_no_titles_ids_or_counts(question):
    ctx, scope, found, reading, payload = await prepare_mock(question=question)
    result = assemble(scope, found, reading, validate(payload, reading, ctx, scope))
    allowed = set(NUMBER.findall(question)) | {n for c in result.claims for n in NUMBER.findall(c.text)}
    assert set(NUMBER.findall(result.answer)) <= allowed
    assert "模拟政策2020年" not in result.answer and "P000" not in result.answer
    assert all(not p["full_text_covered"] for p in result.structured["policies"])


async def test_mock_unknown_relevance_low_and_zero_claims_empty_outcome():
    ctx, scope, found, reading, payload = await prepare_mock(count=1)
    payload["policies"][0]["relevance"] = ["bad"]
    parsed = validate(payload, reading, ctx, scope)
    assert parsed.relevance == {"P000": "low"}
    result = assemble(scope, found, reading, parsed)
    assert result.insufficient and "不足" in result.structured["coverage"]["note"]
    payload["policies"][0]["relevance"] = "irrelevant"
    result = assemble(scope, found, reading, validate(payload, reading, ctx, scope))
    assert result.insufficient and result.answer == "" and result.structured == {} and result.claims == []


async def test_mock_unread_and_missing_targets_keep_null_analysis():
    ctx, scope, found, reading, payload = await prepare_mock()
    reading.policies["P003"] = PolicyReading()
    reading.windows = [w for w in reading.windows if w.planid != "P003"]
    found.missing_targets = ["MISSING"]
    result = assemble(scope, found, reading, validate(payload, reading, ctx, scope))
    unread, missing = result.structured["policies"][-2:]
    assert unread["read_status"] == "not_read"
    assert missing["read_status"] == "not_retrieved" and missing["title"] is None
    for policy in (unread, missing):
        assert policy["relevance"] is policy["summary"] is None
        assert policy["topics"] == policy["claim_indexes"] == []


async def test_mock_exact_quote_whitespace_and_list_marker_cleanup():
    ctx, scope, _, reading, payload = await prepare_mock(count=1)
    window = reading.windows[0]
    window.shown = "  " + window.shown
    point = payload["policies"][0]["points"][0]
    point["text"] = "1. " + point["text"]
    parsed = validate(payload, reading, ctx, scope)
    assert parsed.points[0].claim.quotes == ["  建设科技创新平台20个。"]
    assert not parsed.points[0].claim.text.startswith("1.")
