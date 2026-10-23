# 向量化与存储工程：原版切块输入

本目录与兄弟目录[向量化与存储工程（开源组件版）](../向量化与存储工程（开源组件版）/README.md)分别接入两份已冻结的切块结果，均依据[向量化与存储方案2.0](../设计方案2.0/向量化与存储设计方案.md)。两版使用**相同模型、向量化代码、参数和存储规则**，区别是上游切块、PostgreSQL schema、Qdrant collection及发布入口。

用户已确认：本轮包括文本、图片识别、语义切块、原图/区域映射、多模态向量化和存储。这里不增加独立重排模型或在线问答系统，不把OCR识别结果冒充Markdown原句。

## 当前状态

2026-10-23已按完整覆盖规则补录并激活：**506篇文档，42,516个原始文本子块全部入库，47,477个真实向量点**。17个超长子块形成52段完整覆盖的输入，因此原生文本对应42,551条向量。逐点对账及5篇来源/父块回取通过，共享实现35项离线回归通过。历史基线已有真实备份恢复验证，本次补录未重复整库恢复演练，证据分别记录。详见[验收报告](验收报告.md)、[验收摘要](验收摘要.json)、[实施记录](实施记录.json)及[最新批次](outputs/README.md)。

现有内容的向量覆盖已完成：3,562个已有识别子块（含56个不确定块）及919处现存原图出现均有向量。490张唯一图片中，479张有可解析转录，11张OCR未完成；后者仍有原图向量。原有285处缺图引用没有实际文件，无法生成图像向量。pending及质量提示继续保留，不等于尚未向量化，也不宣称OCR文字均已核实，见[图片识别质量清单](待处理图片.md)。

本次复用45,337个缓存向量，新增计算146个唯一输入向量。**模型计算是增量的；存储采用新集合全量写入和核对，再切换发布入口，并非直接向旧集合追加差额。** 保留旧批次的代价是补录耗时较长，不能把本流程称为纯增量入库。

已取得的真实接口证据：

- 用户提供的服务公布`qwen3-vl-embedding-8b`；两条测试文本实际返回4096维、接近L2归一化的向量。
- 显式官方模板的测试字符串被服务计为29 token，本地为28；改为提交同一模板的token ID后，服务计数为28。正式文本链路因此使用token ID，并逐批核对总数，不能按字符串路径的结果假称模板一致。
- 服务支持base64 float32输出，单条向量为16384字节；客户端再做维度、有限值、非零及L2检查。
- `qwen3.8-27b`已通过实际图片识别探测，已实际处理全部490份唯一图片，合格识别块及所选图像表示已完成向量化；同字节的969个物理文件保留各自路径和来源身份。
- 实际图像Embedding协议已验证：原图1700×2400，按固定规则处理为832×1216；完整模板与图像共1017 token，本地预算与服务usage一致，返回4096维向量。图片通过messages/image_url发送，不能用链接文本的向量代替。
- 服务未暴露权重revision；profile如实记录未知，并用固定探针检查明显的向量空间变化。客户端不能证明服务端权重或processor配置未被运营方更换。

本机Docker Desktop因自身Inference manager启动错误退出。项目已安装并启动官方便携PostgreSQL 16.13与Qdrant 1.19.2，不重置Docker，两者只监听回环地址的项目专用端口15432、16333。两套schema均已建立，中文、非BMP字符和CRLF数据库往返验证通过。PostgreSQL工具遇到中文路径编码问题时使用经目标核验的ASCII目录入口，实际数据仍保存在工程内，不修改系统语言。

依赖下载曾出现SHA-256不符，未跳过校验；现已只读复制既有依赖到独立环境，再用与缓存PyPI索引SHA-256匹配的wheel离线补齐新增依赖。25项离线测试与pip check通过，详见依赖来源记录。自动审批额度的历史阻塞已于2026-10-22 23:06后解除。

## 两版隔离

| 项目 | 本目录 | 开源组件输入版 |
|---|---|---|
| 上游 | 切块工程 | 切块工程（开源组件版） |
| PostgreSQL schema | rag_legacy | rag_oss |
| 知识库身份 | guance_legacy_text | guance_oss_text |
| Qdrant collection前缀 | guance_legacy | guance_oss |
| 配置/输出/当前批次 | 本目录独立保存 | 兄弟目录独立保存 |
| 实现、凭证、模型计算缓存 | 本目录集中维护 | 只读调用共享实现 |

PostgreSQL与Qdrant进程可共享，但来源成员、关系、collection和active generation不混用。相同实际模型输入+相同profile只复用计算，各政策、图片出现及表示保留独立ID。整个构建持有共享文件锁，一次一个worker。

