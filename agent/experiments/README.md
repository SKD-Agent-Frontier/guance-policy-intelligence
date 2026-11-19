# C：Deep Agents 单 Agent

基于 [LangChain Deep Agents](https://github.com/langchain-ai/deepagents) 的单 Agent 实验。
模型为现有学校 Qwen（兼容 Chat Completions，不使用 Responses API），工具复用原仓库只读 RAG。
这里测试的是 Deep Agents 运行底座，并不加 Skill/MCP；不修改旧 Agent 代码。

## 安装（建议独立虚拟环境）
```powershell
cd agent
py -3.11 -m venv .venv-c
.venv-c\Scripts\python.exe -m pip install -e .
.venv-c\Scripts\python.exe -m pip install "deepagents>=0.7,<1" "langchain-openai>=1"
.venv-c\Scripts\python.exe experiments/run.py --question "甲市十四五农业农村规划第二章有哪些重点任务？"
```

运行需要本机 LLM_BASE_URL/LLM_API_KEY，真实 RAG 需恢复发布的运行库并设置 KB_BACKEND=rag、RAG_STORE_PROJECT。模型未实际连通之前不宣称功能验收通过。

## 安全与对照
默认使用 Deep Agents 的 in-memory backend；系统指令禁止无关文件操作、执行命令和多 Agent，但框架仍可能暴露内置工具。隔离运行、不赋予系统高权限。答案引用暂未经过原 Agent 的确定性校验，结果标识 unreviewed。
相比 B，唯一新增核心变量应当是 Deep Agents 执行链路；questions.json 与其它组完全相同。
