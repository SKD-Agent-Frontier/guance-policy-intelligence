# RAG 检索工程

2026-10-24，工程0.4.0，API **proposal-v0.4**。保留文本V0.3，新增独立媒体类型。同步 `search/read` 现已支持原生文字、OCR和原图，并提供 `read_image` 获取原图/区域的真实图片数据。只开发RAG，Agent源码由同事接入。验证见[文本验收](验收报告.md)、[多模态验收](多模态验收报告.md)，接入要求见[多模态交接说明](../设计方案2.0/Agent对接说明/多模态交接说明.md)。

本工程直接读取相邻的 `向量化与存储工程（降级版）` 已发布产物，不重新切块、OCR或建库。默认混合三类来源；`source_types=["text_chunk"]` 可选择原先纯文本行为。回答生成、Agent运行内证据登记和图片展示由Agent接入。

2026-10-24整理GitHub目录后，正式三个工程仍保持同级；本工程、默认存储路径和正式发布未移动。安装与数据交付边界见[RAG总入口](../README.md)，不从已移除的归档PostgreSQL源码加载当前检索库。

## 启动与调用

在本目录使用已有、已锁定依赖的 Python 环境：

```powershell
$py = '..\向量化与存储工程（降级版）\.venv\Scripts\python.exe'
& $py .\run.py --mode bm25 search '养老服务' --area 苏州 --fyp 十四五 --top-k 8
& $py .\run.py --mode hybrid stats
& $py .\run.py --mode hybrid search '逝有所安重点工程' --area 江苏 --source-types visual_chunk image_occurrence
& $py .\run.py --mode bm25 read 48787 --chunk-id chunk_594a17c56902e7fdac2a38662282ebda --offset 6000
& $py .\run.py --mode hybrid serve
```

`bm25` 不调用向量模型；默认 `hybrid` 加载完整向量集合，并使用建库时的同一 tokenizer、指令、模型及输入协议。鉴权只读取建库侧现有私密配置，日志和返回值不带凭证或服务地址。查询向量优先使用经哈希/配置核验的缓存；新请求发送前核对既有空间探针，随后验证模型名、token 数和 4096 维归一化向量。不会下载模型权重。

单条 CLI 命令适合核查，每次会启动和关闭。**常驻调用使用一个共享 `RagKnowledgeBase`，或一直运行 `serve`。** `serve` 先输出 ready，再逐行接收如下 JSON，逐行返回 `status/message/data`；它是标准输入输出入口，无 HTTP 端口。

```json
{"operation":"search","arguments":{"query":"养老服务","area":"苏州","fyp":"十四五","top_k":8}}
{"operation":"read","arguments":{"planid":"48787","chunk_id":"chunk_594a17c56902e7fdac2a38662282ebda","offset":6000}}
{"operation":"stats"}
```

要从其他项目导入，可把本工程 `src` 加入其 Python 路径，或在**调用方环境**中执行 `python -m pip install -e <本工程目录>`。代码仍依赖相邻本地存储项目及其真实发布目录，不能只复制此 Python 包就获得语料和索引。依赖声明在 [pyproject.toml](pyproject.toml)，现有复现环境以建库工程依赖锁为准。

```python
from guance_rag import RagKnowledgeBase, RuntimeOptions

# 在应用启动阶段创建，ready 后再接收请求；退出时统一 kb.close()。
kb = RagKnowledgeBase(RuntimeOptions(mode="hybrid"))
hits = kb.search("养老服务", area="苏州", fyp="十四五", top_k=8, source_types=["text_chunk"])
page = kb.read(hits[0].planid, chunk_id=hits[0].chunk_id,
               document_id=hits[0].document_id,
               generation_id=hits[0].generation_id)
payload = page.model_dump(mode="json")
kb.close()
```

## 对接契约

