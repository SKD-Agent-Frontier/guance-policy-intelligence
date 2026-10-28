# 比较功能交付材料索引

本目录保留最终候选的六场真实验收材料及必要的复核记录。历史迭代输出、临时探测文件和含测试地址的原始 JUnit 已从公开交付中清理；清理后已复跑完整离线回归，真实模型与 RAG 场景未重跑。

## 最终验收

- [最终验收报告](acceptance-fix-v11/README.md)：六个真实场景和 27 次引文字面检查属于 v11 原批次；清理后离线回归为 507 项通过（16.13秒），比较测试106项，含新增导入/入口注册兼容测试；未重跑真实模型/RAG。
- [交付记录](acceptance-fix-v11/DELIVERY.md)：提交与 PR 交付信息。
- [自动检查结果](acceptance-fix-v11/acceptance.json)、[证据与源码指纹](acceptance-fix-v11/evidence-audit.json)、[逐项审阅](acceptance-fix-v11/review.json)。
- [最终样例](../../../src/guance_agent/business/compare/sample.json)及[结构契约](../../../src/guance_agent/business/compare/SCHEMA.md)。样例中的运行/会话标识已统一替换为稳定占位符。
- [保留的静态检查输出](acceptance-fix-v11-checks/ruff.txt)。JUnit 原始 XML 未保留，因为用例名称带有测试 URL 参数。

## 历史与限制

最终批次之前的修复轮次和失败试运行曾用于开发，但其逐轮原始输出已裁剪，仅在[功能验收记录](../../../src/guance_agent/business/compare/VALIDATION.md)中保留精简修复摘要。507 项结果是清理后的离线复跑；六个真实模型/RAG场景及27次引文检查仍为 v11 原批次记录。

引文来源及字面核验通过不代表语义正确或覆盖全文；本轮样例不证明任意输入均正确。取证范围和其他限制见最终验收报告与功能验收记录。
