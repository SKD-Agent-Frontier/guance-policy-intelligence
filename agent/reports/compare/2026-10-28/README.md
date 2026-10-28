# 比较功能测试报告

这些文件来自实际测试，已从本机排除目录移入仓库，可随开发分支推送到 GitHub。

**最新状态：本轮受控后端功能验收通过。** 最终候选502项离线回归和静态检查通过；
同一版本的六个真实场景通过结构、27次引文字面检查及AI助手逐项语义审阅。
详见 [最终验收报告](acceptance-fix-v11/README.md)。普通比较、方案、跨期和跨专题有据输出，
两类缺证按预期降级。开发分支未推送，PR未创建。

[retest-110430](retest-110430/README.md)及acceptance-fix-v2至v10均保留各自原始结果和未通过原因。
下表原有文件是更早的历史记录，不能替代最终候选的证据。

| 文件 | 内容 |
| --- | --- |
| [pytest-agent.xml](pytest-agent.xml) | 原功能交付的完整离线回归：449 passed，无失败、错误或跳过；仅去除本机 hostname。 |
| [publication-checks.xml](publication-checks.xml) | 调整报告与私有会话目录后的完整离线回归：450 passed，含新增路径隔离测试；仅去除本机 hostname。 |
| [comment-review-checks.xml](comment-review-checks.xml) | 详细注释及解析形状复查后的完整离线回归：455 passed；仅去除本机 hostname。 |
| [comment-review-structure.json](comment-review-structure.json) | 对照旧提交检查 AST、模型定义和提示词构造，并重放保存的 16 份真实响应；没有发起新的真实模型调用。 |
| [acceptance.json](acceptance.json) | 此前同一次真实 Qwen + RAG 运行，四个场景通过结构与字面核验；不代表最新复测结果。 |
| [policy.json](policy.json) | 政策对比的完整实际 RunResult。 |
| [schemes.json](schemes.json) | 多方案分析的完整实际 RunResult。 |
| [cross_period.json](cross_period.json) | 跨规划期和文件类型对比的实际结果。 |
| [controlled_missing.json](controlled_missing.json) | 受控屏蔽乙侧正文时的真实降级结果。 |
| [real-rag-probe.json](real-rag-probe.json) | 真实 RAG 检索、回读和引文探测，未调用模型。 |
| [real-collection-probe.json](real-collection-probe.json) | 真实 BusinessServices 均衡取证探测，未调用模型。 |
| [model-connectivity.json](model-connectivity.json) | 模型连接探测，仅记录状态，不含地址和 API Key。 |
| [history/step-3.7-flash/](history/step-3.7-flash/README.md) | 先前失败试运行的汇总和四份完整结果，与最终验收分开标注。 |

政策事实和引文通过字面核验，分析字段仍为模型判断；四个样例不代表任意输入均正确。
实现范围、AI 助手逐项审阅和已知限制见
[业务验收记录](../../../src/guance_agent/business/compare/VALIDATION.md)。

模型凭据、会话数据库、原始提示词、模型草稿和临时缓存继续留在本机排除目录。
后续验收的正式结果默认写入 `agent/reports/compare/latest/`；运行状态由 `--private-dir` 单独指定。