```python
search(query, area=None, fyp=None, top_k=8, *, source_types=None) -> list[HitV03 | MediaHitV04]
read(planid, section_idx=None, max_chars=None, *, chunk_id=None, source_id=None,
     offset=0, document_id=None, generation_id=None) -> PassageV03 | MediaPassageV04
read_image(source_id, *, variant="original", document_id=None, generation_id=None) -> ImageDataV04
get_chunk_source(chunk_id, *, document_id=None, generation_id=None) -> PassageV03
verify_quote(chunk_id, quote, observed_ranges, *, document_id=None, generation_id=None) -> bool
verify_media_quote(source_id, quote, observed_ranges, *, document_id=None, generation_id=None) -> bool
stats() -> dict
close() -> None
```

- 不给章节、chunk或source_id返回目录；给章节返回一页章节正文；给chunk返回一页块原文和原章节；给媒体source_id返回OCR及原图信息。定位参数同时给出会校验归属。未知来源、错误版本或越界offset不返回空成功。
- `read` 默认及配置上限为 6000 码点；`offset` 是所选块/章节内部的偏移，按 `next_offset` 续读。页内 `chunks` 同时保存完整块范围、实际交集和 `complete`；新增的可选 `section_id/section_path` 保留子块自身章节，根目录或父章节读页不会把子块改归到根。
- `get_chunk_source` 为程序原文回看返回完整长块，不受 6000 字页预算限制；调用方不得把它直接无预算塞给模型。
- `HitV03.content` 最多 300 码点，保持连续原文。长块的检索表示若对应中间片段，只有通过检索视图哈希及原文连续后缀核对后才换算预览位置。
- 原生Hit和read的页内`chunks`均返回所属章节的`section_char_start/end`。按完整块检查`section_char_start <= chunk_char_start < chunk_char_end <= section_char_end`；恰好贴合边界合格，不能用300字预览或当前页代替完整块。越界保留内容及登记章节，并返回`chunk_outside_section`；按章读的顶层提示带`:chunk_id`，每个页内块保留自身章节范围和提示。Agent据此提醒“该块超出登记章节范围，可能跨章或归属异常”，不可直接认定为单章内容。原文/文档/父块真实性校验仍为硬校验；OCR坐标不参与此比较。
- `area` 对真实 `area_name` 做包含匹配；`fyp` 将十一五至十五五常用别名规范到 `period_key`，其他值精确匹配。缺元数据不满足硬条件。两路均在 Top-K 前过滤，每路同 source 保留最佳原始名次后按 `Σ1/(60+rank)` 融合。score 只表示排序值。
- 父块、补充 `parent_context` 和已解析的必要条件以独立 `contexts` 返回；同次 search 按来源范围去重。主 `content` 不混入标题前缀、条件或省略号。无法完整放入的上下文整体省略，并返回 `context_budget_exceeded:*`；未解决关联和原始 pending 状态保留质量提示。调用方须共同收集一批 Hit 的 contexts，不能把后续 Hit 的去重视为缺失来源。
- 默认 search 主内容＋contexts 总预算 16000 码点，每条上下文预算 6000；read 主内容与 contexts 各受独立上限。这里控制的是字符，不是回答模型的 token，Agent 仍需按实际模型预算装配并保留不足提示。
- 异常独立定义为 `InvalidArgument/KBNotFound/KBNoText/RetrievalTimeout/BackendError`，分别对应 `invalid_argument/not_found/no_text/timeout/backend_error`；无搜索命中返回 `[]`。Agent 需映射类型和异常、透传关键字参数，并扩展其证据登记；当前旧 Agent 不能仅切换配置即接入。
- `verify_quote` 只在调用方提供的已见范围内合并重叠或相邻范围，再逐字校验原句。**它不维护 Agent run 的证据记录，也不证明语义结论正确。** 历史回看依赖原发布清单、旧对象及快照仍被保存；测试包含历史版本隔离，当前生产库只有一个发布基线。

## 图片和OCR

