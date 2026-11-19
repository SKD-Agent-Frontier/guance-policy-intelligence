# E：GPT Researcher 独立研究引擎

本分支以开源 [GPT Researcher](https://github.com/assafelovic/gpt-researcher) 替代分析与报告生成部分；**不是** Deep Agents 的子工具或 Skill。

与 B 使用同一个原有 RAG 的确定性 search/read 读取源文段，将文段转为 LangChain Document，再使用 `report_source="langchain_documents"` 调用 GPT Researcher 的 conduct_research/write_report。

## 安装（agent/ 目录，单独环境）
```powershell
py -3.11 -m venv .venv-e
.venv-e\Scripts\python.exe -m pip install -e .
.venv-e\Scripts\python.exe -m pip install "gpt-researcher" "langchain-core>=1"
.venv-e\Scripts\python.exe experiments/run.py --question "甲市十四五农业农村规划第二章有哪些重点任务？" --prepare-only
.venv-e\Scripts\python.exe experiments/run.py --question "甲市十四五农业农村规划第二章有哪些重点任务？"
```

本地配置 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL。三个模型角色都指定为同一个 Qwen，不允许静默落入 GPT Researcher 默认外部模型。注意 GPT Researcher 可能使用其他配置（例如 embeddings/retrieval），需要实际核对运行日志后才可以确认不曾调用外部服务。

## 解释实验
- E 的目标是研究型交付质量，不是与 B 的一次模型调用做完全公平的耗时对比。
- 与 B 保持相同输入文段，输出为研究报告，不包含本项目的原文坐标核验；默认标识 unreviewed。
- 默认 mock 只是冒烟；没有恢复真实 RAG 且接通学校 Qwen 前，**没有实测通过结果**。
- 若 GPT Researcher 与学校网关在 temperature、模型名等参数上不兼容，不要静默换模型。记录失败并单独修适配。
