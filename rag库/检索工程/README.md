# RAG 检索工程

2026-10-24，契约 **proposal-v0.3**。独立实现同步 `search/read`、BM25＋dense＋RRF、正文坐标、目录与分页、原文回取和共享运行时。只开发 RAG；Agent 源码由同事接入。验证结果见 [验收报告](验收报告.md)。

本工程直接读取相邻的 `向量化与存储工程（降级版）` 已发布产物，不重新切块或建库。首轮限定原生 `text_chunk`；图片、OCR、回答生成及 Agent 的运行内证据登记不在本工程内。

## 启动与调用

在本目录使用已有、已锁定依赖的 Python 环境：

```powershell
$py = '..\向量化与存储工程（降级版）\.venv\Scripts\python.exe'
& $py .\run.py --mode bm25 search '养老服务' --area 苏州 --fyp 十四五 --top-k 8
& $py .\run.py --mode hybrid stats
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
hits = kb.search("养老服务", area="苏州", fyp="十四五", top_k=8)
page = kb.read(hits[0].planid, chunk_id=hits[0].chunk_id,
               document_id=hits[0].document_id,
               generation_id=hits[0].generation_id)
payload = page.model_dump(mode="json")
kb.close()
```

## 对接契约

```python
search(query, area=None, fyp=None, top_k=8) -> list[HitV03]
read(planid, section_idx=None, max_chars=None, *, chunk_id=None,
     offset=0, document_id=None, generation_id=None) -> PassageV03
get_chunk_source(chunk_id, *, document_id=None, generation_id=None) -> PassageV03
verify_quote(chunk_id, quote, observed_ranges, *, document_id=None, generation_id=None) -> bool
stats() -> dict
close() -> None
```

- 不给章节或 chunk 返回目录；给章节返回一页章节正文；给 chunk 返回一页块原文和原章节。两者同时给出会校验归属。未知来源、错误版本或越界 offset 不返回空成功。
- `read` 默认及配置上限为 6000 码点；`offset` 是所选块/章节内部的偏移，按 `next_offset` 续读。页内 `chunks` 同时保存完整块范围、实际交集和 `complete`；新增的可选 `section_id/section_path` 保留子块自身章节，根目录或父章节读页不会把子块改归到根。
- `get_chunk_source` 为程序原文回看返回完整长块，不受 6000 字页预算限制；调用方不得把它直接无预算塞给模型。
- `Hit.content` 最多 300 码点，保持连续原文。长块的检索表示若对应中间片段，只有通过检索视图哈希及原文连续后缀核对后才换算预览位置。
- `area` 对真实 `area_name` 做包含匹配；`fyp` 将十一五至十五五常用别名规范到 `period_key`，其他值精确匹配。缺元数据不满足硬条件。两路均在 Top-K 前过滤，每路同 source 保留最佳原始名次后按 `Σ1/(60+rank)` 融合。score 只表示排序值。
- 父块、补充 `parent_context` 和已解析的必要条件以独立 `contexts` 返回；同次 search 按来源范围去重。主 `content` 不混入标题前缀、条件或省略号。无法完整放入的上下文整体省略，并返回 `context_budget_exceeded:*`；未解决关联和原始 pending 状态保留质量提示。调用方须共同收集一批 Hit 的 contexts，不能把后续 Hit 的去重视为缺失来源。
- 默认 search 主内容＋contexts 总预算 16000 码点，每条上下文预算 6000；read 主内容与 contexts 各受独立上限。这里控制的是字符，不是回答模型的 token，Agent 仍需按实际模型预算装配并保留不足提示。
- 异常独立定义为 `InvalidArgument/KBNotFound/KBNoText/RetrievalTimeout/BackendError`，分别对应 `invalid_argument/not_found/no_text/timeout/backend_error`；无搜索命中返回 `[]`。Agent 需映射类型和异常、透传关键字参数，并扩展其证据登记；当前旧 Agent 不能仅切换配置即接入。
- `verify_quote` 只在调用方提供的已见范围内合并重叠或相邻范围，再逐字校验原句。**它不维护 Agent run 的证据记录，也不证明语义结论正确。** 历史回看依赖原发布清单、旧对象及快照仍被保存；测试包含历史版本隔离，当前生产库只有一个发布基线。