`source_types` 支持 `text_chunk/visual_chunk/image_occurrence`，省略表示三类；旧Agent接入前可显式仅选text_chunk。原生文字和OCR走BM25与dense，原图及区域图/图文表示走dense。纯BM25模式不会产生原图向量命中，但仍可按已知图片来源ID读图。每路先按来源ID归一，再RRF；相同OCR块的文字、区域图及图文表示不会重复累计同路得分，各文档图片出现位置仍独立保留。

媒体命中以 `source_id` 标识，不编造原生 `chunk_id`。用 `read(planid, source_id=...)` 取得OCR页、原图引用、像素区域和识别状态；用 `read_image(source_id, variant="original"|"region")` 获取经哈希核验的图片 `data_url`，可由Agent传给支持图片的模型。搜索/普通read不携带大段base64。默认单张原图及返回图片上限20MiB，可通过RuntimeOptions调整。

```python
hits = kb.search("逝有所安重点工程", area="江苏", source_types=["visual_chunk", "image_occurrence"])
hit = hits[0]
page = kb.read(hit.planid, source_id=hit.source_id,
               document_id=hit.document_id, generation_id=hit.generation_id)
image = kb.read_image(hit.source_id, document_id=hit.document_id,
                      generation_id=hit.generation_id)
# image.data_url是实际图片数据；把路径或JSON当普通文字发送不会让模型看到图片。
# OCR长页按page.next_offset继续read同一source_id；区域图只适用于visual_chunk来源。
```

媒体API的 `contract_version=proposal-v0.4`；原生Hit/Passage保留V0.3正文语义。媒体不填写原生char_start/end或chunk_char_start/end。`ocr_range` 使用完整识别转录串内的码点、左闭右开，保存父转录ID/哈希及完整范围与本页范围；`reference_span` 单独表示原文中的图片Markdown引用。部分图片仅有已验证的文档目录关联，reference_span/section为空，association_basis和质量提示明确这一缺口，不猜页码或正文位置。

`image.bbox_pixels` 已是原图像素，上游 `coordinate_basis=model_estimated_normalized_1000` 说明模型框的来源；上游已经做过归一化到像素换算，此处禁止再乘尺寸/1000。接口另给 `coordinate_unit=original_image_pixels`，裁剪仅使用原像素范围，原图字节不改。模型估计的框不等于人工精确标注。

`HitV03/PassageV03.media_sources` 是原文范围内的已发布图片ID；目录响应还列出仅关联文档的图片，并明确reference_span=null。它们是回取入口，不代表模型已经看过图片。OCR保留不确定状态、标题上下文和识别版本，原图OCR不可用时仍可读图。`verify_media_quote`只在已见OCR范围内逐字核验，不批准图片alt文本，也不证明识别准确或语义成立。

### 媒体命中后从哪里回取

**图片/OCR证据按媒体来源回取，章节和Markdown图片标记都是可选的定位信息。** 内容来源如下：

| 命中或请求 | 实际内容来源 | 回取方式 |
|---|---|---|
| 原生文字块 | 冻结Markdown正文 | `read(planid, chunk_id=...)` |
| OCR片段或整图的识别文字 | 同版识别父转录及其独立OCR范围 | `read(planid, source_id=...)`；按`next_offset`续读 |
| 原图或OCR区域图 | 冻结图片资产；区域图按已存像素框裁剪 | `read_image(source_id, variant="original"或"region")`；region仅适用于visual_chunk |

媒体内容无需从Markdown正文切片取得；运行时仍校验所属文档、发布版本和来源身份，当前工程仍需保留冻结Markdown及元数据。OCR是派生识别文字，原图才是图片证据的原始内容。

对仅能确认文档归属、无法定位章节的媒体，按现有proposal-v0.4执行：

