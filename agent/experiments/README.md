# A/B/C/D/E Agent 实验 — A：原始对照组

本分支从 2026-11-19 的 main 创建。只增加 experiments/ 测试入口，不改变原有 Agent 执行链路。

## 使用
在仓库根目录恢复既有 RAG 数据、在 agent/ 安装依赖：
```powershell
cd agent
python -m pip install -e ".[dev]"
python experiments/run.py --question "甲市十四五农业农村规划第二章有哪些重点任务？"
```

默认 KB_BACKEND=mock，是合成数据，不是政策业务验收。真实 RAG 需配置 KB_BACKEND=rag、RAG_STORE_PROJECT；正式结果必须跑已冻结的真实题集。学校模型 LLM_BASE_URL/LLM_API_KEY 留在本地，不可提交。

**实验约定：** 每组输出 variant/question/status/answer/evidence_planids/elapsed_ms 等；使用相同模型、数据版本、问题、时间窗口。A 仅做原系统基线，不添加 Skill/MCP。questions.json 仅供 mock 冒烟，不能用于排名、判断研究效果。

后续 B：极简；C：Deep Agents；D：Skills+MCP；E：GPT Researcher（独立研究任务，对普通 QA 的公平性需单列）。
