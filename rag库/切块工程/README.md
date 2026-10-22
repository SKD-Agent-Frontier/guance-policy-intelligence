# RAG 2.0 文本切块工程

按照 `../设计方案2.0/切块方案.md` 实现 Markdown 结构识别、原文定位、父子块、必要关联、图片登记、断点恢复及独立校验。代码中的规则说明为中文；不执行 OCR、向量计算或检索问答，不调用生成模型，不上传语料。

首次阅读建议先看[代码阅读说明](代码阅读说明.md)：包含调用顺序、关键变量、对象/字段关系、表格与引用算法、token 计数、恢复和校验原理。各源码同步补充了对应位置的详细中文注释。

## 当前文件结构

以下结构对应 `rag库/切块工程/`。源码、配置和文档完整列出；批次与缓存只展开当前交付和代表性内容，自动生成的临时文件不逐个列出。新增、删除、移动、重命名或修改文件时，按文末维护约定同步更新本 README。

```text
切块工程/
├─ README.md                     # 工程总入口：结构、职责、运行、输出及维护约定
├─ AGENTS.md                     # 后续开发需遵守的 README 同步维护约定
├─ 代码阅读说明.md               # 沿调用链解释关键变量、算法、对象关系与边界
├─ chunk.py                      # 命令行入口：build / validate
├─ prepare_model.py              # 下载固定版本的分词器/配置，不下载模型权重
├─ config.json                   # 切块参数、输入长度上限、固定样例 planid
├─ model_profile.json            # Qwen 修订、配置文件摘要和计数方式
├─ requirements.txt              # 直接依赖的兼容版本范围
├─ requirements.lock.txt         # 已验证环境的完整依赖版本
├─ 当前批次.json                 # 当前正式全量/样例批次及报告、可用清单入口
├─ 验收报告.md                   # 本次正式交付的结果、验证与限制摘要
├─ 验收记录.json                 # 机器可读验收记录及输出哈希
├─ .gitignore                    # 排除环境、缓存、生成数据和 Python 临时文件
├─ .gitattributes                # 固定源码换行；派生数据不做文本转换
├─ policy_chunker/               # 流水线实现，具体职责见下方模块表
│  ├─ __init__.py                # 包说明；导入时不启动任务
│  ├─ source.py                  # 读取、原文坐标、身份、清单与文件读写
│  ├─ structure.py               # 基础单元、标题/条文识别、章节归属
│  ├─ chunking.py                # 质量状态、父子块、句群与表格行组
│  ├─ relations.py               # 必要关联、依赖展开、检索输入定稿
│  ├─ tokens.py                  # 官方模板装配和真实 token 计数
│  ├─ media.py                   # 图片资源及出现位置登记
│  └─ validation.py              # 单篇和整批确定性校验、可用成员清单
├─ tests/
│  └─ test_chunker.py            # 边界规则、反向篡改、恢复与增量集成测试
├─ outputs/                     # 本地生成数据，Git 忽略
│  ├─ all-9dd48b1750-1d289c5d78/    # 当前正式全量批次
│  ├─ sample-9dd48b1750-1d289c5d78/ # 同规则的固定样例批次
│  └─ 其他 all-*/sample-*/      # 历史调试批次，不自动成为正式交付
├─ .cache/                      # 本地缓存和开发辅助文件，Git 忽略
│  ├─ qwen-tokenizer/           # 官方分词器、词表及 processor 配置
│  ├─ documents/                # 单篇结果分片及摘要，按配置/任务身份缓存
│  ├─ http-v2/                  # pip 下载缓存，不是政策数据
│  ├─ tmp/                     # 环境安装等临时文件
│  ├─ pytest-*/                 # 测试临时输入、输出和缓存损坏模拟文件
│  ├─ check_delivery.py         # 本次交付的全量恢复验收辅助脚本
│  ├─ explanation_baseline.json # 注释修改前的源文件/交付结果核对基线
│  └─ explanation_ast_check.json # 注释修改前后的 AST 与注释数量检查记录
├─ .venv/                       # Python 独立环境，Git 忽略
├─ .pytest_cache/               # pytest 自动缓存，Git 忽略
└─ __pycache__/                 # Python 字节码缓存；源码包/测试目录内也可能出现
```

工程之外的相关位置：`../设计方案2.0/` 保存四份设计文档；仓库内 `语料库/Agent标准引用库/{corpus,edge_cases}/` 是只读输入，包含 `<planid>.md` 和 `media/`；`训练集/` 不进入政策证据切块。工程没有把这些来源复制为源码目录的一部分。