- 搜索命中后固定`source_id/document_id/generation_id`，直接使用上表的媒体接口。**无章节信息不会阻止OCR或原图回取。**
- `read`不传`section_idx`，也不以`read(planid)`的目录响应替代媒体内容；显式传入章节时仍会校验归属。
- `reference_span=null`、`section_id=null`、`section_idx=null`、`section_path=""`；保留`association_basis=verified_document_directory`和`document_only_image_association`提示，不按文件名推断章节或页码。
- 引用可显示“《政策标题》中的图片／识别文字（章节未定位）”，并保留媒体ID、原图哈希和识别版本。Agent引用校验不能以章节为空拒绝这类已验证文档归属的媒体，也不能将其伪装成Markdown原句。
- OCR不可用时仍可`read_image`；此时`content`可能只是图片alt或空串，应以`content_basis/recognition_status`区分，不能当作完整图片原文。

当前有422个仅关联文档的图片来源，连同派生OCR片段共2359个媒体证据单元；2359不是不同图片张数。这些是可回取的文档级媒体证据，章节细化可在后续取得可靠原始版面对应关系时补充。

### 当前图片与OCR数据缺口

以下为2026-10-24核对的当前revision=1发布限制；本轮只补充说明，未补图或重跑OCR，发布仍保留`partial=true`。

| 缺口 | 数量和原因 | 当前能力与影响 |
|---|---|---|
| 缺失图片引用 | 285处Markdown图片引用未登记到对应图片资产；按引用次数统计 | 保留原引用及缺失状态，无法取得这些引用对应的图片/OCR；需补文件或核对路径 |
| OCR失败 | 11个不同图片资产：9个模型输出因长度上限截断，2个返回无法解析的JSON；影响26个图片出现位置 | 原图仍可检索和回取，这些位置没有可用OCR，不将残缺响应当完整转录 |
| 不确定OCR | 56个OCR子块为pending，含小字、模糊数字、遮挡等识别不确定内容 | 保留已识别文字、原图和质量提示，文字及数字需对照原图核验 |

计数口径分别是“引用”“图片资产”“OCR子块”，不能相加作为缺失图片总数。来源记录与状态见[存储工程说明](../向量化与存储工程（降级版）/README.md)，媒体回取实测见[多模态验收报告](多模态验收报告.md)。

## 坐标与版本

原生文字继续使用`contract_version=proposal-v0.3`，`offset_basis=body_first_non_whitespace`，`offset_unit=unicode_codepoint`。跳过元数据及正文前空白，以正文首字符为 0；之后所有字符保留，包括空格、CRLF、Markdown 和非 BMP 字符。坐标为 0 起点、左闭右开。`body_origin` 计算及缓存一次，内部数据库和原始 ID 不变。

Agent接入原生文本时须遵守：

- Agent不得自行读取原始Markdown计算坐标或重新清洗正文；原文统一通过`kb_read`（调用RAG同步`read`）取得，直接使用RAG返回的坐标。
- `content`、`body_origin`、`char_start/end`及来源/版本信息须成组取自同一次响应；不得拿其他响应的原点或范围解释当前`content`，也不得重复换算坐标。分页续读仍按`next_offset`规则处理。

下例仅用于RAG侧核验原文一致性，不是Agent自行打开Markdown的接入步骤。

```python
body = frozen_md_bytes.decode("utf-8")[body_origin:]
assert result.content == body[result.char_start:result.char_end]
```

`chunk_char_start/end` 是完整块范围，`char_start/end` 是本次 content 范围。原文件位置等于正文坐标加 `body_origin`。`section_idx` 按 `(start, level, id)` 冻结排序，只在对应发布、文档目录内有效；真实身份是 `section_id`。来源同时携带 `document_id/document_version_id/source_hash/generation_id`。不要以向量 ID 替换 `chunk_id`，也不要把 JavaScript UTF-16 下标直接当码点。

## 共享与资源

构造函数在专用库线程中固定 active generation，校验并加载 SQLite 来源映射、506 份冻结快照、目录、媒体映射、490份原图哈希/尺寸、BM25 和所需 Qdrant 集合，完成后才返回。后续请求共用一个运行时，库操作经有界队列串行执行；SQLite 连接从创建、使用到关闭始终属于同一线程。可以从多个线程或 `asyncio.to_thread` 调用公开方法。

