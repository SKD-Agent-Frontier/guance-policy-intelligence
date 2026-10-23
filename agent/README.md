# 管策智研 Agent

命令行政策文本检索助手，支持单次问答、多轮会话、原文出处和 JSON 输出。
当前检索库只有五篇合成测试资料，不能用于真实政策研究。

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
| MAX_INPUT_CHARS、MAX_TURNS | 最多 4000 字、8 轮模型调用 |
| RUN_TIMEOUT、TOOL_TIMEOUT | 整次执行 180 秒、单次工具 20 秒 |
| TOP_K_DEFAULT、TOP_K_MAX | 检索默认 8 条、上限 20 条 |
| SEARCH_SNIPPET_CHARS、READ_MAX_CHARS | 检索片段最多 300 字、章节读取最多 6000 字 |
| KB_BACKEND、MOCK_KB_PATH | mock、mock_data/kb.json |
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
来自本轮工具结果，不代表结论经过事实核验。`insufficient` 表示证据不足；`none` 表示未引用政策原文。
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

## 检查与排障

```bash
python -m ruff check .
python scripts/live_acceptance.py
```

代码检查完全离线，不需要模型密钥。验收脚本调用真实学校模型，运行后生成
`docs/live_acceptance.md`，需要本机模型配置和可用的网络连接。

`data/runs.jsonl` 每行是一条事件，可按 `run_id` 关联模型轮次、工具状态与耗时。
默认不记录问题、回答和政策原文；会话历史保存在本机 `data/sessions.sqlite`。
模型不可用时先检查环境变量和网络；出现 MAX_TURNS 时缩小问题范围；
出现 OUTPUT_PARSE 时先查看接口实测记录，确认结构化输出模式是否兼容。

## 检索接入与限制

接口位于 [contracts/kb_contract.md](contracts/kb_contract.md)。
当前仅支持 MockKB；正式后端实现同一接口后，在 `get_kb()` 增加分支即可接入。
正式接入前需要与 RAG 负责人确认字段、章节定位参数和规划期分类。

只提供 `kb_search` 与 `kb_read` 两个只读工具，不提供 Web 服务、文档导出或真实语料检索。
不进行逐字引文或语义支持校验，也未验证五人并发。
模型若不调工具直接回答政策问题，结果只能标为“未引用政策原文”。
章节超长时只返回开头部分；工具超时会停止等待，但不能强制终止后端线程，正式后端仍需自身查询超时。
