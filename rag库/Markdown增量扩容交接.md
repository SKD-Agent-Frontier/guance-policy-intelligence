# Markdown 增量扩容交接

本次 PR 提供分批建库、累计发布、结构化筛选和原文回读的实现及回归测试。接收方用已有的复赛数据及原 506 篇运行库在本机复现。数据集、快照、SQLite、BM25 索引、日志和私有配置放在本地运行目录。

## 准备数据和环境

- 复赛 v2.1 数据集根目录须包含 `metadata/sample_plans.csv` 和 `corpus/<planid>/<planid>.md`。脚本按行政级别和规划期分层选取 `corpus`，保留现有政策编号和引用版本。
- 按[运行数据交付说明](运行数据交付.md#下载校验和恢复)恢复原 506 篇运行库。构建使用原库下 `.cache/qwen-tokenizer` 的固定分词器计算切块长度。也可通过 `--tokenizer` 指定同一版本的本地目录。新增检索只使用 BM25。
- 下方命令在 Windows PowerShell、仓库根目录执行。将数据集和原库路径替换成本机实际位置。用 Python 3.12 安装依赖。

```powershell
py -3.12 -m venv 'GitHub排除/.venv-rag'
$ragPy = 'GitHub排除/.venv-rag/Scripts/python.exe'
& $ragPy -m pip install -r 'rag库/向量化与存储工程（降级版）/requirements.lock.txt'
& $ragPy -m pip install -r 'rag库/切块工程/requirements.txt' -e 'rag库/检索工程'

$dataset = 'D:/你的复赛数据集根目录'
$base = 'D:/你的原506篇运行库'
$expansion = 'GitHub排除/rag-md-expansion'
$expandScript = 'rag库/向量化与存储工程（降级版）/expand_md.py'
$paths = @('--dataset', $dataset, '--base', $base, '--root', $expansion)

& $ragPy $expandScript plan @paths
& $ragPy $expandScript build @paths --limit 100 --batch-size 100 --batch-mib 8
& $ragPy $expandScript verify @paths
# 试跑校验通过后继续构建。发布前关闭持有原库的检索进程。
& $ragPy $expandScript build @paths --limit 15000 --cap-gib 20 --workers 6
& $ragPy $expandScript activate @paths
& $ragPy $expandScript status --base $base --root $expansion
```

`--limit` 是累计新增篇数。同一扩容目录中重复执行构建会跳过已处理的政策编号，未完成批次可以续跑。每批上限是 250 篇和 16 MiB。扩容目录默认累计上限为 12 GiB，15,000 篇示例显式设为 20 GiB，所在盘始终保留至少 30 GiB。分片版本取决于输入路径和批次，复现时以本机发布记录为准。

## 系统识别与回退

```powershell
$env:RAG_EXPANSION_ROOT = (Resolve-Path -LiteralPath $expansion).Path
& $ragPy 'rag库/检索工程/run.py' --store-project $base --mode bm25 stats
```

完整构建且发布成功后，`stats` 的 `documents` 应为 15,506。Web 运行进程使用同一 `RAG_EXPANSION_ROOT`，私有配置中将 `KB_BACKEND` 设为 `rag`、`RAG_STORE_PROJECT` 设为原库路径、`RAG_MODE` 设为 `bm25`，然后重启。目录和筛选统计读取同一发布清单。RAG 检索接口的组合过滤支持 `area`、`fyp`、`planids`、`admin_level`、`plantype` 和 `plan_stage`。按 `generation_id` 和 `document_id` 可回到该证据的冻结原文。

需要回退时运行 `& $ragPy $expandScript rollback --base $base --root $expansion` 并重启服务。附加 `--revision N` 可选此前的累计发布；历史快照和引用版本留在本地。发布收据为扩容目录下的 `release_summary.json`。

## 本地真实库验收

2026-11-24 运行记录。耗时为该机器上的单次复测值。

| 项目 | 实测结果 |
| --- | --- |
| 发布规模 | 原库 506 篇，累计新增 15,000 篇，共 15,506 篇 |
| 分片与索引 | 64 个分片复核通过，共 1,033,508 个新增可检索正文块 |
| 质量校验 | 55 篇候选跳过，其中疑似来源异常 39 篇、无可用证据 14 篇、仅标题 2 篇 |
| 检索与引用 | 7 篇新文献通过标题精确定位、组合过滤 Top 10 命中和原文逐字回读。另检验了新旧文献跨批检索 |
| 数据字段 | 新增文献中明确提供规划阶段的记录为 0，缺失值保留为空 |
| 运行时间 | 冷启动 388.48 秒，全库关键词抽测一次 15.47 秒，关闭 10.58 秒 |
| 磁盘与模型 | 扩容产物 17.07 GiB，第 5 版发布后 D 盘余量 124.6 GiB，模型调用 0 次 |

提交前复测通过 196 项自动化测试，覆盖切块、检索、Agent 集成、后端目录与前端。另通过 6 项命令行检查，确认本机第 5 版能被显式路径识别。