共享索引不共享请求临时状态：入队时深复制参数，包括 `source_types` 和嵌套的 `observed_ranges`；每次检索独立创建过滤集合、各路候选、RRF分数、上下文去重集合和字符预算，并新建返回结果。修改某次结果不会影响后续请求。调用方若同时修改参数，应在调用前自行同步；运行时保证入队后的快照隔离。Agent仍须按run独立保存证据和会话状态。

当前排序仅为RRF融合，没有独立重排模型。BM25分词器、Qdrant客户端、查询编码器、向量缓存、来源对象LRU及统计计数均由同一库线程访问，不对请求线程暴露。以后接入重排时，候选排序和中间张量仍须按请求独立；共享重排器应留在该线程，或先确认其并发安全并保护可变状态，再开放并行推理。

默认队列容量 32、排队 20 秒、每任务执行等待 30 秒、启动 180 秒、关闭 60 秒，可通过不可变 `RuntimeOptions` 调整。排队任务可以取消；已经运行的底层计算无法强行取消，等待超时后仍由库线程持有资源直到真正结束。关闭停止接收请求并收尾，超时会明确报告尚未释放。参考 [Python Future 的取消语义](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel)。

持有既有项目锁和 Qdrant 路径锁；同目录只允许一个运行时，不删除锁，不让多个 Web worker 各自打开同一 Local 库。更新发布需受控关闭后重开。SQLite 使用 `mode=ro`、`query_only` 和固定读事务；源文件、切片、发布成员与索引业务数据不回写。SDK 锁/WAL 维护可能产生文件系统写入，查询缓存仅写本工程 `.cache`。

整库 Qdrant Local 会冷加载大量向量，须预留内存；本次实测见验收报告。并发调用共享内存，但首轮检索执行串行，不能据此承诺吞吐量或多进程服务能力。

进程内共享不等于跨进程共享：多个服务进程各自创建运行时，会分别加载索引、客户端和缓存并占用内存；BM25的mmap也不代表全部运行时对象共享。当前同一路径的第二个进程会被项目锁拒绝，不能通过增加Web worker数扩容。未来多进程部署须先明确唯一的索引持有服务或独立索引副本及其内存预算，并设计跨进程调用；当前stdio入口没有提供该服务能力，也不能把已启动的线程运行时经fork继承使用。

## 工程与验证

```text
src/guance_rag/  类型、只读来源层、双路召回、业务适配、运行时、CLI
tests/          小型真实 SQLite/BM25/Qdrant 产物及生命周期回归
scripts/acceptance.py  当前完整发布验收（只读，可调用真实向量服务）
scripts/media_acceptance.py  全量媒体读取、OCR查询与原图向量召回/原图及区域验证
验收报告.md / 验收结果-*.json  本次实际结果
outputs/        本地复验原始输出，忽略提交
.cache/         分词、问题向量、pytest 临时产物，忽略提交
```

```powershell
& $py -m pytest -q --basetemp=.cache/pytest-tmp
& $py .\scripts\acceptance.py --mode bm25 --output .\outputs\acceptance-bm25.json
& $py .\scripts\acceptance.py --mode hybrid --output .\outputs\acceptance-hybrid.json
& $py .\scripts\media_acceptance.py --mode bm25 --output .\outputs\media-acceptance-bm25.json
& $py .\scripts\media_acceptance.py --mode hybrid --output .\outputs\media-acceptance-hybrid.json
```

临时测试库位于本工程 `.cache` 下；测试不读取鉴权、不调用模型。完整库验收会读取真实快照和索引，hybrid 模式在缓存未命中时发送测试查询到已配置服务。核对加载次数、并发请求、真实样例、全部超长块分页重建和数据库文件哈希；这些结果不等同于端到端问答准确率。

并发回归还覆盖入队后修改来源过滤/嵌套已见范围、20次交错查询与返回结果修改，以及第二个服务进程被本地库锁拒绝；用于验证请求隔离和单进程共享边界。
