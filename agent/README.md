# 管策智研 Agent

需要 Python 3.11 或更新版本。在本目录执行：

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS / Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

将 `.env.example` 复制为 `.env`，在本机配置模型地址和密钥。
环境变量优先于 `.env`，请勿提交本地配置。

运行检查：

```bash
python -m pytest
python -m ruff check .
```
