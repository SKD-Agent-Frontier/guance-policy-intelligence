# 管策智研 Agent

命令行政策文本检索助手，支持单次问答、多轮会话、原文出处和 JSON 输出。
当前检索库只有五篇合成测试资料，不能用于真实政策研究。

## 模块结构

`src/guance_agent/` 按单向依赖分层。`tests/test_architecture.py` 用 AST 强制校验三项结构性约束：单文件不超过 150 行、只有工具层与执行层可导入 Agent SDK、模块依赖必须单向。

| 模块 | 职责 |
| --- | --- |
| `config.py` | 环境变量与配置装载、边界校验 |
| `schemas.py` | 工具契约与对外结果的 Pydantic 模型，不依赖 Agent SDK |
| `kb.py` | 检索后端契约与 MockKB 实现 |
| `context.py` | 每次运行独立的证据注册表与工具调用记录 |
| `tools.py` | `kb_search` / `kb_read` 包装：参数校验、超时、统一 ToolResult |
| `agent.py` | 单 Agent 配置与版本化提示词 |
| `runtime.py` | 会话持久化、客户端复用、脱敏运行日志 |
| `runner.py` | 调用 SDK 执行循环并映射执行异常 |
| `output.py` | 证据 ID 校验、确定性结果与出处组装 |
| `orchestrator.py` | 执行编排：建立检索与证据上下文，调用执行核心，返回结构化结果 |
| `main.py` | 任务入口：只做校验、装配与转交，不直接访问 RAG |
| `cli.py` | 标准库命令行，调用 `main.submit` |

依赖方向：`config -> schemas -> kb -> context -> tools -> agent -> runtime -> runner -> output -> orchestrator -> main -> cli`，不得反向或循环。入口层不持有检索与模型实现，替换 `get_kb()` 分支或调整模型连接参数都不需要修改 `main.py`。

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

JSON 中 `status` 表示执行完成、失败或达到限制；`evidence_status=verified` 仅表示出处 ID
来自本轮工具结果，不代表结论经过事实核验。

`warnings` 是面向使用方的风险提示列表：未检索原文、有结论未被采信、部分引用无法核验、证据不足等都会写在这里。**客户端必须展示该字段**——只渲染 `answer` 会丢失全部风险提示。脚本如需比对模型原话，请用 `answer`；`answer` 不含系统追加的提示。`insufficient` 表示证据不足；`none` 表示未引用政策原文。
有效出处包含篇名、地区、规划期、章节和片段编号。

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
CLI 已在 `main()` 里接好这两步。首轮只取 `source_types=("text_chunk",)`，OCR 与图片留到第二阶段。

证据诚信由三层确定性校验保证：坐标自洽（跨度非空、长度与坐标一致）、章节包含（块不得越出
登记章节，除非后端已标 `chunk_outside_section`）、版本一致（版本/哈希/坐标基准）。后端提供
`verify_quote` 时，`verify.py` 还会把每条引文换回原文逐字核对，只批准来自本次已见范围的句子。
这一步只做字面核对，不判断语义是否支撑结论。

同一层还有第二道核对：`verify_numbers` 把答案正文里的数字逐个拿去已见原文里找，
找不到就写进 `warnings`；缺的里面有百分号时直接降级为证据不足——政策问答里的
百分比几乎只可能是编出来的。有序列表的行首编号（`1.` `2、`）会先剥离，避免误报。
无 `get_chunk_source` 的后端自动跳过，不假装核过。
正式接入前需要与 RAG 负责人确认字段、章节定位参数和规划期分类。

只提供 `kb_search` 与 `kb_read` 两个只读工具，不提供 Web 服务、文档导出或真实语料检索。
匿名会话不绑定客户端身份；持有同一 `session_id` 的匿名调用方都可以读取该会话历史。
新会话 ID 含 32 位随机字符，难以猜测；生产环境应由 Web 认证层签发不可伪造的凭证。
不进行逐字引文或语义支持校验，也未验证五人并发。
模型若不调工具直接回答政策问题，结果只能标为“未引用政策原文”。
章节超长时只返回开头部分；工具超时会停止等待，但不能强制终止后端线程，正式后端仍需自身查询超时。
