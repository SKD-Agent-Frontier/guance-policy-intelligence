# 向量化批次时间索引

更新时间：2026-10-23T08:05:38.867933+08:00。

正式入口以工程当前批次.json及PostgreSQL指针为准。

| 目录 | 创建时间（北京时间） | 范围 | 文档 | 纳入/排除文本块 | 当前代码配置 | 原文验证 |
|---|---|---|---:|---:|---|---|
| [prepared_b0c1f2967768f8f55e4d8fbd34386fd05305f17bfa2dbf41ccf7caccf80f87f3](prepared_b0c1f2967768f8f55e4d8fbd34386fd05305f17bfa2dbf41ccf7caccf80f87f3/manifest.json) | 2026-10-23T00:55:29+08:00 | 5篇样例 | 5 | 1293/5 | 开发版本 | 通过 |
| [prepared_500de044fe2796840e48615ccca8b73ce42248df10f355b3e95786e3eb854867](prepared_500de044fe2796840e48615ccca8b73ce42248df10f355b3e95786e3eb854867/manifest.json) | 2026-10-23T00:59:00+08:00 | 全量 | 506 | 42459/57 | 一致 | 通过 |
| [prepared_974e54cf1fae7785c6a666faeb8cb444a62c4adc6b1245f990983e07e39feaf4](prepared_974e54cf1fae7785c6a666faeb8cb444a62c4adc6b1245f990983e07e39feaf4/manifest.json) | 2026-10-23T00:59:03+08:00 | 5篇样例 | 5 | 1293/5 | 一致 | 通过 |

最新全量准备版本：`prepared_500de044fe2796840e48615ccca8b73ce42248df10f355b3e95786e3eb854867`。准备版本与向量发布状态分别记录。

**当前已激活版本：[generation_531a951ba715b142282dfddd07116f0573c60171dd19ced503c8a7ed973f3799](generation_531a951ba715b142282dfddd07116f0573c60171dd19ced503c8a7ed973f3799/manifest.json)**。激活时间：2026-10-22T18:27:06.707154+00:00。

| 向量批次 | 清单写入时间（北京时间） | 对账 | 当前激活 |
|---|---|---|---|
| [generation_39f284408139b37e2a425d941a6bcded4512977c13a9d4f2c1982fde39e3ae53](generation_39f284408139b37e2a425d941a6bcded4512977c13a9d4f2c1982fde39e3ae53/manifest.json) | 2026-10-23T00:56:04+08:00 | 未通过或未完成 | 否 |
| [generation_2e94a078c650283f8ba53ba5394c2f0c45faa8f3b63e0e231385243c14d9fbb6](generation_2e94a078c650283f8ba53ba5394c2f0c45faa8f3b63e0e231385243c14d9fbb6/manifest.json) | 2026-10-23T00:59:34+08:00 | 通过 | 否 |
| [generation_531a951ba715b142282dfddd07116f0573c60171dd19ced503c8a7ed973f3799](generation_531a951ba715b142282dfddd07116f0573c60171dd19ced503c8a7ed973f3799/manifest.json) | 2026-10-23T01:10:06+08:00 | 通过 | 是 |

| 识别快照 | 文件记录时间（北京时间） | 用途 |
|---|---|---|
| [media_3c356db0373bdc6e3189a503](media_3c356db0373bdc6e3189a503/media_extractions.jsonl) | 2026-10-23T00:55:37+08:00 | 不可变识别及视觉父子块，是否正式使用以generation清单为准 |
| [media_93cbe8cb9e3a9c8a6098fb8e](media_93cbe8cb9e3a9c8a6098fb8e/media_extractions.jsonl) | 2026-10-23T01:10:06+08:00 | 不可变识别及视觉父子块，是否正式使用以generation清单为准 |

[已归档开发准备版本及各自时间](../.cache/development-runs/README.md)。归档只移动本任务未被发布引用的准备目录，没有删除数据。

`prepared_*`冻结上游、原文快照、入库资格与媒体；`media_*`冻结识别结果；`generation_*`才承载向量与存储候选。构建失败不切换正式指针。开发样例若归档至.cache/development-runs，保留其数据但不混在正式输出中。
