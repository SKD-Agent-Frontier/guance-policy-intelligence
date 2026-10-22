# 切块工程（开源组件版）

采用 LlamaIndex Core `0.14.25` 的 `HierarchicalNodeParser` 与 `SentenceSplitter` 实际生成父子候选；项目代码负责政策结构约束、原文映射、必要关联和校验。选型与边界见[改进说明](改进说明.md)。

当前阶段：已完成506篇全量处理、独立校验和恢复验收，31项测试通过。只处理本地文本及图片登记，未加载模型权重、计算向量或上传语料。

<!-- delivery:start -->
## 当前正式交付

正式全量：`outputs/all-d99495e468-1d289c5d78`；配套13篇样例：`outputs/sample-d99495e468-1d289c5d78`。

- [验收报告](验收报告.md) · [机器记录](验收记录.json) · [当前批次](当前批次.json) · [批次时间索引](outputs/README.md)
- [全量校验](outputs/all-d99495e468-1d289c5d78/校验报告.md) · [506篇新旧对照](outputs/all-d99495e468-1d289c5d78/新旧对照.md) · [两篇父子块展示](outputs/all-d99495e468-1d289c5d78/两篇抽检对照.md)
- 40209个父块、43542个子块；其中43460个ready、82个pending。最大ready输入8030 token。
- 实际记录40207次组件调用和84903个原始节点；保留完整单元的适配来源单独标注。
- 输入及原工程哈希保持不变；恢复重算0篇、复用全部文档，12份JSONL哈希一致。程序检查通过不等于检索效果提升。
<!-- delivery:end -->

## 当前文件与作用

```text
切块工程（开源组件版）/
├─ README.md                 # 工程状态、结构、用法和维护记录
├─ AGENTS.md                 # 开源来源、README同步和原文保护维护要求
├─ 改进说明.md               # 用户已确认的方案及原版差异
├─ safe_run.py               # Windows原子替换有界重试入口，不改变切分算法
├─ chunk.py                  # build / validate 命令入口
├─ compare.py                # 同源新旧对照、两篇抽检父子块展示
├─ deliver.py                # 恢复实测、原工程/输入保护核对、验收报告和正式指针
├─ config.json               # 768/3072/8192、零重叠、中文分隔配置和13篇样例
├─ model_profile.json        # 复用的Qwen tokenizer修订和7份文件摘要
├─ component_profile.json    # 实装LlamaIndex版本、许可证、两份官方源码SHA-256
├─ requirements.txt          # 固定直接依赖版本
├─ requirements.lock.txt     # 实装环境完整依赖锁
├─ 复用来源.json             # 复用旧工程的模块与提取工具清单
├─ 当前批次.json             # 本工程正式全量/样例及报告入口
├─ 验收报告.md               # 实际结果、检查证据和限制
├─ 验收记录.json             # 参数、数量、哈希和恢复证据
├─ .gitignore                # 排除环境、缓存、输出数据，保留批次README
├─ .gitattributes            # 源码/配置保留已验字节，文档LF，派生数据不转换
├─ policy_chunker/
│  ├─ __init__.py            # 包说明
│  ├─ source.py              # 字节读取、码点坐标、oss1身份和原子读写
│  ├─ structure.py           # Markdown线索、行内连续标题、中文列举识别
│  ├─ protection.py          # 复用质量、句群安全边界和表格网格工具；不包含旧切分核心
│  ├─ engine.py              # 官方组件真实调用、确定性节点ID、顺序有界原文映射
│  ├─ chunking.py            # 结构输入与保护约束、组件候选审核及完整单元保留
│  ├─ relations.py           # 条件/定义/例外/引用关联和最终输入；超限如实待处理
│  ├─ tokens.py              # 本地官方Qwen tokenizer、模板及防截断计数
│  ├─ media.py               # 图片登记、出现范围和缺失引用
│  ├─ pipeline.py            # 单worker、逐篇检查点、恢复、汇总与报告
│  └─ validation.py          # 独立重读、原文/关系/组件证据/长度校验
├─ tests/
│  ├─ test_chunker.py        # 复用原版23项回归/集成测试
│  ├─ test_open_source.py    # 真实开源调用、抽检缺陷、附注/嵌套引导语与篡改测试
│  └─ test_io_retry.py        # Windows短时占用恢复及永久拒绝不得吞错
├─ outputs/                  # 正式全量/样例、快照、JSONL和报告；仅README纳入Git
│  └─ README.md              # 每个批次的时间、用途和最新版本
├─ .venv/                    # 独立Python环境，Git忽略
└─ .cache/                   # tokenizer、逐篇缓存、开发样例、测试与输入基线，Git忽略
```

上方列出当前真实文件；outputs中的两份正式批次由当前批次.json明确指定。输入只读来自仓库 `语料库/Agent标准引用库/{corpus,edge_cases}`；训练集不参与。

## 运行方式

