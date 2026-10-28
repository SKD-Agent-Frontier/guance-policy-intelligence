# 历史失败试运行

这是此前使用学校 `step-3.7-flash` 的真实试运行，不是当前实现的最终验收结果。
当时使用 4096 tokens 输出上限，尚未加入最终的分析复核步骤，跨期对象也与最终验收不同。

[acceptance.json](acceptance.json) 记录政策对比、跨期及缺证场景通过，而多方案分析缺少有据的
优势、限制和适用性，整体未通过。运行中出现输出截断及一次格式修正；没有将其计入最终通过场景。

同目录保存 [policy.json](policy.json)、[schemes.json](schemes.json)、
[cross_period.json](cross_period.json)、[controlled_missing.json](controlled_missing.json) 四份实际结果。
保留这些失败记录用于追溯模型配置与实现变化，不将它们与最终通过结果混合统计。