## 当前文件结构与作用

```text
向量化与存储工程/
├─ README.md                  # 范围、当前状态、文件作用、运行与维护说明
├─ AGENTS.md                  # 只读上游、真实验证、凭证与README维护约定
├─ config.json                # 原版输入、schema、模型、8192上限、批量与去重参数
├─ 当前批次.json              # 已激活generation、collection、覆盖状态及统计
├─ run.py                     # 原版命令入口
├─ runtime_setup.py           # 官方便携数据库下载、校验、初始化与回环地址启动
├─ runtime_check.py           # 只读检查两版PG指针、point计数及Qdrant优化器健康
├─ runtime_profile.json       # 实际安装版本、下载来源、字节数及SHA-256
├─ report_status.py           # 从真实产物更新实施记录与批次时间索引
├─ report_delivery.py         # 从完成双库验收的正式批次生成报告，不运行模型
├─ 验收摘要.json/验收报告.md   # 实际统计与全量校验/备份恢复证据入口
├─ 待处理图片.md              # 按原图列出的失败及不确定内容入口
├─ 双版实施对照.md            # 两版实际数量对照，不代表检索质量评价
├─ schema.sql                 # 有来源/父子/表示外键、任务与发布状态的PG结构
├─ requirements.txt           # 固定直接依赖；不包含模型权重
├─ requirements.lock.txt      # 当前实装独立环境的完整依赖版本
├─ 依赖来源.json              # 复制起点、实装版本及新增wheel的SHA核验来源
├─ 接口探测记录.json          # 已实际运行的少量接口探测；明确未验证项
├─ secrets.example.json       # 无真实密钥的配置示例
├─ secrets.local.json         # 已配置的本地凭证，Git忽略，勿复制到报告
├─ 实施记录.json              # 实际执行、未完成项和阻塞的机器记录
├─ vector_store/
│  ├─ __init__.py             # 共享包说明
│  ├─ common.py               # 规范JSON、确定性身份、原子文件、范围与脱敏
│  ├─ prepare.py              # 冻结输入/源字节/媒体，质量标签作提示，补完整父级上下文
│  ├─ text_inputs.py          # 超长检索输入连续分片；保留码点范围，不改父块和原始子块
│  ├─ coverage.py             # 逐子块/原图核对覆盖；检查分片无缺口并独立重编码
│  ├─ embedding.py            # 本地tokenizer、真实API、向量检查与共享缓存
│  ├─ probe.py                # 全量OCR之前先验证真实图像Embedding协议
│  ├─ media.py                # 真实视觉转录、bbox映射、派生块及图文输入预算
│  ├─ candidates.py           # OCR任务、独立表示、payload、图像/图文选择
│  ├─ dedup.py                # 精确正文组、MinHash/LSH候选与关键差异记录
│  ├─ importer.py             # 来源、关系、媒体及派生对象导入有外键的PG表
│  ├─ storage.py              # PG事务/发布指针、Qdrant建库、写入与遍历
│  ├─ validation.py           # 全文覆盖、范围、计划成员/PG/point/payload/向量对账
│  ├─ pipeline.py             # 单worker构建、恢复、缓存、失败与发布
│  ├─ operations.py           # 入库验证、来源/父块回取、备份与真实恢复演练
│  └─ cli.py                  # prepare/build/verify/smoke/备份/恢复/回滚/status
├─ tests/test_core.py         # 离线回归；不冒充模型或数据库集成
├─ outputs/README.md          # 每个准备/识别/向量批次的时间、用途与正式入口
├─ .gitignore                # 凭证、环境、缓存、运行数据和大体积输出排除规则
├─ .gitattributes            # 源码/配置/依赖保持已验证字节，防止Git换行改变指纹
├─ .venv/                    # 独立Python环境；25项测试及依赖检查通过
├─ .runtime/                 # 官方二进制、项目专用数据库、日志；Git忽略
└─ .cache/                   # 测试、共享OCR/向量/派生图片与构建锁；Git忽略
   ├─ ocr/                   # 通过校验的完整识别响应及解析区域
   ├─ ocr-attempts/           # 新调用在解析前保留的原始响应，包括截断/无效JSON
   ├─ ocr-failures/           # 同图片同profile的无效响应证据，避免两版重复调用
   ├─ vectors/               # 实际向量缓存（具体目录以embedding.py为准）
   ├─ service_calls.jsonl     # 实际请求摘要、状态及usage，无密钥、无请求正文
   └─ development-runs/       # 归档的本任务开发准备批次，不作为正式入口
```