在本目录的 PowerShell 中执行；已安装环境可直接使用。迁移时以 Python 3.12 创建独立环境，按 lock 文件安装依赖，并从原工程核验复制 `model_profile.json` 对应的7份 tokenizer 文件至 `.cache/qwen-tokenizer/`。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --basetemp=.cache/pytest-local
.\.venv\Scripts\python.exe -X utf8 safe_run.py build --sample --resume
.\.venv\Scripts\python.exe -X utf8 safe_run.py build --all --resume
.\.venv\Scripts\python.exe -X utf8 safe_run.py validate --run 'outputs/<批次>'
.\.venv\Scripts\python.exe -X utf8 compare.py --new 'outputs/<批次>'
```

`--source` 可指定同结构的测试库；`--run` 可指定独立批次。配置、来源、组件源码或程序改变后另建批次；恢复不会混入不同版本。数据和快照保存在本工程，原工程入口不切换。

## 实现边界

- 开源负责递归拆分和父子关系；`component_nodes.jsonl`、`component_calls.jsonl` 保存原始节点、参数、关系与映射，不能只凭组件名称判断来源。
- `generation=component` 表示直接采用官方边界；`preserved_complete_*` 表示约束下保留完整单元；表格行组另记来源。所有有效块都有实际调用证据，不将适配结果冒充官方原始边界。
- Qwen裸文本token用于组件预算，完整检索输入另用官方模板真实计数；目标768是组织参考，硬上限8192禁止截断。表格、完整条文和列表优先保留必要上下文。
- 不直接使用库裁剪空白后的文本作为证据：逐层顺序有界映射，再从完整原文范围取回。CRLF、Unicode码点及重复句均检查；不能唯一定位则待处理。
- 引导语和列表在同一保护父块；每个项目需携带必要引导语。行内标题只接受有位置证明的连续编号，不向原文补换行。
- 文档、单元、结构、父子块、必要关联、图片、异常等表沿用原字段用途；新增组件表提供来源证明。仅校验通过的 `usable_chunks.jsonl` 可供后续索引。父块status只表示容器已建立，可用性以子块状态和可用成员清单为准。
- 原文位置与程序约束通过不等于全部政策语义或检索效果正确；已知依赖、超限和异常如实留在待处理清单。

## 输出字段与来源怎样核对

正常批次含 `documents`、`units`、`structures`、`parents`、`chunks`、`relations`、`media_assets`、`media_occurrences`、`issues` 这9份JSONL，字段用途与原工程一致；另外新增：

| 产物/字段 | 作用 |
|---|---|
| `component_calls.jsonl` | 每次真实调用的范围、版本、父子预算、中文分隔参数、节点ID及映射状态 |
| `component_nodes.jsonl` | 库原始文本、节点/父节点/子节点ID、库内局部下标、全文件映射与摘要 |
| `parents/chunks.component_node_ids` | 把最终父子块反查到真实上游节点；不靠名称猜测开源作用 |
| `generation` | `component`直接采用库边界；`preserved_complete_unit/list_item`保留完整单元；表格安全行组和失败范围另记 |
| `units.original_unit_id` | 普通原单元跨官方父边界时记录细化前身份；原文仍连续完整保存 |
| `source_segments/context_segments` | 原文正文与必要共享上下文，分别保存全文件码点范围，逐字校验 |
| `retrieval_text/embedding_tokens` | 派生检索输入和带官方模板的真实长度，不作为原文引用 |
| `usable_chunks.jsonl` | 独立校验通过后才生成的可用成员清单；不能把chunks全表直接入索引 |
| `comparison.json`、`新旧对照.md` | 同源逐篇数量、状态、组件/适配来源和已知两篇缺陷对照 |
| `两篇抽检对照.md` | 34457、50143全部父子范围、token、生成路径与正文预览 |

`initial_*`和`resume_evidence.json/resume.log`由`deliver.py`在恢复验收时保存，普通build不会自动生成；快照保存源Markdown原始字节。完整数据不提交Git。

切分指纹覆盖policy_chunker/*.py、chunk.py、组件profile及依赖锁；safe_run.py只负责原子I/O重试，其SHA-256另存恢复证据，不改变块身份。

代码指纹依据源码字节，包含注释和换行；Git对程序、JSON和依赖文件禁用自动换行转换，确保已验批次指纹与提交字节一致。只改注释也会产生新的后续批次身份，已有批次不回写成新代码的验收证明。

## 后续维护

每次修改本工程文件，同步更新本README的结构、职责、命令、状态和实际结果。代码含中文说明；样例或正式批次改变时同步维护批次README和本工程当前入口。保留旧工程，提交只含本任务代码、配置、测试和报告摘要，环境与生成数据不提交。

## 维护记录

- 2026-10-22：完成全量与恢复验收、开源来源留痕、新旧对照；修正题名前缀、引导语和附注遗漏，范围不明的列表明确待处理，更新所有正式入口。

- 2026-10-22：用户授权实施后，接入LlamaIndex、复用通用定位/校验能力并明确来源；新增真实组件节点记录、13篇样例、新旧对照及回归测试。当前验证完成，详情见正式交付入口。
- 2026-10-22：建立独立目录，先交付改进说明供用户查看。
