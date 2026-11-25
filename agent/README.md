# 管策智研 Agent

政策文本检索与分析助手，支持问答、要素提取、政策对比、多方案分析、原文出处和 JSON 输出。
默认 mock 后端只有五篇合成测试资料；真实研究需配置 rag 后端、恢复运行库并完成真实业务验收。

## 研究执行与交付

CLI 和 Web 的 `POST /api/runs` 都调用 `main.submit()`。保留的 Python 入口 `orchestrator.run_task()` 也进入同一个 SDK 研究循环。每个请求建立一个 `ResearchState`。模型按问题选择检索、回读和研究能力；显式的业务任务只能调用同名能力。

| 环节 | 实现与约定 |
| --- | --- |
| 研究对象 | `orchestrator.py` 优先采用显式 targets，再使用可信目录或唯一标题定位。标题有歧义时返回澄清。 |
| 取证 | SDK 的 `search` 和 `read_batch` 使用原有 `kb_search` 和 `kb_read` 接口。各次实际读取向本轮证据表登记位置与来源身份。 |
| 业务能力 | `run_capability` 调用 `extract`、`organize`、`compare` 或 `compare_plans`。业务模块通过 `BusinessServices` 使用当前运行的知识库、预算和截止时间，返回候选事实、结构和分析。 |
| 预算 | `MAX_TOOL_CALLS` 约束检索、回读和后端引句确认；可信目录发现、标题定位也占用本轮额度。`MAX_TURNS` 与执行器上限共同约束主模型、业务生成和语义审核。运行时关闭客户端自动重试。 |
| 交付 | `read_verification.py` 依据本次实际看到的原文页核对引句、数字与来源。`delivery_gateway.py` 检查事实和审核记录，再生成公开答案及结构化结果。审核未完成时，可保留独立成立的逐字事实和此前获准的分析块。 |

`research_core.py` 暴露 SDK 工具并运行主 Agent，`runtime.py` 管理会话与日志，`research_runtime.py` 依据最终交付结果评估任务完成度。RAG 后端的调用签名、坐标基准、版本字段及错误约定见 [冻结检索接口](contracts/kb_contract.md)。

## 安装

需要 Python 3.11 或更新版本。以下命令都在仓库的 `agent/` 目录执行。

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

macOS / Linux：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## 配置模型

在能够访问学校模型接口的机器上配置 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`。
连接地址和密钥向校方获取，只保留在本机环境变量中；不要写入源码或提交历史。
也支持将 `.env.example` 复制为本机 `.env`；环境变量优先于 `.env`。

| 配置项 | 默认值 / 用法 |
| --- | --- |
| LLM_BASE_URL、LLM_API_KEY | 必填；未配置时问答返回 MODEL_UNAVAILABLE |
| LLM_MODEL | qwen3.8-27b |
| QA_ENGINE | 历史配置，接受 controlled 和 sdk。两个值均进入 SDK 主 Agent，可在 meta.qa_engine_effective 查看实际执行器。 |
| STRUCTURED_OUTPUT | off；模型接口实测支持 json_schema 后可改 on |
| LLM_TEMPERATURE | 留空，不发送该参数；接口实测支持后再填 0 |
| LLM_REQUEST_TIMEOUT、LLM_MAX_RETRIES | 单次模型请求 60 秒、最多重试 2 次 |
| MAX_INPUT_CHARS、MAX_TURNS | 去除首尾空白后的最大字符数为 4000、最多 8 轮模型调用 |
| MAX_USER_ID_CHARS | 用户 ID 最多 64 字符 |
| RUN_TIMEOUT、TOOL_TIMEOUT | 整次执行 180 秒、单次工具 20 秒 |
| TOP_K_DEFAULT、TOP_K_MAX | 检索默认 8 条、上限 20 条 |
| SEARCH_SNIPPET_CHARS、READ_MAX_CHARS | 检索片段最多 300 字、章节读取最多 6000 字 |
| KB_BACKEND | mock（默认）、sample、rag |
| RAG_STORE_PROJECT、RAG_MODE | 留空用 guance_rag 默认路径；bm25（默认）、dense、hybrid |
| MOCK_KB_PATH | mock_data/kb.json |
| SESSION_PATH、RUN_LOG | data/sessions.sqlite、data/runs.jsonl |
| LOG_CONTENT | off；on 仅用于本机调试，会额外记录问题与回答 |

相对路径以 `agent/` 为基准。模型请求、轮次和工具限制都可通过以上配置调整。

先执行接口实测：

```bash
python -m guance_agent probe
# 等价命令：python scripts/probe_model.py
```

运行后查看生成的 `docs/model_probe.md`，再据此设置结构化输出和 temperature。
脚本不会自动覆盖本机配置；配置缺失时返回“待联调”，不发送模型请求。

## 问答

```bash
python -m guance_agent ask "甲市十四五农业农村规划有哪些重点？"
python -m guance_agent ask "那它的重点任务呢？" --session s_xxx
python -m guance_agent ask "甲市农业重点有哪些？" --json
python -m guance_agent chat
python -m guance_agent chat --session s_xxx
```

单次问答会把 `session_id` 打印到标准错误输出。将 `s_xxx` 替换为实际编号即可继续会话。
不传 `--session` 会新建会话；`chat` 中输入空行或文件结束符退出，Ctrl+C 取消。
`--json` 的标准输出只包含 RunResult JSON，可重定向保存或交给其他程序。

`status` 表示执行是否结束或达到限制。`evidence_status` 表示公开证据的核验情况；客户端可在 `claims` 和 `evidence_refs` 回看每条事实的原文。`meta.research_delivery.status`、`meta.research` 及业务结构中的缺口说明哪些研究要求已完成。执行结束时仍可能只有部分事实可交付。

客户端应同时展示 `answer`、`warnings` 和原文出处。检索服务故障与正常检索无匹配分别记录。已有可交付事实时，局部检索失败会附带说明。

退出码：0=执行完成（也可能证据不足），1=执行失败，2=输入不合法，3=达到运行限制。

## 在 Python 中调用

```python
import asyncio
from guance_agent.main import submit