### 顶层文件怎样配合

| 文件 | 作用与使用方式 |
|---|---|
| [AGENTS.md](AGENTS.md) | 将“本工程文件变化时同步更新 README”固化为后续开发约定 |
| [chunk.py](chunk.py) | 日常构建/校验入口；选定范围后调用流水线，不在入口里实现切分规则 |
| [prepare_model.py](prepare_model.py) | 首次准备或恢复 tokenizer 文件时使用；正常处理不需要再次下载 |
| [config.json](config.json) | 默认 768/3072/8192 token 参数、固定样例、规则版本和输入指令；不是历史批次的配置替身 |
| [model_profile.json](model_profile.json) | 固定官方修订及各下载文件 SHA-256，供加载分词器时逐项核对 |
| [requirements.txt](requirements.txt)、[requirements.lock.txt](requirements.lock.txt) | 前者声明兼容范围，后者复现已验证的直接和间接依赖；复现优先安装 lock 文件 |
| [当前批次.json](当前批次.json) | 查找当前正式数据入口；不以目录时间或名字排序选择批次 |
| [验收报告.md](验收报告.md)、[验收记录.json](验收记录.json) | 前者面向阅读，后者供程序核对数量、配置、恢复证据和产物摘要；都是原执行批次的记录 |
| [代码阅读说明.md](代码阅读说明.md) | 解释模块调用顺序及算法；具体修改规则仍需对照源码和测试 |
| [.gitignore](.gitignore)、[.gitattributes](.gitattributes) | 控制提交范围与文本换行；不会清洗或改写输入库 |

`.cache/check_delivery.py` 针对本次固定批次编写，用来保存首次构建结果、执行恢复重跑、比较 JSONL 哈希并生成交付摘要；它不是日常通用构建入口。`.cache` 中的检查记录和测试目录也不属于政策证据，不参与正常输入扫描。

### 当前正式数据入口

当前指针 [当前批次.json](当前批次.json) 对应：

- 全量结果：`outputs/all-9dd48b1750-1d289c5d78/`，原批次处理 506 篇。
- [可用子块成员清单](outputs/all-9dd48b1750-1d289c5d78/usable_chunks.jsonl)：42465 个 ready 子块；完整子块表共 42516 条，另有 51 条 pending。
- [完整校验报告](outputs/all-9dd48b1750-1d289c5d78/校验报告.md)与[切块样例](outputs/all-9dd48b1750-1d289c5d78/切块样例.md)。
- 对应样例批次：`outputs/sample-9dd48b1750-1d289c5d78/`。

这些链接指向本地生成数据，受 `.gitignore` 排除；只检出代码的环境中不一定存在，需构建后才有。后续正式批次变化时，同步更新本节及 `当前批次.json`，保留历史批次自身的验收记录。

## 环境与复现

Python 3.12。实际安装的直接与间接依赖固定在 `requirements.lock.txt`；`requirements.txt` 表示兼容范围。Qwen tokenizer 修订及每个配置文件的 SHA-256 在 `model_profile.json`，不包含模型权重。恢复同一批次时配置、模型修订、代码与输入必须一致。

在本目录运行 PowerShell：

```powershell
# 本机已验证的 Python 3.12；其他机器可换成等效的 Python 3.12。
& 'C:\Users\39962\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe prepare_model.py
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.cache/pytest-local
.\.venv\Scripts\python.exe -X utf8 chunk.py build --sample
.\.venv\Scripts\python.exe -X utf8 chunk.py build --all --resume
```

`prepare_model.py` 仅连接 Hugging Face 官方模型仓库下载分词及 processor 配置；已存在 profile 时严格使用记录的修订。处理、恢复、测试及校验均离线运行。

```powershell
# 将下面路径替换为实际批次，独立重读 JSONL、快照和来源进行校验。
.\.venv\Scripts\python.exe -X utf8 chunk.py validate --run 'outputs/<批次>'

# 可指定新批次目录，输入变化或规则变化时另建批次，旧快照不覆盖。
.\.venv\Scripts\python.exe -X utf8 chunk.py build --all --resume --run 'outputs/<新批次>'
```

默认输入是仓库内 `语料库/Agent标准引用库` 的 `corpus` 与 `edge_cases`。`--source` 可指定相同目录结构的输入库，用于离线迁移及回归测试。输入根下其他目录和训练集不被扫描。输出不能位于输入库内。

若隔离环境的系统临时目录不可写，可将当前 PowerShell 进程的 TEMP/TMP 指向本目录 `.cache/tmp`；这不需要修改系统级环境变量。

