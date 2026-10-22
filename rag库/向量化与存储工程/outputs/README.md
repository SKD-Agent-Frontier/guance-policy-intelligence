# 向量化批次时间索引

更新时间：2026-10-23T00:14:29.876600+08:00。

**尚无正式向量批次，没有已激活版本。** 下列都是准备批次，不是完成的向量库。

| 目录 | 创建时间（北京时间） | 范围 | 文档 | 纳入/排除文本块 | 当前代码配置 | 原文验证 |
|---|---|---|---:|---:|---|---|
| [prepared_8f806756c91dad4459364250d8a820e8402b535691fe6170fa5ac3a06a5d7614](prepared_8f806756c91dad4459364250d8a820e8402b535691fe6170fa5ac3a06a5d7614/manifest.json) | 2026-10-22T22:41:50+08:00 | 5篇样例 | 5 | 1293/5 | 开发版本 | 通过 |
| [prepared_c32b2e6dab7be13267f63a6b7aab7b95f1a18f59502e697cd86b8dc43dd34c21](prepared_c32b2e6dab7be13267f63a6b7aab7b95f1a18f59502e697cd86b8dc43dd34c21/manifest.json) | 2026-10-22T22:42:16+08:00 | 全量 | 506 | 42459/57 | 开发版本 | 通过 |
| [prepared_18dd2b2007675d05564d795aa756d7536c264baa528e609f2482d2e6cbfad400](prepared_18dd2b2007675d05564d795aa756d7536c264baa528e609f2482d2e6cbfad400/manifest.json) | 2026-10-22T22:50:57+08:00 | 5篇样例 | 5 | 1293/5 | 开发版本 | 通过 |
| [prepared_35a7c8d4aed416000863d893dd25f109689667b8ebd4b54ca24c770bad206f56](prepared_35a7c8d4aed416000863d893dd25f109689667b8ebd4b54ca24c770bad206f56/manifest.json) | 2026-10-22T22:51:25+08:00 | 全量 | 506 | 42459/57 | 开发版本 | 通过 |
| [prepared_9e9cf29f030956a841f007d4a714a90aea32c23d396427efd3d8944799025bec](prepared_9e9cf29f030956a841f007d4a714a90aea32c23d396427efd3d8944799025bec/manifest.json) | 2026-10-22T23:03:54+08:00 | 全量 | 506 | 42459/57 | 开发版本 | 通过 |
| [prepared_cf68e61ef8475c7526b232010dd20d1ee3571e8284ab33859572a49749003ad1](prepared_cf68e61ef8475c7526b232010dd20d1ee3571e8284ab33859572a49749003ad1/manifest.json) | 2026-10-22T23:06:59+08:00 | 5篇样例 | 5 | 1293/5 | 开发版本 | 未完成 |
| [prepared_fa56d378766d44fb0762d18faeb12409e65b19bdd58f69cf28a04d84dc755c04](prepared_fa56d378766d44fb0762d18faeb12409e65b19bdd58f69cf28a04d84dc755c04/manifest.json) | 2026-10-22T23:24:25+08:00 | 全量 | 506 | 42459/57 | 开发版本 | 未完成 |
| [prepared_16fe46ed646e95ba10040564920b5f40ddbd334d901eaad140ad521fdf2fceb3](prepared_16fe46ed646e95ba10040564920b5f40ddbd334d901eaad140ad521fdf2fceb3/manifest.json) | 2026-10-22T23:48:30+08:00 | 全量 | 506 | 42459/57 | 开发版本 | 未完成 |
| [prepared_38b3d04ba33f461c47baf93dec487786b5836678cd6f125fafa71843945ebf06](prepared_38b3d04ba33f461c47baf93dec487786b5836678cd6f125fafa71843945ebf06/manifest.json) | 2026-10-23T00:12:37+08:00 | 全量 | 506 | 42459/57 | 一致 | 通过 |

最新全量准备版本：`prepared_38b3d04ba33f461c47baf93dec487786b5836678cd6f125fafa71843945ebf06`。准备版本与向量发布状态分别记录。

`prepared_*`冻结上游、原文快照、入库资格与媒体；`media_*`冻结识别结果；`generation_*`才承载向量与存储候选。构建失败不切换正式指针。开发样例若归档至.cache/development-runs，保留其数据但不混在正式输出中。
