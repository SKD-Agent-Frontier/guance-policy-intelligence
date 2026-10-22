# 向量化批次时间索引

更新时间：2026-10-23T00:14:29.876600+08:00。

**尚无正式向量批次，没有已激活版本。** 下列都是准备批次，不是完成的向量库。

| 目录 | 创建时间（北京时间） | 范围 | 文档 | 纳入/排除文本块 | 当前代码配置 | 原文验证 |
|---|---|---|---:|---:|---|---|
| [prepared_3e287fc5725a0247075fa2e3e25c18ccbccc8ffa2e5252bfc513d8cb6eb2d4f7](prepared_3e287fc5725a0247075fa2e3e25c18ccbccc8ffa2e5252bfc513d8cb6eb2d4f7/manifest.json) | 2026-10-22T22:42:52+08:00 | 5篇样例 | 5 | 1282/15 | 开发版本 | 通过 |
| [prepared_bc66e669996f585ba220112c9906843923a06ce77e593d76d7849ceebe2f5267](prepared_bc66e669996f585ba220112c9906843923a06ce77e593d76d7849ceebe2f5267/manifest.json) | 2026-10-22T22:43:18+08:00 | 全量 | 506 | 43446/96 | 开发版本 | 通过 |
| [prepared_7dae0fbba8c3ef9e4f466fb30b79cf5be4a69d97be18a504ca739aa5d3bc2079](prepared_7dae0fbba8c3ef9e4f466fb30b79cf5be4a69d97be18a504ca739aa5d3bc2079/manifest.json) | 2026-10-22T22:53:36+08:00 | 5篇样例 | 5 | 1282/15 | 开发版本 | 通过 |
| [prepared_9f6c21b9e780078b4c75f2e412a2160e1ef7b09f50d3dca381f6277bd1bcafac](prepared_9f6c21b9e780078b4c75f2e412a2160e1ef7b09f50d3dca381f6277bd1bcafac/manifest.json) | 2026-10-22T22:54:06+08:00 | 全量 | 506 | 43446/96 | 开发版本 | 通过 |
| [prepared_04504442b51ed2df4b34775ea6c2ce78f87ff8d773fbffcdf87ec501eedd7f36](prepared_04504442b51ed2df4b34775ea6c2ce78f87ff8d773fbffcdf87ec501eedd7f36/manifest.json) | 2026-10-22T23:06:18+08:00 | 全量 | 506 | 43446/96 | 开发版本 | 通过 |
| [prepared_efb082feba5691244adf80960c30c64fe23bb7973d6b862a6febba59ded790fc](prepared_efb082feba5691244adf80960c30c64fe23bb7973d6b862a6febba59ded790fc/manifest.json) | 2026-10-22T23:49:25+08:00 | 全量 | 506 | 43446/96 | 开发版本 | 未完成 |
| [prepared_139d43cdf83ed890c8d2ce502f5fada51b6ec36fbe4dc4cab8f10019dfce6945](prepared_139d43cdf83ed890c8d2ce502f5fada51b6ec36fbe4dc4cab8f10019dfce6945/manifest.json) | 2026-10-23T00:13:11+08:00 | 全量 | 506 | 43446/96 | 一致 | 通过 |

最新全量准备版本：`prepared_139d43cdf83ed890c8d2ce502f5fada51b6ec36fbe4dc4cab8f10019dfce6945`。准备版本与向量发布状态分别记录。

`prepared_*`冻结上游、原文快照、入库资格与媒体；`media_*`冻结识别结果；`generation_*`才承载向量与存储候选。构建失败不切换正式指针。开发样例若归档至.cache/development-runs，保留其数据但不混在正式输出中。
