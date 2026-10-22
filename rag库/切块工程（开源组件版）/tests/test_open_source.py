"""新版独有的回归：真实组件调用、两处抽检缺陷、来源证明的反向篡改。"""
from copy import deepcopy
from pathlib import Path
import json
from llama_index.core.node_parser import HierarchicalNodeParser
from policy_chunker.pipeline import process
from policy_chunker.source import Source, stable_id
from policy_chunker.tokens import TokenCounter
from policy_chunker.validation import check_document
from test_chunker import source, counter, run, CONFIG, PROJECT


def test_actual_upstream_api_is_called(tmp_path, counter, monkeypatch):
    original = HierarchicalNodeParser.get_nodes_from_documents
    calls = []
    def spy(self, documents, **kwargs):
        calls.append([d.id_ for d in documents])
        return original(self, documents, **kwargs)
    monkeypatch.setattr(HierarchicalNodeParser, "get_nodes_from_documents", spy)
    s, data, config = run(tmp_path, "# 总则\n\n政策原文逐字保留。\n", counter)
    assert calls and data["component_nodes"] and data["component_calls"]
    assert all(c["component_node_ids"] for c in data["chunks"])
    assert any(n["parent_node_id"] for n in data["component_nodes"])
    assert not check_document(s, data, counter, config)


def test_inline_five_sections_are_not_one_cross_topic_chunk(tmp_path, counter):
    body = "规划正文 一、乡村振兴 推进乡村建设。 二、农村改革 推进土地改革。 三、生态建设 加强绿化。 四、生态经济 支持旅游。 五、文化建设 加强宣传。\n"
    s, data, config = run(tmp_path, body, counter)
    inline = [x for x in data["structures"] if x.get("basis") == "inline_consecutive_chinese_heading"]
    assert len(inline) == 5
    assert all(len([sec for sec in inline if sec["start"] < c["source_segments"][-1]["end"] and c["source_segments"][0]["start"] < sec["end"]]) <= 1 for c in data["chunks"])
    assert "".join(u["text"] for u in data["units"]) == s.text
    assert not check_document(s, data, counter, config)


def test_shi_list_keeps_intro_and_no_intro_only_evidence(tmp_path, counter):
    body = "# 现状\n\n（三）存在以下主要问题：\n\n一是事故较多，需要治理。\n\n二是投入不足，需要保障。\n\n三是队伍不足，需要培训。\n"
    s, data, config = run(tmp_path, body, counter, child_target_tokens=100)
    intros = [u for u in data["units"] if u["kind"] == "list_intro"]
    assert len(intros) == 1
    parent = next(p for p in data["parents"] if intros[0]["id"] in p["unit_ids"])
    assert len([u for u in data["units"] if u["id"] in parent["unit_ids"] and u["kind"] == "list_item"]) == 3
    children = [c for c in data["chunks"] if c["parent_id"] == parent["id"]]
    assert children and all("存在以下主要问题" in c["retrieval_text"] for c in children)
    assert all(any(word in "".join(seg["text"] for seg in c["source_segments"]) for word in ("一是", "二是", "三是")) for c in children)
    assert not check_document(s, data, counter, config)


def test_component_trace_tampering_is_rejected(tmp_path, counter):
    s, data, config = run(tmp_path, "真实原文，支持企业创新。\n", counter)
    damaged = deepcopy(data)
    damaged["component_nodes"][0]["text"] += "伪造"
    assert "component_raw_text_mismatch" in check_document(s, damaged, counter, config)
    damaged = deepcopy(data)
    damaged["chunks"][0]["component_node_ids"] = []
    assert "ready_chunk_without_component_evidence" in check_document(s, damaged, counter, config)


def test_real_audit_documents_regression(counter):
    library = PROJECT.parents[1] / "语料库" / "Agent标准引用库"
    for planid in ("34457", "50143"):
        s = Source.load(library/"corpus"/planid/f"{planid}.md")
        data = process(s, library, counter, CONFIG, stable_id("test", CONFIG))
        assert not check_document(s, data, counter, CONFIG)
        if planid == "34457":
            assert len([x for x in data["structures"] if x.get("basis")]) == 5
            assert len(data["parents"]) == 5
            assert any(u["kind"] == "document_title" and u["disposition"] == "excluded" for u in data["units"])
        else:
            intro = next(u for u in data["units"] if u["kind"] == "list_intro" and "存在的主要困难和问题" in u["text"])
            parent = next(p for p in data["parents"] if intro["id"] in p["unit_ids"])
            assert "五是安全生产应急救援" in parent["source_segments"][0]["text"]
            assert all("存在的主要困难和问题" in c["retrieval_text"] for c in data["chunks"] if c["parent_id"] == parent["id"])


def test_list_note_is_kept_and_closes_its_scope(tmp_path, counter):
    body = "1. 甲类项目。"+"支持科技创新。"*40+"\n\n注：以上数字为累计值。\n\n2. 另一组项目。\n"
    s, data, config = run(tmp_path,body,counter,child_target_tokens=100)
    notes=[u for u in data["units"] if u["kind"]=="note"]
    assert len(notes)==1 and notes[0]["disposition"]=="represented"
    first=next(p for p in data["parents"] if notes[0]["id"] in p["unit_ids"])
    assert all("以上数字为累计值" in c["retrieval_text"] for c in data["chunks"] if c["parent_id"]==first["id"])
    later=[c for c in data["chunks"] if c["parent_id"]!=first["id"]]
    assert later and all("以上数字为累计值" not in c["retrieval_text"] for c in later)
    assert not check_document(s,data,counter,config)


def test_unresolved_nested_intro_is_an_explicit_pending_chunk(tmp_path,counter):
    body="主要目标：\n\n1. 第一组任务如下：\n\n一是支持创新。\n\n二是完善设施。\n"
    s,data,config=run(tmp_path,body,counter)
    pending=[c for c in data["chunks"] if "unresolved_list_scope" in c["reasons"]]
    assert pending and all(c["status"]=="pending" for c in pending)
    assert any("主要目标" in seg["text"] for c in pending for seg in c["source_segments"])
    assert not check_document(s,data,counter,config)