async def example():
    first = await submit("甲市农业规划有哪些重点？")
    followup = await submit("那它第二章呢？", session_id=first.session_id)
    print(followup.model_dump_json())

asyncio.run(example())
```

在同一个事件循环中复用入口。需要会话归属检查时传入 `user_id`；已绑定身份的会话不允许其他身份访问。
取消任务使用调用方的 asyncio 取消机制，CancelledError 会继续向调用方传播。

## 测试与排障

```bash
python -m pytest
python -m ruff check .
python scripts/live_acceptance.py
```

测试和代码检查完全离线，不需要模型密钥。验收脚本调用真实学校模型，运行后生成
`docs/live_acceptance.md`，需要本机模型配置和可用的网络连接。

`data/runs.jsonl` 每行是一条事件，可按 `run_id` 关联模型轮次、工具状态与耗时。
默认不记录问题、回答和政策原文；会话历史保存在本机 `data/sessions.sqlite`。
模型不可用时先检查环境变量和网络；出现 MAX_TURNS 时缩小问题范围；
出现 OUTPUT_PARSE 时先查看接口实测记录，确认结构化输出模式是否兼容。

## 检索接入与限制

接口和样例位于 [contracts/kb_contract.md](contracts/kb_contract.md) 与 `contracts/samples/`。
支持三种后端：`mock`（默认，离线）、`sample`（用 RAG 交付的真实样例）、`rag`（真实检索工程）。

`rag` 后端需要先安装 `rag库/检索工程`（`pip install -e rag库/检索工程`）并恢复运行数据。
`top_k` 默认 20：RAG 侧 21 题基准给出 Recall@5=90.5%、Recall@8=95.2%、Recall@20=100%、MRR=0.768，
已知一条查询的相关来源排在第 18 位，取 8 会漏。政策场景漏检比多读几条片段代价更高。

`RagKnowledgeBase` 构造会阻塞到索引就绪（BM25 约 11 秒，Hybrid 约 83 秒），因此
**必须在进程启动时调用 `kb.warm_up(settings)`，不能留到首个请求**；退出时调用 `kb.close_kb()`。
CLI 会在接收请求前的 `_run()` 阶段在线程池中完成真实 RAG 预热；请求编排拒绝未预热的真实 RAG。
首轮只取 `source_types=("text_chunk",)`，OCR 与图片留到第二阶段。

原文坐标、引句和数字的核验只使用本轮已见范围。后端提供 `verify_quote` 时，登记引句会按文献和发布代次向后端确认。事实概括、对比及方案判断还要通过与证据绑定的审核，最终由网关组装成答案。

匿名会话不绑定客户端身份；持有同一 `session_id` 的匿名调用方都可以读取该会话历史。
新会话 ID 含 32 位随机字符，难以猜测；生产环境应由 Web 认证层签发不可伪造的凭证。
章节超长时只返回开头部分；工具超时会停止等待，但不能强制终止后端线程，正式后端仍需自身查询超时。

## 政策整理、比较与多方案分析

`business.builtins` 注册 `organize`、`compare` 和 `compare_plans`；普通导入业务包或单个模块不会注册。政策整理支持开放主题，也可传入明确的 `targets`：

```python
result = await submit(
    "梳理十四五时期的科技创新政策",
    task_type="organize",
    settings=settings,
)
```

政策比较通过 Python 统一入口选择 `task_type="compare"` 或 `"compare_plans"`，指定 2–8 个政策对象及可选维度。
两个模块共享模型客户端、RAG、调用预算和证据核验，输出事实矩阵、逐维口径说明、主要异同；
多方案任务另输出有依据的优势、限制和适用条件。没有评价标准不自动评分或排名。

```python
result = await submit(
    "比较两份人才政策的支持措施和适用对象，说明比较限制",
    task_type="compare",
    targets=["37982", "38366"],
    dimensions=["支持措施", "适用对象"],
    settings=settings,
)
```

真实 RAG 需在应用启动时预热。命令行 ask/chat 仍使用默认 qa；本轮不增加界面或导出功能。
比较的完整字段、调用方式、缺证处理和真实验收说明见
[比较模块说明](src/guance_agent/business/compare/README.md) 与
[SCHEMA](src/guance_agent/business/compare/SCHEMA.md)。
客户端须展示 warnings 与证据不足状态；基础层核验失败导致 structured 清空时，不得继续展示旧矩阵。