## 坐标与版本

`contract_version=proposal-v0.3`，`offset_basis=body_first_non_whitespace`，`offset_unit=unicode_codepoint`。跳过元数据及正文前空白，以正文首字符为 0；之后所有字符保留，包括空格、CRLF、Markdown 和非 BMP 字符。坐标为 0 起点、左闭右开。`body_origin` 计算及缓存一次，内部数据库和原始 ID 不变。

```python
body = frozen_md_bytes.decode("utf-8")[body_origin:]
assert result.content == body[result.char_start:result.char_end]
```

`chunk_char_start/end` 是完整块范围，`char_start/end` 是本次 content 范围。原文件位置等于正文坐标加 `body_origin`。`section_idx` 按 `(start, level, id)` 冻结排序，只在对应发布、文档目录内有效；真实身份是 `section_id`。来源同时携带 `document_id/document_version_id/source_hash/generation_id`。不要以向量 ID 替换 `chunk_id`，也不要把 JavaScript UTF-16 下标直接当码点。

## 共享与资源

构造函数在专用库线程中固定 active generation，校验并加载 SQLite 来源映射、506 份冻结快照、目录、BM25 和所需 Qdrant 集合，完成后才返回。后续请求共用一个运行时，库操作经有界队列串行执行；SQLite 连接从创建、使用到关闭始终属于同一线程。可以从多个线程或 `asyncio.to_thread` 调用公开方法。

默认队列容量 32、排队 20 秒、每任务执行等待 30 秒、启动 180 秒、关闭 60 秒，可通过不可变 `RuntimeOptions` 调整。排队任务可以取消；已经运行的底层计算无法强行取消，等待超时后仍由库线程持有资源直到真正结束。关闭停止接收请求并收尾，超时会明确报告尚未释放。参考 [Python Future 的取消语义](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel)。

持有既有项目锁和 Qdrant 路径锁；同目录只允许一个运行时，不删除锁，不让多个 Web worker 各自打开同一 Local 库。更新发布需受控关闭后重开。SQLite 使用 `mode=ro`、`query_only` 和固定读事务；源文件、切片、发布成员与索引业务数据不回写。SDK 锁/WAL 维护可能产生文件系统写入，查询缓存仅写本工程 `.cache`。

整库 Qdrant Local 会冷加载大量向量，须预留内存；本次实测见验收报告。并发调用共享内存，但首轮检索执行串行，不能据此承诺吞吐量或多进程服务能力。

## 工程与验证

```text
src/guance_rag/  类型、只读来源层、双路召回、业务适配、运行时、CLI
tests/          小型真实 SQLite/BM25/Qdrant 产物及生命周期回归
scripts/acceptance.py  当前完整发布验收（只读，可调用真实向量服务）
验收报告.md / 验收结果-*.json  本次实际结果
outputs/        本地复验原始输出，忽略提交
.cache/         分词、问题向量、pytest 临时产物，忽略提交
```

```powershell
& $py -m pytest -q --basetemp=.cache/pytest-tmp
& $py .\scripts\acceptance.py --mode bm25 --output .\outputs\acceptance-bm25.json
& $py .\scripts\acceptance.py --mode hybrid --output .\outputs\acceptance-hybrid.json
```

临时测试库位于本工程 `.cache` 下；测试不读取鉴权、不调用模型。完整库验收会读取真实快照和索引，hybrid 模式在缓存未命中时发送测试查询到已配置服务。核对加载次数、并发请求、真实样例、全部超长块分页重建和数据库文件哈希；这些结果不等同于端到端问答准确率。
