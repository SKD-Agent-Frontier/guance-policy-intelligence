# 向量化与存储工程（降级版）

本工程从原`向量化与存储工程`复制并改造，保留原有文本向量化、图片识别、视觉语义子块、多模态向量化、输入计数、来源定位和父块回取。原源码已从仓库移除，不在当前运行链路内。主要变化是将 **PostgreSQL + Qdrant Server 换为 SQLite + Qdrant Local + BM25S**，无需安装或启动数据库服务。

2026-10-24仅整理GitHub目录：本工程、上游切块、语料及当前正式数据路径均未移动。`migration_source=../向量化与存储工程`仍指向本机原地保留的旧冻结数据和指针，供显式迁移使用；该目录由Git忽略，不是归档源码路径。日常检索使用本工程已发布库，不依赖旧PostgreSQL目录。复用来源及原发布记录中的旧路径、哈希按历史含义保留。总入口见[RAG工程说明](../README.md)。

本地的是存储与检索计算。新文本/图片向量、OCR仍访问既有模型服务；模型权重没有下载到本机。纯 BM25 查询可离线运行，也不会打开 dense 向量库。

当前已建库通过[完整运行数据Release](https://github.com/SKD-Agent-Frontier/guance-policy-intelligence/releases/tag/rag-v0.4.0-data-20261024-r1)交付，包括本工程的SQLite、Qdrant Local、BM25S、冻结原文／图片、OCR和当前向量缓存。请使用`restore_delivery.py`校验分卷并恢复到新目录，步骤与本次校验范围见[运行数据交付说明](../运行数据交付.md)；私有服务地址和凭证需在接收方本地配置。

## 与原版的关系

| 项目 | 原版 | 本降级版 |
|---|---|---|
| 来源、对象、父子关系、任务、发布指针 | PostgreSQL schema | 一个独立 SQLite 文件 |
| Dense 向量 | 独立 Qdrant 服务 | `QdrantClient(path=...)`，进程内持久化 |
| 中文关键词 | 原工程的词法数据/检索接口 | jieba + 真正的 BM25S 稀疏矩阵和词表 |
| 统一检索 | 原服务端接口 | dense/BM25 各自过滤后，应用层 RRF 融合 |
| 模型 | Qwen3-VL-Embedding-8B，4096 维 | 保持相同模型、模板和向量空间 |
| 初始数据 | 已完成的文本、OCR和图片表示 | 校验并复用原缓存，不全量重算 |
| 并发/运维 | 服务端多客户端 | 项目单进程锁；适合单机开发和验证 |
| 备份 | 服务端导出/快照 | 关闭库后逐文件校验的本地 ZIP 备份 |

原工程、上游切块、原数据库均保留。本目录不包含旧数据库服务二进制、服务器启动脚本、旧备份及旧验收结论。不可变输入和缓存在同盘优先建立硬链接，数据库是本工程新建的独立文件；删除任意一个硬链接文件名不会让另一份路径失效，但不能原地编辑共享字节。缓存使用原子替换写入。

## 实际状态

2026-10-23已完成迁移、发布和重新开库后的独立全量复核。当前版本为`local_generation_e125cd0f3a4faf24860081796440a6e73f26196edb6bb838dd93c7a92ec1078f`，集合为`guance_local_3040f34d76751123c4af`。详细证据见`验收报告.md`、`实施记录.json`及`检索与存储抽检.json`。

| 实际纳入 | 数量 |
|---|---:|
| 文档 | 506 |
| 原生文本子块覆盖 | 42,516 / 42,516 |
| 原生文本输入表示 | 42,551 |
| OCR文本子块/表示 | 3,562 |
| BM25S文本成员 | 46,113 |
| 原图表示 | 919 |
| 局部图/图文表示 | 256 / 189 |
| 本地向量表示总数 | 47,477 |
| 不同实际输入缓存 | 45,483 |
| 迁移新增语料向量计算 | 0 |

17个超长原生子块保留原身份，并形成52个完整输入片段，因此原生文本表示是42,516−17＋52＝42,551；加3,562个OCR文本表示得到46,113个词法成员。父块没有被向量化或重切。中断续跑实际复用了8,704个已落盘point，最后一次迁移只补38,773个point。

35项回归通过，并对后续存储/校验调整完成4项本地专项回归；47,477条向量重新开库逐条复核通过。五文档抽检、BM25/混合检索和唯一词零命中检查通过。2026-10-23建库验收时未运行整库恢复演练，仅完成小型真实本地库的关闭备份/恢复回归；2026-10-24完整数据交付的恢复校验另见[交付说明](../运行数据交付.md)和对应Release验证附件。

图片分支继承479个资产的既有真实识别结果。2026-10-24核对当前revision=1发布，以下数据缺口仍保留原状态，发布带`partial=true`媒体缺口标记：

| 缺口 | 具体原因与计数口径 | 影响 |
|---|---|---|
| 285处缺失图片引用 | Markdown含图片引用，但未登记到对应图片资产；统计引用次数，不是独立图片张数 | 这些引用无法回取原图或OCR，需补文件或核对路径 |
| 11个资产OCR失败 | 9个模型输出因长度上限截断，2个返回无法解析的JSON；同一资产可关联多个文档位置，共影响26个出现位置 | 已有原图及其图像表示保留，仍能检索/回图；这些位置没有可用OCR |
| 56个不确定OCR子块 | 状态为pending，识别记录含小字、模糊数字、遮挡等不确定内容 | 已识别文字及质量提示保留，需对照原图核验 |

原始记录位于当前发布关联的`outputs/prepared_*/media_occurrences.jsonl`、`outputs/media_*/media_failures.jsonl`和`visual_chunks.jsonl`，汇总见`outputs/local_generation_*/media_summary.json`。285处缺图引用加26个OCR失败位置，对应汇总中的311个`missing_or_unprocessed_occurrences`；11与26分别按资产和出现位置计数，不是两批失败图片。本轮仅补充说明，没有补图、重做识别或改变发布状态。

另有422个图片来源只能由真实目录确认文档归属，缺少Markdown插入位置；这类图片与其OCR仍可按媒体ID直接回取。检索工程将章节/正文锚点作为可选信息，具体规则见[媒体回取与数据缺口](../检索工程/README.md#媒体命中后从哪里回取)。不根据文件名补造章节或页码。

## 当前文件结构与作用

以下路径均相对于本工程；所有实际数据都在本目录内。

```text
向量化与存储工程（降级版）/
├─ README.md                  本说明：架构、文件作用、运行方式、状态和限制
├─ AGENTS.md                  后续维护约定，要求结构变化同步本说明
├─ run.py                     命令行入口
├─ restore_delivery.py        Release分卷校验、恢复到新目录与本地模型配置绑定
├─ config.json                模型/批处理/完整覆盖/本地后端配置
├─ schema.sql                 SQLite 正式建表 SQL，外键及发布指针约束
├─ requirements.txt           直接依赖的固定版本
├─ requirements.lock.txt      本独立环境的完整实际版本
├─ model_profile.json         tokenizer 修订号及七份文件的校验和
├─ secrets.example.json       不含实际密钥的模型服务配置示例
├─ secrets.local.json         实际模型地址和凭证，Git 忽略
├─ 复用来源.json              原工程提交及文件哈希、复用/修改边界
├─ 实施记录.json              实际迁移、验证、调用和存储统计
├─ 验收报告.md                本轮验证依据、结果和未验证项
├─ 检索与存储抽检.json         独立全量复核、五文档及三次检索的实际证据
├─ 接口探测记录.json          少量真实文本/图片向量接口探测结果
├─ 当前批次.json              可读发布指针副本；SQLite runtime 才是权威
├─ vector_store/              下表逐个解释的业务代码和存储适配器
├─ tests/
│  ├─ test_core.py            继承的原文/计数/OCR/片段覆盖/缓存回归
│  └─ test_local.py           SQLite、BM25、Local、续跑、备份恢复集成回归
├─ outputs/
│  ├─ README.md               批次角色、创建时间、当前发布版本
│  ├─ prepared_<hash>/        冻结的来源表、Markdown/图片快照和资格清单
│  ├─ media_<hash>/           OCR提取、视觉父子块、失败及排除记录
│  └─ local_generation_<hash>/ 表示计划、报告、校验及发布清单
├─ .runtime/
│  ├─ project.lock            CLI互斥锁（读写均持有）
│  └─ local/
│     ├─ metadata.sqlite3     来源、父子、关系、表示、任务及发布元数据
│     ├─ qdrant/              Qdrant Local 配置及集合持久化文件
│     └─ bm25/<generation>/   BM25S词表、稀疏矩阵、成员和文件哈希
├─ .cache/
│  ├─ vectors/<profile>/      文档/图片4096维float32缓存和校验元数据
│  ├─ query_vectors/<profile>/ 用户问题的向量缓存（与文档缓存分开）
│  ├─ qwen-tokenizer/         七份已固定处理器文件，无模型权重
│  ├─ ocr/                   既有或新调用的图片识别结果
│  ├─ ocr-failures/           识别失败的确定性记录
│  ├─ ocr-attempts/           识别原始尝试与输出，保留失败原因
│  ├─ derived_images/         区域图片和视觉输入的派生图像
│  ├─ service-canaries/       固定输入向量探针，发现明显空间变化
│  ├─ service_calls.jsonl     脱敏模型调用审计，不记录密钥
│  ├─ dedup/                 新build的完全重复和近重复候选缓存
│  ├─ media-work/            新build的图片处理工作区
│  ├─ backups/               显式backup时生成的ZIP及校验回执
│  ├─ restore-tests/         显式restore-drill的完整恢复副本
│  └─ wheelhouse/、pip/、tests-*/等 安装及测试临时材料
└─ .venv/                     本工程独立Python环境，不改原环境
```

`.runtime`、`.cache`、`.venv`、实际凭证与 `outputs` 大数据受 `.gitignore` 排除；`outputs/README.md`、代码、配置、版本清单和精简验收材料进入 Git。备份和恢复副本仅在显式执行对应命令时生成，会额外占空间。

| 模块 | 文件职责与关键边界 |
|---|---|
| `__init__.py` | Python包标识 |
| `common.py` | 规范JSON/哈希/ID、Unicode范围验证、原子文件写入、Windows长路径、配置及脱敏 |
| `contracts.py` | MetadataStore、VectorStore、LexicalStore三个独立接口；后续切换后端的约定 |
| `storage.py` | 唯一存储装配入口，把配置映射到三个适配器 |
| `sqlite_metadata.py` | SQLite事务、不可变对象、外键、父子/引用、任务、版本与发布revision校验 |
| `qdrant_local.py` | 真正的Qdrant Local创建/写入/逐点扫描/过滤检索；延迟打开向量库 |
| `bm25s_index.py` | jieba中文分词、BM25S索引/持久化/mmap；先过滤Top-K，再去除零分填充 |
| `prepare.py` | 只读冻结原切块、原文及图片；完整覆盖资格与已确认跨父块断词上下文 |
| `embedding.py` | 原模型API、固定模板token计数、无截断检查、视觉输入、L2归一化与缓存 |
| `media.py` | 图片读取、OCR协议、失败原始输出、区域像素坐标、视觉父子块与派生图像 |
| `candidates.py` | 构造文本/原图/区域图/图文表示，生成独立来源身份与检索payload |
| `text_inputs.py` | 原检索文本的完整输入分片；片段位置明确属于retrieval_text，非原MD坐标 |
| `dedup.py` | 完全重复分组、MinHash/字符ngram候选，保留所有来源，不按近重复删除 |
| `importer.py` | 经元数据接口导入全部冻结来源，先写对象后登记外键关系 |
| `pipeline.py` | 迁移缓存/新向量化、只补缺口、BM25建索引、全量验证后发布 |
| `coverage.py` | 逐原生子块/OCR块/原图核对覆盖，重新计数并验证超长片段无缝覆盖 |
| `validation.py` | 快照和范围、视觉来源、SQLite成员/外键/内容指纹、逐向量内容及BM25成员/文本指纹核对 |
| `operations.py` | 状态、独立复核、真实接口探测、dense/BM25/RRF查询及父块回取 |
| `probe.py` | 少量真实图片Embedding协议探测，不用图片路径文本冒充视觉输入 |
| `backup.py` | 关闭连接后备份，逐文件SHA校验，恢复到新目录并重定位本地清单 |
| `cli.py` | 统一命令、互斥锁、错误脱敏；查询默认预览，可用--full回取全文 |

## 运行

在本目录的 PowerShell 中执行，已有独立 `.venv`：

```powershell
& '.\.venv\Scripts\python.exe' run.py status
& '.\.venv\Scripts\python.exe' run.py verify
& '.\.venv\Scripts\python.exe' run.py smoke
& '.\.venv\Scripts\python.exe' run.py query '燃气安全管理' --mode bm25 --limit 5
& '.\.venv\Scripts\python.exe' run.py query '如何加强燃气安全管理' --mode hybrid --limit 5
& '.\.venv\Scripts\python.exe' run.py query '残疾人保障' --mode hybrid --filter 'planid=36656' --full
```

支持 `dense`、`bm25`、`hybrid`。过滤字段为 `planid`、`area_name`、`area_code`、`period_key`、`document_type`、`source_type`、`representation_type`、`document_version_id`；多次 `--filter` 取交集，不根据问题自动猜地区或时期。RRF使用各路名次，`rrf_k=60`，不同分值不直接相加；同来源多表示在展示时去重。没有查询改写、重排模型或回答生成器。

迁入原工程当前发布版本（不连接旧数据库或模型）：

```powershell
& '.\.venv\Scripts\python.exe' run.py migrate --resume
```

重新扫描上游切块并真实处理新增输入：

```powershell
& '.\.venv\Scripts\python.exe' run.py build --sample --resume --allow-partial
& '.\.venv\Scripts\python.exe' run.py build --all --resume --allow-partial
& '.\.venv\Scripts\python.exe' run.py probe
```

`build`复用实际输入哈希匹配的向量和识别缓存，新增/改变的输入才发模型请求；固定空间探针仍会发少量请求。代码或配置变化会形成新的冻结批次和发布候选；旧发布不会自动删除。`--resume`说明续跑意图，相同身份的构建默认也会跳过已存point。已知缺图、识别失败保留在媒体报告；`--allow-partial`允许在有明确缺口标记的情况下发布，不能补造结果。

输入完整计数上限8192 token。超长检索输入分片但保留原父块与原子块身份；没有固定字符重叠，不在本工程重做父子切块。原始MD范围仍为完整解码文件的Unicode码点、左闭右开，保留CRLF。OCR范围属于识别转录，像素框属于原图，两者不得冒充原MD范围。

在其他机器新建环境时使用 Python 3.12：

```powershell
py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
Copy-Item secrets.example.json secrets.local.json
# 在本地填写模型地址和密钥；只做BM25查询与数据校验不需要模型凭证。
& '.\.venv\Scripts\python.exe' -m pytest tests -q --basetemp '.cache\tests-manual'
```

## 发布、备份与恢复

三个存储没有跨库事务。程序先持久化来源与任务，再写固定身份的向量、创建BM25索引，核对全部成员/数据后标记ready，最后在SQLite事务中校验revision并切换runtime指针。中断后原发布继续有效，重复写入相同point不会新增重复对象；校验失败不会发布。关闭Qdrant Local前明确执行WAL checkpoint；回写被阻塞则报错，不能继续宣称备份成功。

```powershell
& '.\.venv\Scripts\python.exe' run.py backup
& '.\.venv\Scripts\python.exe' run.py restore --archive '完整ZIP路径' --destination '尚不存在的恢复目录'
# 下面会额外生成完整备份与完整恢复副本，请按需要显式运行：
& '.\.venv\Scripts\python.exe' run.py restore-drill
```

备份包含源码、配置和数据，不含凭证或Python环境。恢复后可用本工程的Python环境执行恢复目录的`run.py verify`和`run.py query`，也可以在恢复目录重新建立环境；模型调用需在恢复目录补充凭证，纯关键词和校验不需凭证。向量缓存、tokenizer、原文快照和索引一并备份，不能仅复制`metadata.sqlite3`。不要复制正在打开的Local目录，也不要在资源管理器中绕开项目锁修改文件。

## 本地版的限制与空间

Qdrant Local为单进程本地精确向量搜索，不能当作Qdrant Server的HNSW、网络并发或服务端snapshot。47,477×4096个float32的纯向量约0.724GiB，打开时还存在矩阵容量、payload、Python对象和来源表等内存开销。BM25S索引采用mmap；关键词单路不加载dense库。后续面向多用户或更大语料，可实现相同接口切回服务端。

本机实测：本地存储合计约 **2.43GiB**（SQLite约0.66GiB、Qdrant Local约1.72GiB、BM25S约0.05GiB）；独立环境约0.42GiB。向量缓存按路径计约0.72GiB，90,966个缓存文件与原版共享硬链接，不能重复算成新增物理占用。以上为文件逻辑大小，不是NTFS精确分配空间。

**冷加载资源限制：** 重新打开完整库进行混合查询时，验收进程峰值工作集实测约 **9.76GiB**；这不是低内存实现。SDK会装载持久化向量并创建临时列表/数组。当前版本适合单机开发，不能把本地化理解为内存也一定较小。服务端HNSW、量化或修改向量精度均未在本轮引入。

Local持久化文件启用WAL，保留默认FULL同步，降低逐point回滚日志的文件开销；只调整日志模式，不直接读写Qdrant内部point表。这依赖已固定的qdrant-client 1.15.1目录布局，升级时必须复核适配器和备份恢复测试。

保留原工程和新工程会增加独立本地数据库的空间。硬链接降低快照/缓存重复占用，但资源管理器按文件长度相加会重复计数，不能当作新增物理占用。此轮不清理原数据库，也不自动制作多份全量备份。

官方组件依据：[Qdrant Client Local实现（固定1.15.1）](https://github.com/qdrant/qdrant-client/blob/v1.15.1/qdrant_client/local/qdrant_local.py)、[BM25S](https://github.com/xhluca/bm25s)。Qwen模型服务可能不公开服务端权重修订，固定输入探针只检查明显空间变化，不等同于服务端修订证明。

后续增删文件、变更入口或生成新正式批次时，必须同步更新本README的结构/状态，以及`outputs/README.md`的当前版本标记。