`当前批次.json`已生成；PostgreSQL的active_generation_id为权威，文件提供本地入口。准备批次不等于已发布向量库。完整依赖锁来自实际环境；迁移需保留两版上游冻结产物及其已核验的7份tokenizer文件，不能只复制本目录代码。

## 输入资格及来源保护

只读上游当前正式批次，使用完整`chunks.jsonl`，不再仅使用`usable_chunks.jsonl`。当前配置`coverage_policy=all_existing_content`落实用户2026-10-23的完整覆盖要求：标题、日期、子块断词、未解析引用和上游pending不阻止向量化，原始状态/原因保留为提示。空正文没有原始子块，不生成虚构文本。来源字节、Unicode码点位置、父子归属和输入指纹仍必须一致。

不重切上游父子块。原有质量排除改为资格清单中的提示。罗甸县已确认“确/保”同时跨父块，因此每版额外生成一个完整父级上下文，记录在`prepared_*/parent_contexts.jsonl`并存入PG；相关表示的payload.parent_id指向完整上下文，original_parent_id保留原父块身份。原始父块/子块数据不覆盖。

超过8192 token的检索输入由`text_inputs.py`完整分片，优先使用换行和句末；单行超长时按Unicode码点细分，不丢字、不静默截断。分片不是新的原生子块，原父块保留完整内容，每个分片仍关联原子块和父块。`embedding_fragment`下标明确属于retrieval_text视图，不能冒充Markdown位置。`coverage.py`逐原生子块、视觉子块及现存原图核对，并独立核验分片范围、哈希和实际token输入，所以向量记录数可大于原始子块数。

媒体保留每个物理路径；相同字节共享asset与计算。缺图不补造。无Markdown锚点但路径明确属于某文档的资源登记`document_only`，页码未知；同图49个文件不称为49页。源图、裁剪图、OCR转录与Markdown位置采用不同字段。

## 图片与表示

视觉模型按完整段落、整表、图表和公式转录，保留表题/表头/单位/附注等。JSON、输出完成标志和原图bbox必须通过检查；`□`等不确定文字保留pending，但已有转录仍向量化并带提示。OCR失败但原图存在时仍生成原图向量，不据此宣称文字识别成功。bbox明确标为模型估计，不能称为人工或像素级真值。

图像分支保留原图向量；合格识别区域可有文本向量，表格/图表/公式按需要增加带标题上下文的区域图文表示。空白/无法识别图片不冒充证据。超8192的完整单元待处理，不截断单元格，也不猜补表格。图像确实以image_url内容提交，并检查完整视觉token预算；接口探测已通过，正式每个请求仍独立验证。

视觉请求最多并发2条，结果按来源顺序持久化；构建及共享缓存仍由一个worker持锁。新调用先保存原始响应再解析，length失败不得当作完整转录，并保留待处理证据。截断或非法JSON等无效响应在同图片同识别profile下不自动重复消耗请求；显式配置`vision_retry_incomplete=true`才重试，或修改输出上限产生新profile。8张旧作业已证实超限图片只保留实际请求哈希及失败摘要，没有伪造已丢失的响应正文；其他格式异常经过一次重试，恢复2张，仍失败的保存了原始响应。

`representation_id`包含来源版本、对象、occurrence、模态、真实输入和profile；point使用确定性UUID。向量缓存key为profile+实际输入。近重复只留候选及数字/单位/否定差异，不删除来源，不复用不同输入。

## 运行命令

下列入口使用已安装的独立环境；重启电脑后先启动项目数据库。

```powershell
# 在本目录运行；只连接用户指定模型服务，不下载8B权重。
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -X utf8 runtime_setup.py install
.\.venv\Scripts\python.exe -X utf8 runtime_setup.py start
.\.venv\Scripts\python.exe -X utf8 runtime_check.py
.\.venv\Scripts\python.exe -X utf8 -m pytest tests -q --basetemp .cache/test-temp-<新批次名>

# prepare完全离线：冻结、核验来源和资格；不生成向量，不写入数据库。
.\.venv\Scripts\python.exe -X utf8 run.py prepare --sample
.\.venv\Scripts\python.exe -X utf8 run.py prepare --all
.\.venv\Scripts\python.exe -X utf8 run.py dedup --all
.\.venv\Scripts\python.exe -X utf8 run.py probe

# build实际调用视觉与Embedding并写入两库；同输入恢复会复用缓存。
.\.venv\Scripts\python.exe -X utf8 run.py build --sample --allow-partial
.\.venv\Scripts\python.exe -X utf8 run.py build --all --allow-partial
.\.venv\Scripts\python.exe -X utf8 run.py verify
.\.venv\Scripts\python.exe -X utf8 run.py smoke
.\.venv\Scripts\python.exe -X utf8 run.py backup
.\.venv\Scripts\python.exe -X utf8 run.py restore-drill
.\.venv\Scripts\python.exe -X utf8 run.py status
.\.venv\Scripts\python.exe -X utf8 run.py document --planid 31629
```