## 规则与代码入口

| 模块 | 职责 |
|---|---|
| [source.py](policy_chunker/source.py) | 输入字节→完整原文、码点坐标和文档版本；还提供 JSON/JSONL 原子写入及输入清单 |
| [structure.py](policy_chunker/structure.py) | 原文→排他基础单元与可嵌套章节；识别编号、目录、表格及归属，保留原文范围 |
| [chunking.py](policy_chunker/chunking.py) | 单元/章节→父块与子块草稿；处理质量状态、安全句群、HTML 合并单元格和 Markdown 行组 |
| [relations.py](policy_chunker/relations.py) | 草稿→带必要条件、定义、例外和明确引用上下文的子块；定稿并重新检查长度 |
| [tokens.py](policy_chunker/tokens.py) | 检索文本→官方模板输入、真实 token 数与配置身份；不加载模型权重 |
| [media.py](policy_chunker/media.py) | 原图/引用→资源、出现位置和异常记录；只登记，不执行 OCR |
| [pipeline.py](policy_chunker/pipeline.py) | 组织配置、逐篇处理、缓存恢复、快照、汇总、校验与报告，是整批调度入口 |
| [validation.py](policy_chunker/validation.py) | 重新读取对象和来源，校验原文、结构、依赖、长度、资源和成员，生成可用清单 |
| [test_chunker.py](tests/test_chunker.py) | 使用临时政策与小图片验证规则、错误拦截、恢复和增量，不修改真实语料 |

位置都是完整源文件的 0 起点、左闭右开 Unicode 码点，包括 front-matter。原文通过 `read_bytes().decode('utf-8')` 获取，不转换 CRLF。Markdown 解析结果只提供线索，原文文本始终直接按范围取回。行号仅辅助展示，采用 CR/LF 换行定义；不能把 UTF-16 下标直接当本工程位置。

子块目标 768 token，普通父块参考 3072 token，完整检索输入上限 8192 token。完整原子单元可以超过目标；只能沿安全句群或表格逻辑行组处理超限。纯代码块不按政策句子拆分。条件、单位、表头和附注加入输入后重新计数；无法安全处理的对象保留为 pending。

HTML 行组只在网格能验证且所有 rowspan 已结束的位置划分。缺少可靠表头、网格不规则或无法安全分组的超长表格待处理。父块始终保留整个原表。未唯一解析的引用、外部文件引用和循环依赖不会被猜测补全。

## 输出与下游接口

每个 `outputs/<批次>/` 内包含：

- `documents.jsonl`：来源版本、planid、原始元数据、源路径、快照、哈希、编码和状态。
- `units.jsonl`：排他基础单元。顺序拼接 `text` 必须等于完整源文件；`disposition` 为 represented、pending 或 excluded，后两者附原因。
- `structures.jsonl`：可嵌套章节与条文，包含文档根、原文范围、标题路径及编号作用域。无可靠标题时根标题为空。
- `parents.jsonl`：父块、完整范围、单元成员、章节归属。主要用于回取，不直接承诺可独立入索引。
- `chunks.jsonl`：全部子块，包括 ready 和 pending。`source_segments` 保存证据原文；`context_segments` 保存必要原文上下文；`retrieval_text` 仅用于检索。
- `relations.jsonl`：触发原文、来源父块、目标类型/ID、规则及 resolved/unresolved 状态。明确关联可展开其必要依赖，循环保持待处理，不推理新关系。
- `media_assets.jsonl`：字节相同图片共用 asset 身份，并保留全部物理路径与文档关联。
- `media_occurrences.jsonl`：图片每次引用的独立身份和原文位置，包含缺失状态；未引用图片只登记资源，不猜测出现位置。
- `issues.jsonl`：异常及位置，区分源数据问题、待处理内容和处理错误。
- `usable_chunks.jsonl`：**只有独立校验通过后才生成的 ready 子块成员清单**，包含子块/文档/父块 ID、token 数与实际模型输入哈希。
- `snapshots/`：原始 Markdown 字节快照；本轮媒体仅登记路径、哈希与尺寸，不生成冻结图片副本或识别结果。
- 配置、输入清单、检查点、环境、统计、`validation.json`、`校验报告.md` 和 `切块样例.md`。

除上述业务数据，每个正常构建批次还保存以下运行文件：

