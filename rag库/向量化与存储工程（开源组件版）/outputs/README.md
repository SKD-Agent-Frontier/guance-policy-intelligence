# 向量化批次时间索引

更新时间：2026-10-23T10:14:35.372424+08:00。

正式入口以工程当前批次.json及PostgreSQL指针为准。

| 目录 | 创建时间（北京时间） | 范围 | 文档 | 纳入/排除文本块 | 当前代码配置 | 原文验证 |
|---|---|---|---:|---:|---|---|
| [prepared_9619840ab58e5e8521ea92a4d4439370ec7662e8446fb411ad67f1e88dab18eb](prepared_9619840ab58e5e8521ea92a4d4439370ec7662e8446fb411ad67f1e88dab18eb/manifest.json) | 2026-10-23T04:02:35+08:00 | 全量 | 506 | 43446/96 | 历史代码版本 | 通过 |
| [prepared_177d59c05ed7cc7f28af9fd06dd94e882ce1f0d84358eacbb4e92473cf9772b6](prepared_177d59c05ed7cc7f28af9fd06dd94e882ce1f0d84358eacbb4e92473cf9772b6/manifest.json) | 2026-10-23T08:48:27+08:00 | 全量 | 506 | 43542/0 | 历史代码版本 | 通过 |
| [prepared_a6196e2ec9e34c21a88d7dfe276cbac67d87576d1261f2b7fd909e5a0a2cf38b](prepared_a6196e2ec9e34c21a88d7dfe276cbac67d87576d1261f2b7fd909e5a0a2cf38b/manifest.json) | 2026-10-23T09:26:46+08:00 | 全量 | 506 | 43542/0 | 一致 | 通过 |

最新全量准备版本：`prepared_a6196e2ec9e34c21a88d7dfe276cbac67d87576d1261f2b7fd909e5a0a2cf38b`。准备版本与向量发布状态分别记录。

**当前已激活版本：[generation_ce8d63791a039cff77e4d9251c8b06680ae27188306d96f3ae4dce950d3a765e](generation_ce8d63791a039cff77e4d9251c8b06680ae27188306d96f3ae4dce950d3a765e/manifest.json)**。激活时间：2026-10-23T02:06:27.398620+00:00。

| 向量批次 | 清单写入时间（北京时间） | 对账 | 当前激活 |
|---|---|---|---|
| [generation_71821316d70c92132db03cf68d66818246ec06f84f8e2fe1f926e20f60401e61](generation_71821316d70c92132db03cf68d66818246ec06f84f8e2fe1f926e20f60401e61/manifest.json) | 2026-10-23T04:02:59+08:00 | 通过 | 否 |
| [generation_56e6aa58ecb632013e557d87dfcadf5f50a658326a5544b71ecc17e9892e8cef](generation_56e6aa58ecb632013e557d87dfcadf5f50a658326a5544b71ecc17e9892e8cef/manifest.json) | 2026-10-23T09:14:13+08:00 | 未通过或未完成 | 否 |
| [generation_ce8d63791a039cff77e4d9251c8b06680ae27188306d96f3ae4dce950d3a765e](generation_ce8d63791a039cff77e4d9251c8b06680ae27188306d96f3ae4dce950d3a765e/manifest.json) | 2026-10-23T09:27:03+08:00 | 通过 | 是 |

| 识别快照 | 文件记录时间（北京时间） | 用途 |
|---|---|---|
| [media_05fc4760f5abc6de0ab92c34](media_05fc4760f5abc6de0ab92c34/media_extractions.jsonl) | 2026-10-23T04:02:59+08:00 | 不可变识别及视觉父子块，是否正式使用以generation清单为准 |

[已归档开发准备版本及各自时间](../.cache/development-runs/README.md)。归档只移动本任务未被发布引用的准备目录，没有删除数据。

`prepared_*`冻结上游、原文快照、入库资格与媒体；`media_*`冻结识别结果；`generation_*`才承载向量与存储候选。构建失败不切换正式指针。开发样例若归档至.cache/development-runs，保留其数据但不混在正式输出中。
