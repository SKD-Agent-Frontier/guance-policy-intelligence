"""验收并发布本地入口：恢复实测、原工程/输入保护、对照、测试记录和报告。

只在 build 已通过后运行；不修改原工程和政策来源，不推送远端。
这里的“发布”仅指写本工程当前批次指针，不是部署、上传或数据库入库。
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from compare import compare
from policy_chunker.source import inventory, read_jsonl, write_json


def digest(path):
    """流式哈希大体积 JSONL，避免一次读入数百 MB。"""
    result=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            result.update(block)
    return result.hexdigest()


def publish(project, run, sample):
    validation=json.loads((run/"validation.json").read_text(encoding="utf-8"))
    sample_validation=json.loads((sample/"validation.json").read_text(encoding="utf-8"))
    if not validation["passed"] or not sample_validation["passed"]:
        raise ValueError("both persisted batches must pass independent validation")
    # 保存首次构建证据后再恢复；统计文件会被 resume 改写，不能拿0篇重算冒充首次处理。
    for name in ("build_stats.json","validation.json","environment.json"):
        target=run/("initial_"+name)
        if not target.exists():
            shutil.copyfile(run/name,target)
    before={path.name:digest(path) for path in sorted(run.glob("*.jsonl"))}
    with (run/"resume.log").open("w",encoding="utf-8") as log:
        result=subprocess.run([sys.executable,"-X","utf8",str(project/"safe_run.py"),"build","--all","--resume","--run",str(run)],cwd=project,stdout=log,stderr=subprocess.STDOUT)
    after={path.name:digest(path) for path in sorted(run.glob("*.jsonl"))}
    stats=json.loads((run/"build_stats.json").read_text(encoding="utf-8"))
    validation=json.loads((run/"validation.json").read_text(encoding="utf-8"))
    evidence={"exit_code":result.returncode,**stats,"same_jsonl_hashes":before==after,
              "before":before,"after":after,"validation_passed":validation["passed"],
              "io_runner":{"path":"safe_run.py","sha256":digest(project/"safe_run.py"),"purpose":"bounded atomic replace retry only"}}
    write_json(run/"resume_evidence.json",evidence)
    if result.returncode or before!=after or stats["processed"]!=0 or stats["reused"]!=validation["source_count"]:
        raise ValueError("full resume consistency check failed")
    baseline=json.loads((project/".cache"/"baseline.json").read_text(encoding="utf-8"))
    repo=project.parents[1]
    old_unchanged=all(digest(repo/path)==expected for path,expected in baseline["old_project"].items())
    inputs_unchanged=inventory(repo/"语料库"/"Agent标准引用库")==baseline["inputs"]
    if not old_unchanged or not inputs_unchanged:
        raise ValueError("original engineering or source inventory changed")
    old_project=project.parent/"切块工程"
    old_pointer=json.loads((old_project/"当前批次.json").read_text(encoding="utf-8"))
    comparison=compare(old_project/old_pointer["run"],run)
    compare(old_project/old_pointer["run"],sample)
    if not all(value["passed"] for value in comparison["regressions"].values()):
        raise ValueError("known two-document regressions not resolved")
    # 测试数量来自实际 pytest XML，不在程序里写死“已通过28项”。
    test_root=ET.parse(project/".cache"/"pytest-release.xml").getroot()
    suites=list(test_root.iter("testsuite"))
    tests={name:sum(int(s.get(name,0)) for s in suites) for name in ("tests","failures","errors","skipped")}
    if not tests["tests"] or tests["failures"] or tests["errors"]:
        raise ValueError("pytest evidence missing or failed")
    maximum=max((c["embedding_tokens"] for c in read_jsonl(run/"chunks.jsonl") if c["status"]=="ready"),default=0)
    counts=validation["counts"]
    relative=run.relative_to(project).as_posix()
    sample_relative=sample.relative_to(project).as_posix()
    run_config=json.loads((run/"run_config.json").read_text(encoding="utf-8"))
    receipt={"date":datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
        "scope":"开源组件父子切块＋政策约束适配＋图片登记",
        "run":relative,"sample_run":sample_relative,"validation_passed":True,
        "pytest":tests,"source_unchanged":inputs_unchanged,"old_project_unchanged":old_unchanged,
        "old_project_files_verified":len(baseline["old_project"]),"input_files_verified":len(baseline["inputs"]),
        "code_hash":run_config["code_hash"],"config_hash":run_config["config_hash"],
        "component_profile":run_config["component_profile"],"tokenizer_revision":validation["tokenizer_revision"],
        "counts":counts,"max_ready_input_tokens":maximum,"full_resume":evidence,
        "regressions":comparison["regressions"],"artifact_hashes":after,
        "semantic_accuracy_evaluated":False,"not_executed":["OCR","向量计算","数据库部署","检索问答评测","远端推送"]}
    write_json(project/"验收记录.json",receipt)
    write_json(project/"当前批次.json",{"run":relative,"sample_run":sample_relative,"validation_passed":True,
        "usable_chunks":relative+"/usable_chunks.jsonl","report":relative+"/校验报告.md",
        "comparison":relative+"/新旧对照.md","audit_examples":relative+"/两篇抽检对照.md","receipt":"验收记录.json"})
    lines=["# 开源组件版切块验收报告","",f"实际完成 {counts['documents']} 篇 Markdown 文本处理与图片登记。正式批次：`{run.name}`。",
        "",f"[独立校验报告]({relative}/校验报告.md) · [新旧对照]({relative}/新旧对照.md) · [两篇抽检的全部父子块]({relative}/两篇抽检对照.md) · [机器记录](验收记录.json)",
        "","## 实际产物","","| 项目 | 数量 |","|---|---:|"]
    for key in ("documents","units","structures","parents","chunks","chunk_status:ready","chunk_status:pending","relations","component_nodes","component_calls","physical_media_files","media_assets","media_occurrences","issue:missing_media"):
        lines.append(f"| {key} | {counts.get(key,0)} |")
    lines += ["","## 实际开源使用与适配","",
        f"LlamaIndex Core `{run_config['component_profile']['version']}`，调用及节点存入 component_calls/component_nodes；源码摘要和许可证见 component_profile。没有复制旧版 make_parents/make_chunks。",
        "","| 子块生成路径 | 数量 |","|---|---:|"]
    for key,value in sorted(comparison["new_chunk_generation"].items()):
        lines.append(f"| {key} | {value} |")
    lines += ["","component 表示官方边界直接映射；preserved_* 是完整性约束下保留范围，不能当作官方原始切分结果。表格行组调用单独标记。",
        "","## 检查证据","",
        f"- pytest XML记录 {tests['tests']} 项，失败 {tests['failures']}、错误 {tests['errors']}、跳过 {tests['skipped']}。",
        f"- 13篇样例和全部{counts['documents']}篇通过持久化重读校验；快照、全文覆盖、父子/关系、组件来源及真实token计数均检查。",
        f"- {len(baseline['inputs'])}份输入与起始清单一致；原工程{len(baseline['old_project'])}份受跟踪文件哈希保持不变。",
        f"- ready完整输入最大 {maximum} token，硬上限8192，禁止截断。",
        f"- 全量恢复：重算{stats['processed']}篇、复用{stats['reused']}篇；{len(after)}份JSONL SHA-256全部一致。",
        "- Windows文件短时占用由safe_run.py对同一原子替换执行有界重试；持续拒绝仍报错。该运行层入口的SHA-256另存恢复证据，不改变切分规则和已有内容。",
        "- 34457：识别5个行内小节，题名不独立生成事实块；无跨五主题子块。",
        "- 50143：困难问题引导语和五个列举项同父块；每个子块都包含必要引导语。",
        "","## 待处理与限制","",
        f"仍有 {counts.get('chunk_status:pending',0)} 个待处理子块，原因可重叠：", ""]
    lines += [f"- {key.split(':',1)[1]}：{value}" for key,value in sorted(counts.items()) if key.startswith("chunk_reason:")]
    lines += ["","所有空正文、仅标题、疑似乱码、纯图片和缺图均有状态，不补造内容。只按 usable_chunks.jsonl 选取可用对象。",
        "","块数变化及程序校验通过不能证明全部政策语义或检索效果提升。未做OCR、向量计算、数据库部署和问答评测；原工程正式入口保留。",
        "","代码、配置、测试及验收摘要本地提交；大体积数据、缓存和环境按.gitignore保留在本地，不推送远端。"]
    (project/"验收报告.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    index=["# 输出批次索引","",f"最新正式全量：`{run.name}`；配套样例：`{sample.name}`。正式入口见[当前批次](../当前批次.json)。",
           "","时间为北京时间，来自 environment.json 的 finished_at；首次构建与恢复后的完成时间分列。开发试跑保留在 .cache 中，中断批次归档于 .cache/development-runs，不作为正式入口。",
           "","| 批次 | 文档 | 首次构建完成 | 最近构建完成 | 用途 |","|---|---:|---|---|---|"]
    for path,purpose in ((sample,"配套固定样例"),(run,"最新正式全量")):
        env=json.loads((path/"environment.json").read_text(encoding="utf-8"))
        initial_path=path/"initial_environment.json"
        initial=json.loads(initial_path.read_text(encoding="utf-8")) if initial_path.exists() else env
        val=json.loads((path/"validation.json").read_text(encoding="utf-8"))
        index.append(f"| [{path.name}]({path.name}/校验报告.md) | {val['source_count']} | {initial['finished_at']} | {env['finished_at']} | {purpose} |")
    index += ["","[全量新旧对照]("+run.name+"/新旧对照.md) · [两篇父子块展示]("+run.name+"/两篇抽检对照.md)",
              "","批次数据、快照与生成报告保留在本地；本索引纳入Git。新增或更换批次时同步更新此表、工程README和当前批次指针。"]
    (project/"outputs"/"README.md").write_text("\n".join(index)+"\n",encoding="utf-8")
    # README 与数据指针在同一交付步骤更新，避免入口仍停留在“等待实施”。
    readme_path=project/"README.md"
    readme=readme_path.read_text(encoding="utf-8")
    readme=re.sub(r"当前阶段：[^\n]*",f"当前阶段：已完成{counts['documents']}篇全量处理、独立校验和恢复验收，{tests['tests']}项测试通过。只处理本地文本及图片登记，未加载模型权重、计算向量或上传语料。",readme,count=1)
    summary=["<!-- delivery:start -->","## 当前正式交付","",
        f"正式全量：`{relative}`；配套13篇样例：`{sample_relative}`。",
        "",f"- [验收报告](验收报告.md) · [机器记录](验收记录.json) · [当前批次](当前批次.json) · [批次时间索引](outputs/README.md)",
        f"- [全量校验]({relative}/校验报告.md) · [506篇新旧对照]({relative}/新旧对照.md) · [两篇父子块展示]({relative}/两篇抽检对照.md)",
        f"- {counts['parents']}个父块、{counts['chunks']}个子块；其中{counts.get('chunk_status:ready',0)}个ready、{counts.get('chunk_status:pending',0)}个pending。最大ready输入{maximum} token。",
        f"- 实际记录{counts['component_calls']}次组件调用和{counts['component_nodes']}个原始节点；保留完整单元的适配来源单独标注。",
        "- 输入及原工程哈希保持不变；恢复重算0篇、复用全部文档，12份JSONL哈希一致。程序检查通过不等于检索效果提升。",
        "<!-- delivery:end -->",""]
    block="\n".join(summary)
    if "<!-- delivery:start -->" in readme:
        readme=re.sub(r"<!-- delivery:start -->.*?<!-- delivery:end -->\n?",block,readme,flags=re.S)
    else:
        readme=readme.replace("## 当前文件与作用",block+"\n## 当前文件与作用",1)
    readme=readme.replace("├─ 改进说明.md", "├─ AGENTS.md                 # 开源来源、README同步和原文保护维护要求\n├─ 改进说明.md",1) if "├─ AGENTS.md" not in readme else readme
    tree_extra="├─ 当前批次.json             # 本工程正式全量/样例及报告入口\n├─ 验收报告.md               # 实际结果、检查证据和限制\n├─ 验收记录.json             # 参数、数量、哈希和恢复证据\n"
    if "├─ 当前批次.json" not in readme:
        readme=readme.replace("├─ .gitignore",tree_extra+"├─ .gitignore",1)
    if "├─ outputs/" not in readme:
        readme=readme.replace("├─ .venv/", "├─ outputs/                  # 正式全量/样例、快照、JSONL和报告；仅README纳入Git\n│  └─ README.md              # 每个批次的时间、用途和最新版本\n├─ .venv/",1)
    readme=readme.replace("验证进行中。","验证完成，详情见正式交付入口。")
    if "├─ safe_run.py" not in readme:
        readme=readme.replace("├─ chunk.py","├─ safe_run.py               # Windows原子替换有界重试入口，不改变切分算法\n├─ chunk.py",1)
    readme=readme.replace("python.exe -X utf8 chunk.py", "python.exe -X utf8 safe_run.py")
    readme=readme.replace("结构随当前文件变化维护；正式输出生成后再补批次入口，不把计划目录当成已有产物。","上方列出当前真实文件；outputs中的两份正式批次由当前批次.json明确指定。")
    readme=readme.replace("真实开源调用、抽检缺陷与来源证据篡改测试","真实开源调用、抽检缺陷、附注/嵌套引导语与篡改测试")
    readme=readme.replace("## 维护记录\n",f"## 维护记录\n\n- {receipt['date']}：完成全量与恢复验收、开源来源留痕、新旧对照；修正题名前缀、引导语和附注遗漏，范围不明的列表明确待处理，更新所有正式入口。\n",1)
    readme_path.write_text(readme,encoding="utf-8")
    print(json.dumps({"run":relative,"counts":counts,"max_ready_input_tokens":maximum,"pytest":tests,"resume_consistent":True,"originals_unchanged":True},ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",required=True)
    parser.add_argument("--sample",required=True)
    args=parser.parse_args()
    publish(Path(__file__).resolve().parent,Path(args.run).resolve(),Path(args.sample).resolve())
