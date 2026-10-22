"""同源新旧对照：只比较实际批次，展示两篇抽检的全部父子范围与生成来源。

不把块数变化当检索效果提升。输入为冻结批次，输出写在新版批次内，不改旧结果。
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from policy_chunker.source import read_jsonl, write_json


def load(run):
    docs = {d["id"]: d for d in read_jsonl(run/"documents.jsonl")}
    grouped = {name: defaultdict(list) for name in ("parents", "chunks", "structures", "units")}
    for name, table in grouped.items():
        for row in read_jsonl(run/f"{name}.jsonl"):
            pid=docs[row["document_id"]]["planid"]
            if pid in ("34457","50143"):
                table[pid].append(row)
            elif name == "parents":
                table[pid].append(None)
            elif name == "chunks":
                # 全量对照只需数量、状态及来源类型；详细原文仅保留两篇，避免
                # 同时把新旧数百MB的段落、表格和检索文本全部解码常驻内存。
                table[pid].append({"status":row["status"],"generation":row.get("generation","legacy")})
    return {d["planid"]:d for d in docs.values()}, grouped


def compare(old_run, new_run):
    old_docs, old = load(old_run)
    new_docs, new = load(new_run)
    rows = []
    for planid, doc in new_docs.items():
        previous = old_docs[planid]
        if previous["source_hash"] != doc["source_hash"]:
            raise ValueError(f"cannot compare changed source: {planid}")
        rows.append({"planid":planid,"title":doc["metadata"].get("title"),
                     "source_hash_equal":True,"old_parents":len(old["parents"][planid]),
                     "new_parents":len(new["parents"][planid]),"old_chunks":len(old["chunks"][planid]),
                     "new_chunks":len(new["chunks"][planid]),
                     "new_pending":sum(c["status"]=="pending" for c in new["chunks"][planid])})
    checks = {}
    if "34457" in new_docs:
        sections = [s for s in new["structures"]["34457"] if s.get("basis") == "inline_consecutive_chinese_heading"]
        crossing = [c["id"] for c in new["chunks"]["34457"] if sum(s["start"] < c["source_segments"][-1]["end"] and c["source_segments"][0]["start"] < s["end"] for s in sections)>1]
        checks["34457"]={"inline_sections":len(sections),"cross_topic_chunks":crossing,"passed":len(sections)==5 and not crossing}
    if "50143" in new_docs:
        intro = next(u for u in new["units"]["50143"] if u["kind"]=="list_intro" and "存在的主要困难和问题" in u["text"])
        parent = next(p for p in new["parents"]["50143"] if intro["id"] in p["unit_ids"])
        children = [c for c in new["chunks"]["50143"] if c["parent_id"]==parent["id"]]
        retained = "五是安全生产应急救援" in parent["source_segments"][0]["text"]
        all_context = all(intro["text"] in c["retrieval_text"] for c in children)
        checks["50143"]={"intro_parent_id":parent["id"],"children":len(children),"five_items_same_parent":retained,"all_children_have_intro":all_context,"passed":bool(children) and retained and all_context}
    generation = Counter(c["generation"] for values in new["chunks"].values() for c in values)
    result = {"old_run":str(old_run),"new_run":str(new_run),"documents_compared":len(rows),
              "all_compared_sources_identical":True,"regressions":checks,
              "new_chunk_generation":dict(generation),"documents":rows,
              "retrieval_quality_evaluated":False}
    write_json(new_run/"comparison.json",result)
    lines=["# 新旧切块对照", "", f"相同来源版本对照：{len(rows)} 篇。所有来源 SHA-256 相同。",
           "", "旧版采用项目自写核心；新版实际调用 LlamaIndex，并记录候选与约束调整。块数变化不代表检索质量提升。",
           "", "## 两处抽检回归", ""]
    for pid, check in checks.items():
        lines.append(f"- {pid}：{'通过' if check['passed'] else '未通过'}；`{json.dumps(check,ensure_ascii=False)}`")
    lines += ["", "## 新版子块生成路径", "", "| 路径 | 数量 |", "|---|---:|"]
    lines += [f"| {key} | {value} |" for key,value in sorted(generation.items())]
    lines += ["", "component 为官方候选边界直接映射；preserved_* 为完整性约束下保留的范围，不冒充组件原始边界。table_row_group 为保护行组调用。",
              "", "## 每篇数量对照", "", "| planid | 原父块 | 新父块 | 原子块 | 新子块 | 新待处理 |", "|---|---:|---:|---:|---:|---:|"]
    lines += [f"| {r['planid']} | {r['old_parents']} | {r['new_parents']} | {r['old_chunks']} | {r['new_chunks']} | {r['new_pending']} |" for r in rows]
    (new_run/"新旧对照.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    detail=["# 两篇抽检文档的新版父子块", "", "本表展示全部父子关系；正文仅显示开头 160 字符，完整内容以 JSONL 和冻结原文为准。坐标为全文件 Unicode 码点，左闭右开；token 为完整检索输入。", ""]
    for pid in ("34457","50143"):
        if pid not in new_docs:
            continue
        doc = new_docs[pid]
        detail += [f"## {pid} · {doc['metadata']['title']}", "",f"[冻结原文]({doc['snapshot']})；原版 {len(old['parents'][pid])} 父/{len(old['chunks'][pid])} 子，新版 {len(new['parents'][pid])} 父/{len(new['chunks'][pid])} 子。", ""]
        for index,parent in enumerate(new["parents"][pid],1):
            detail += [f"### 父块 {index} · {' / '.join(parent['section_path']) or '无可靠标题'}", "",
                       f"`{parent['id']}`；原文 [{parent['start']}, {parent['end']})；生成来源 `{parent['generation']}`。", ""]
            for child in [c for c in new["chunks"][pid] if c["parent_id"]==parent["id"]]:
                seg=child["source_segments"][0]
                detail += [f"- 子块 `{child['id']}`：[{seg['start']}, {seg['end']})，{child['embedding_tokens']} token，{child['status']}，`{child['generation']}`；必要上下文 {len(child['context_segments'])} 段。",
                           "", "```text", seg["text"][:160], "```", ""]
    (new_run/"两篇抽检对照.md").write_text("\n".join(detail)+"\n",encoding="utf-8")
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new",required=True)
    parser.add_argument("--old")
    args=parser.parse_args()
    project=Path(__file__).resolve().parent
    old_project=project.parent/"切块工程"
    pointer=json.loads((old_project/"当前批次.json").read_text(encoding="utf-8"))
    result=compare(Path(args.old).resolve() if args.old else old_project/pointer["run"],Path(args.new).resolve())
    print(json.dumps({key:value for key,value in result.items() if key!="documents"},ensure_ascii=False))
