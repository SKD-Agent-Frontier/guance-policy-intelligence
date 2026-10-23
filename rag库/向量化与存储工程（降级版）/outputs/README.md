# 本地版输出批次

本目录的三个数据目录承担不同职责，合起来构成一套本地发布，不是重复存放的三套向量库。数据库实际位于`.runtime/local/`。

| 本地目录创建时间（北京时间） | 目录 | 作用 | 状态 |
|---|---|---|---|
| 2026-10-23T15:47:23+08:00 | `local_generation_e125cd0f3a4faf24860081796440a6e73f26196edb6bb838dd93c7a92ec1078f` | 本地向量表示计划、媒体/去重/校验报告 | 当前正式发布（独立全量复核通过） |
| 2026-10-23T15:30:26+08:00 | `media_93cbe8cb9e3a9c8a6098fb8e` | 图片识别、视觉父子块及失败记录 | 继承原工程当前发布数据 |
| 2026-10-23T15:30:21+08:00 | `prepared_822ec6f1de9ba2d1542e1a22a63111a3e0fb8bfe2d908cf77a6a438518f0bac5` | 冻结原文、原切块、图片快照、资格清单 | 继承原工程当前发布数据 |

当前正式版本：`local_generation_e125cd0f3a4faf24860081796440a6e73f26196edb6bb838dd93c7a92ec1078f`。激活时间：`2026-10-23T08:27:46.100507+00:00`（UTC），北京时间`2026-10-23T16:27:46+08:00`。本工程`当前批次.json`为可读副本，SQLite runtime为发布权威。三个数据目录被同一发布共同引用。

## 文件作用

- `prepared_.../manifest.json`：冻结来源表哈希、上游、配置与快照入口。其余JSONL保留文档、基础单元、章节、父子块、关系、图片和异常；`parent_contexts.jsonl`保留跨父块断词补充上下文。
- `media_.../media_extractions.jsonl`、`visual_parents.jsonl`、`visual_chunks.jsonl`：继承的真实识别及视觉切块；失败/排除分开记录。
- `local_generation_.../manifest.json`：本地集合、向量空间和冻结来源位置。
- `representations.jsonl`：每个来源表示的独立ID、实际输入哈希及4096维向量空间身份；不在JSONL内重复存向量。
- `representation_excluded.jsonl`、`lexical_members.jsonl`、`media_summary.json`：表示排除、词法成员和媒体识别缺口。
- `exact_groups.jsonl`、`near_pairs.jsonl`、`dedup_summary.json`：原版重复候选与统计，不删除政策来源。
- `migration_inheritance.json`：硬链接/普通副本等继承方式统计。
- `progress.json`、`embedding_summary.json`：进度检查点和最近一次执行统计，不代表新的业务版本。
- `validation.json`：全量来源、覆盖、逐向量、SQLite、BM25复核。
- `storage_smoke.json`：真实本地持久化、过滤、父块回取的抽样检查。

不可变快照/缓存同盘优先硬链接；当前发布不自动删旧版本。后续新建批次必须补本表创建时间、用途、是否当前，避免把prepared/media/发布三个层次误认为重复版本。
