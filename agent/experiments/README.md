# B：极简 RAG + Qwen 问答基线

与 A 从同一主分支分出。直接使用原仓库的 get_kb、warm_up 和本地 Qwen 配置。
执行只有 **一次检索 → 分页原文读取 → 一次 Chat Completions → 答案**，不使用 Agent SDK、研究规划、额外审核、Skill 或 MCP。

## 运行
```powershell
cd agent
python -m pip install -e ".[dev]"
python experiments/run.py --question "甲市十四五农业农村规划第二章有哪些重点任务？" --retrieval-only
python experiments/run.py --question "甲市十四五农业农村规划第二章有哪些重点任务？"
```
配置 agent/.env（勿提交）：LLM_BASE_URL、LLM_API_KEY、LLM_MODEL、KB_BACKEND。真实 RAG 需要预先恢复原数据并设置 RAG_STORE_PROJECT。

## 边界
- --retrieval-only 不需要模型，但测试的是检索，不是问答。
- 此基线输出 evidence_status=unreviewed；引用标签未经过原 Agent 的逐字与语义审核，不可作为正式交付。
- 标准输出为单行 JSON，包含耗时、调用量和来源 IDs；默认 mock 数据只有合成测试文档。
- 与 A 共享 questions.json；真实题集必须另行冻结，且分别运行多个回合。
- 基线不得直接合并到 main；不要将它作为政策正式答案服务。
