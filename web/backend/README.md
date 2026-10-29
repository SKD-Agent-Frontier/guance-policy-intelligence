# 一、管策智研 Web API

本目录提供供 React 前端调用的 FastAPI 服务。API 将请求交给现有 Agent 的 `submit()`，由 Agent 执行问答、要素提取、政策梳理或比较分析；Web 层不重新生成答案或改写业务结果。

## （一）环境要求

- Python 环境能够安装仓库中的 Agent 包及本目录依赖。
- 运行时所需的模型配置和知识库资源由 Agent 配置提供；私有配置文件和运行库不需要复制到仓库。
- 本地开发前端默认运行于 `http://localhost:5173` 或 `http://127.0.0.1:5173`。

## （二）安装

在仓库根目录运行：

```powershell
python -m pip install -e './agent[dev]'
python -m pip install -r web/backend/requirements.txt
```

私有 Agent 配置可通过 `GUANCE_ENV_FILE` 指向本地配置文件。服务启动时读取该文件，并在进程环境变量中读取 Agent 配置；不要把凭据写入文档、响应或提交到仓库。

在 PowerShell 中可设置配置文件位置（将占位值替换为本机私有配置文件的实际路径）：

```powershell
$env:GUANCE_ENV_FILE = '<private-env-file-path>'
```

若使用真实 RAG 后端，还需从仓库根目录安装 RAG 检索工程：

```powershell
python -m pip install -e './rag库/检索工程'
```

在 `GUANCE_ENV_FILE` 指向的私有配置文件中设置真实 RAG 所需参数：

```text
KB_BACKEND=rag
RAG_STORE_PROJECT=<restored-rag-runtime-directory>
RAG_MODE=bm25
```

`RAG_STORE_PROJECT` 应指向已恢复的运行库目录；`RAG_MODE` 支持 `bm25`、`dense` 或 `hybrid`。配置和检索资源的初始化、预热及关闭方式见 [Agent 集成指南](../../agent/INTEGRATION.md)。不要将私有配置或运行库提交到仓库。

## （三）配置跨域

默认仅允许以下两个本地前端 Origin：

```text
http://localhost:5173
http://127.0.0.1:5173
```

可用 `GUANCE_CORS_ORIGINS` 配置允许的 Origin，多个值用逗号分隔。不要添加路径或尾部斜线；通配符 `*` 不被接受。例如在 PowerShell 中：

```powershell
$env:GUANCE_CORS_ORIGINS = 'http://localhost:5173,http://127.0.0.1:5173'
```

## （四）启动服务

在仓库根目录安装完成后，进入 `web/backend` 并启动单 worker 服务：

```powershell
Set-Location web/backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API Base URL 为 `http://127.0.0.1:8000`。本地前端可请求 `http://127.0.0.1:8000/api/runs` 和 `http://127.0.0.1:8000/api/health`。

本地运行使用一个 Uvicorn worker。FastAPI lifespan 在服务启动时加载共享 Agent 设置并预热检索资源，关闭时释放知识库和共享模型客户端。BM25 预热通过线程执行，避免阻塞异步事件循环；非 RAG 后端的 `warm_up()` 不做预热操作。单 worker 可避免本地开发时每个 worker 各自初始化一份生命周期资源。

`GET /api/health` 只报告 Web API 是否就绪。返回 `ok` 不证明真实 RAG 知识库或模型端点已经连接成功，也不是模型调用验证。

## （五）请求等待与安全

任务以普通异步 HTTP 请求运行，服务会等待 Agent 的 `submit()` 返回完整 RunResult。Agent 的总运行上限约为 180 秒，因此前端应显示加载状态，并避免在前端、反向代理或部署平台设置更短的请求超时。超时或业务执行失败由 Agent 的 RunResult 表达，不应由前端推定成功。

当前 MVP 面向本地开发和演示，没有用户身份认证。`session_id` 用于 Agent 会话续接，不具备认证或授权能力；未增加认证前不要将服务直接暴露至公网。API 不返回模型密钥或私有配置；未预期的服务异常以通用错误响应返回。

## （六）测试与检查命令

在 `web/backend` 目录执行后端自身的测试和静态检查，作为 Web API 新功能的交付检查：

```powershell
python -m pytest
python -m ruff check .
```

Agent 回归检查：从 `web/backend` 目录切换到 Agent 目录后执行：

```powershell
cd ../../agent
python -m pytest
python -m ruff check .
```

# 二、相关文档

- [API_CONTRACT.md](API_CONTRACT.md)：前端请求、响应与错误契约。
- Agent 共享 RunResult 与业务结构的具体含义以 Agent schema 和业务契约为准；Web API 原样传递其字段。