| 文件 | 作用 |
|---|---|
| `run_config.json` | 冻结本批参数、模型 profile、代码/配置哈希、输入根和选中文档清单 |
| `input_manifest.json` | 构建前输入的相对路径、字节数和 SHA-256，用于结束时核对增删改 |
| `checkpoint.json` | 逐篇完成状态与分片摘要，用于恢复；不是子块数据库 |
| `build_stats.json` | 最近一次构建的 processed/reused 数量及构建耗时；恢复运行会更新它 |
| `environment.json` | 实际依赖版本、worker 数和完成时间 |
| `git_baseline.txt` | 建立批次时的 Git 状态，辅助区分原有工作区变更 |
| `validation.json` | 程序检查是否通过、数量、错误和输入是否保持不变；不代表语义准确率 |
| `校验报告.md`、`切块样例.md` | 人可读校验摘要和截短预览；真实证据以原文段及快照为准 |

本次正式全量批次为恢复验收额外保存了四个文件，它们不是每次普通构建都会生成的文件：

| 文件 | 作用 |
|---|---|
| `initial_build_stats.json` | 首次构建统计，避免被后续恢复运行的统计覆盖 |
| `initial_validation.json` | 首次完整校验结果的留存副本 |
| `resume_evidence.json` | 全量恢复的退出状态、复用数量及前后 10 份 JSONL 哈希对比 |
| `resume.log` | 该次恢复命令的实际输出日志 |

`snapshots/<source_hash>/<planid>.md` 是原始 Markdown 字节快照；`.cache/documents/<config_hash>/` 下的任务 JSON 及 `*.checksum.json` 是可复用处理缓存。前者用于原文溯源，后者用于减少重算，两者不能互相替代。

**下游应通过 usable 清单选择 chunks，不能将 chunks 全表直接入索引。** pending 块可能超限或缺少已知必要上下文；父块与 chunks 的原文范围不等于可无条件引用的完整证据。检索增强字段与原文引句必须分开使用。

文档版本由 `planid + source_hash` 决定；结构、父子块 ID 同时包含原文位置和配置/规则指纹。代码变化也进入规则指纹。完全相同的正文来自不同 planid 时仍是独立来源，不合并政策身份。正文内容指纹只提供给后续去重，不删除内容。

文档状态包括 ready、partial、no_body、media_only、source_suspect、title_only、error。图片缺失本身不阻断不依赖图片的原生正文；所有图片均标记未识别，不能据此推断图中事实。

## 恢复、验证与已知边界

检查点采用完整 JSON 原子写入及 SHA-256；缓存损坏时重算。新批次可复用配置、来源、媒体清单均未变化的缓存。增量更新另建完整批次，旧引用快照保留。恢复模式不会在旧批次中混入变化后的来源或规则。

代码指纹按源码字节计算，包含注释；因此补注释也会使后续构建生成新批次标识。已有批次及原验收记录保持不变，其指纹对应当时执行版本，详情见[本次说明](代码阅读说明.md#9-本次只增加解释已有结果如何处理)。

独立校验会重新装配输入、重新分词、核对逐字范围、结构归属、必要上下文、源文件和媒体哈希，并检查文档成员完整性。校验失败时可用清单清空，CLI 返回非零。单文档处理错误保留 error 记录，不伪装成功；源数据待处理内容与程序错误分开报告。

规则是确定性的保守启发式，不能保证识别全部目录、损坏文本、隐含条件或跨页表格关系。极度断行文档可跳过 Markdown 解析线索，转为原文边界规则；该降级在 issue 中保留。程序校验通过不是政策语义准确率，也不是问答效果指标。

原始数据、环境、模型配置缓存及大体积生成数据不提交 Git；代码、固定配置、依赖锁、模型 profile、运行说明和实际验收摘要本地提交。未进行远端推送。

## README 同步维护约定

后续修改本工程文件时，在同一轮修改中核对并更新本 README；该约定也写入 [AGENTS.md](AGENTS.md)。

- 新增、删除、移动或重命名文件/目录：同步更新目录树、文件作用及相关链接。
- 修改已有文件：按实际影响更新模块职责、调用入口、参数、依赖、输出格式或使用边界；仅注释/文案变化也在维护记录简述，不虚构功能变化。
- 更换正式交付批次：同步维护 `当前批次.json` 与本 README 的正式入口、数量和链接；历史批次记录保留原版本含义。
- 更新完成后，对照实际目录检查路径和链接。缓存/临时文件不逐个加入目录树；其用途或组织方式变化时更新对应分类。

### 维护记录

- 2026-10-22：补全当前工程、只读输入、本地批次、缓存、运行文件及验收辅助文件的结构和用途；新增 `AGENTS.md` 固化同步维护要求。本轮仅改文档，不重新切块。