`--fault-after N`仅用于缓存封存后的中断演练，再以相同参数去掉故障注入恢复。`--allow-partial`只允许报告中明确记录的视觉待处理/表示排除；Embedding失败仍阻止发布。未加该参数时，新增视觉缺口会阻止批次激活。

当前完整覆盖模式不再因上述质量标记排除现有内容，`--allow-partial`仅如实容纳缺图与OCR尚未完成等状态；现存图片即使OCR失败仍有原图向量。旧正式批次与开发快照均保留，新的正式入口以当前批次.json为准。

回滚命令为`run.py activate --generation <旧generation> --expected-revision <当前revision> --reason <原因>`：先重新核验目标批次，再在PG短事务切指针。固定point ID可幂等写入；先全量对账再ready，生效入口只在PG，不另外切Qdrant alias。

### 本机Windows运行记录

全量导入后曾出现Qdrant后台segment重命名“拒绝访问”，成员、payload及向量校验仍通过，但优化器为red。仅重启本工程Qdrant后，两版均恢复green/ok，并再次通过point计数、PG指针及5篇父块回取检查。上游有同类[Windows原生运行问题记录](https://github.com/qdrant/qdrant/issues/10806)；本机没有修改防病毒设置，也没有删除索引文件。

2026-10-23本次补录期间记录到一次连接超时，并检测到相同的优化器重命名错误。使用现有脚本核验PID及可执行文件后，仅重启本工程Qdrant；两套正式集合恢复green/ok，计数一致。开源组件版沿用原generation和缓存恢复，不因中断创建另一套向量身份。

之后连接超时再次出现。回归与实际路由检查确认：HTTPX读取Windows系统代理时，127.0.0.1也会进入代理连接池。共享客户端已设置trust_env=False直连本机，并仅对可重放的固定ID写入和只读翻页增加有限重试；参数/鉴权错误不重试。该修复对应新的实施代码指纹，开源组件版重新构建独立批次并复用已有向量缓存，旧失败批次保留。原版已完成批次的19份实际执行源文件另存code_snapshot，哈希逐一吻合其原始清单；向量输入规则没有变化。

后续增量写入后运行`runtime_check.py`；其结果记录在实施记录的current_runtime_check。若再遇到同类错误，可运行`runtime_setup.py restart-qdrant`后重新检查。该命令先核验PID最终exe路径，只重启本工程进程。`indexed_vectors`略小于point数不必然是错误：小于阈值的segment可采用精确扫描，依据[官方说明](https://qdrant.tech/documentation/search-tuning/before-tuning-a-qdrant-collection/)结合green/ok状态判断。健康检查不代替全量`verify`，本轮未执行生产负载或标注问题集的检索质量测试。

## 批次产物与后续衔接

- `prepared_*`：原始快照、上游表、`eligibility.jsonl`、真实媒体去向和清单；是准备结果。
- `media_*`：按内容哈希冻结的视觉提取、派生父子块、失败与排除清单；恢复命中数不参与身份。
- `generation_*`：模型profile、表示清单、精确/近重复记录、缓存及调用统计、失败项、两库对账和备份/恢复记录。
- `generation_*/code_snapshot`：部分历史批次在后续网络修复前保存的实际执行源码及哈希，用于重现其旧实现；不表示缺少代码快照的当前批次没有源代码，当前实现随Git本地提交保存。
- `lexical_members.jsonl`只为后续BM25确定同批文本成员，本轮没有构建BM25或在线问答。
- 正式批次指针仅在两库成员/向量/来源验证通过后生成。自命中和父块回取是存储连通性检查，不是检索准确率。

## 维护记录

- 2026-10-22至23：完成共享实现及两版独立存储；25项回归、全量来源/向量对账、真实中断恢复、父块回取及备份恢复通过。两版已激活，覆盖缺口明确记录。后续文件或状态变化，同轮更新本README与批次索引。
- 2026-10-23补录：两版全部原生子块、已有识别子块和现存原图向量化；超长输入完整分片，每版补充1个完整父级上下文。35项回归及两版逐点核对通过。另修复系统代理影响本地Qdrant连接的问题，保留旧批次与代码快照；本轮未重复整库恢复演练。
