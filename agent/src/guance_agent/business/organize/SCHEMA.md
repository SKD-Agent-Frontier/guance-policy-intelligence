# organize/1 结构化结果

`OrganizeModule` 通过共享 `BusinessOutcome` 返回 `answer`、`claims`、`structured`、
`insufficient`。导入本模块只导出类，不自动注册；生产统一入口 `guance_agent.main`
负责显式注册 `organize`、`compare` 与 `compare_plans`。开放主题与指定 targets 均支持；多地区、
多规划期的开放主题由底座要求缩小范围。以下内容是字段契约，不是真实运行结果。

## 顶层字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `schema_version` | string | 固定为 `organize/1` |
| `scope` | object | 用户要求、实际检索条件与候选后过滤范围 |
| `coverage` | object | 本轮发现、读取、摘要覆盖程度及限制 |
| `policies` | object[] | 过滤后的全部候选，每个 planid 一次；另附未命中的显式 targets |
| `groups` | object[] | 仅由保留的 Claim 生成的主题分组 |
| `verification` | object | 事实核验与分析字段的边界 |

## scope

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `topic` | string | 确定性清理路由子查询得到的主题；空结果回退到截断子查询 |
| `areas` / `fyps` | string[] | 路由识别的地区与规划期，不代表一定作为检索参数生效 |
| `plantypes` | string[] | 用户明确要求的类型：总体规划纲要、专项规划、政府工作报告、规划文件 |
| `categories` | string[] | 用户要求的分类类别，最多八项；没有则由模型建议标签 |
| `dimensions` | string[] | 原样传入的分析重点，不参与筛选 |
| `targets` | string[] 或 null | 底座解析的完整指定对象集合；null 表示开放主题 |
| `effective.queries` | object[] | 实际调用过的检索记录，每项见下表 |
| `effective.area` / `effective.fyp` | string 或 null | 开放主题从路由继承的单值过滤；固定 targets 模式不自动继承，故为 null |
| `effective.top_k` | integer | 每条查询使用的 `settings.top_k_default` |
| `post_filters` | object[] | 类型后过滤的实际执行记录；无类型要求时为空 |
| `unapplied` | object[] | 无法按原形式执行的条件及处理说明 |

`effective.queries[]`：`query` 为实际短查询（最多 40 字符）；`status` 为原始
ToolResult 状态；`hits` 为此次成功查询的片段数；`new_documents` 为本次首次发现的
planid 数。所有查询最多三条；不把失败查询解释为不存在相关政策。

类型后过滤记录：`condition="plantypes"`、`values: string[]`、
`excluded_unknown: integer`、`excluded_mismatch: integer`、`note: string`。
多种类型按 OR 匹配；无法判断任一所选类型的候选计入 unknown，不补齐缺失元数据。
过滤只作用于本轮候选集，不是全库严格筛选。`unapplied[]` 使用
`{condition: "plantypes", reason: "检索接口无类型预过滤，已改为对本轮候选后过滤"}`。

## coverage

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `documents_found` | integer | 成功查询的所有 Hits 经共享 `group_by_document` 按 planid 去重后的数量，过滤前 |
| `documents_after_filter` | integer | 类型后过滤后候选数；不含未命中的 targets |
| `documents_read` | integer | 至少一个成功读取且证据登记有效的窗口的文献数 |
| `documents_summarized` | integer | 至少一条有效 Claim 的文献数 |
| `chunks_read` | integer | 成功读取并形成窗口的 chunk 总数 |
| `limits` | string[] | 见下方枚举；空数组不意味着覆盖全库或全文 |
| `note` | string | 固定覆盖声明及不足、失败、预算限制的具体说明 |

`limits`：`max_docs_read`（超过六篇选文上限）、`max_reads`（达到有效读取上限）、
`stopped_for_tool_budget`（读取服务抛出 TOOL_BUDGET）、`stopped_for_time`
（剩余时间不足 75 秒，为模型保留时间）。有效读取上限为
`max(0, min(10, settings.max_tool_calls - 实际检索次数 - 2))`。
每篇最多两个 chunk，先读各篇第一块，再读第二块；优先高分与不同 section_path。

覆盖声明固定强调：`documents_found` 仅为本轮候选数量；摘要仅基于已读取片段。
少于两篇有效摘要时 `BusinessOutcome.insufficient=true`，底座结果标记证据不足。

## policies[]

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `planid` | string | 稳定政策标识 |
| `title` / `area` / `fyp` / `admin_level` / `plantype_label` | string 或 null | 该 planid 首条 Hit 的身份元数据；未知保留 null，不用后条命中补齐 |
| `read_status` | string | 下方状态枚举 |
| `relevance` | string 或 null | 已读文献模型分析的 `high`、`medium`、`low`、`irrelevant`；无效值降为 low；未读取或模型未返回该篇时 null |
| `relevance_note` | string | 程序模板生成的检索查询词及保留的主题标签；不使用模型自由解释，也不声称每个查询都命中该篇 |
| `topics` | string[] | 该篇保留 Claim 对应的主题，按首次出现顺序去重 |
| `summary` | string 或 null | 只将该篇 `claim_indexes` 对应的 Claim.text 按顺序用“；”拼接；无有效 Claim 时 null |
| `claim_indexes` | integer[] | 对最终 `RunResult.claims` 的零基索引 |
| `chunk_ids_hit` | string[] | 首次出现顺序的去重检索片段编号 |
| `chunks_read` | integer | 成功读取并形成窗口的 chunk 数 |
| `full_text_covered` | boolean | 本版本恒为 false |
| `read_attempts` | object[] | 每次完成的读取记录 `{chunk_id, status}`；保留原始失败状态供解释 |

`read_status`：`summarized`、`read_no_valid_points`、`irrelevant`、`no_text`、
`read_failed`、`not_read`、`not_retrieved`。`not_retrieved` 只表示指定对象未被查询命中，
不表示对象不存在。已命中但被类型过滤排除的 targets 不冒充未命中。
未读取文献的 relevance、summary 为 null，topics、claim_indexes 为空。

`read_attempts[].status` 保留 ToolResult 状态；成功返回空正文记为 `no_text`；
成功响应未通过窗口登记检查时记为 `unregistered_evidence`。
这是对方案字段的补充，避免把 `not_found`、`invalid_argument`、`timeout` 等信息
统一为 `read_failed` 后丢失原因；没有修改共享契约。

## groups[] 与 verification

`groups[]` 包含 `topic: string`、`planids: string[]`、`claim_indexes: integer[]`。
相同清洗标签确定性合并；不做语义合并。最多八组；超出时保留前七个非“其他”标签，
其余归入“其他”，保证总数包含“其他”仍不超过八组。同一政策可出现在多个组中。

`verification.policy_facts="claims_only"`。
`analysis_fields=["relevance", "relevance_note", "topics", "groups"]`。
`note` 说明：只有 claims 的原文引句和数字经过确定性字面核验；相关性与分类是分析
结果，不是政策事实。字面核验不证明语义支持，也不证明覆盖全文。

## 引用、失败与答案边界

- 每条 Claim 必须有一个登记的 chunk_id 和恰好一句 quote，最多十六条、每篇最多三条。
- quote 来自实际展示窗口的精确切片；窗口最多 900 字，合计最多 6000 字。
  未展示文本不参与定位。非章节起点且缺少前置句界的窗口，保守跳过开头候选句。
  展示窗口被截短时，延伸至窗口末尾且没有终止符的尾句不参与定位，防止数字被截断后误采信。
- Claim 中所有数字必须在同一句中按数字边界出现，避免 `100` 借用 `1000`。
- 正常调用模型一次；仅返回文本解析失败且剩余时间大于 75 秒时再试一次。
  服务直接抛出的错误原样上抛；最多两次模型调用。RUN_TIMEOUT、MAX_TURNS 不降级为成功。
- answer 由模板、主题、实际范围、合法标签和 Claim 文本构成；不写入政策标题、planid、
  文献篇数等统计。程序清理主题时产生的孤儿数字片段不进入 answer。
- 没有有效 Claim 时模块返回空 answer、空 claims、空 structured、insufficient=true。
  底座负责保留成功检索的文献身份，格式为 `{documents, verification}`，其中
  `policy_facts="unverified"`。无命中时底座返回 `{}`，不是 `organize/1` 空壳。
- 统一核验删除或修改 Claim 时底座会清空 structured，消费方不得假设总存在 organize/1。

## 真实样例生成

先按 `agent/README.md` 配置真实 BM25 运行库与模型，再在 `agent/` 运行：

```powershell
python scripts/organize_real_run.py
```

脚本只在当前进程注册模块，顺序运行三道验收问题。完整真实结果存入被忽略的
`agent/data/organize_runs/`；主问题通过真实多文献核验后生成本目录 `sample.json`。
未配置真实后端或运行未通过时，不生成或覆盖 sample。mock 测试输出不是 sample。

`sample.json` 是统一 `submit()` 返回的完整 `RunResult`，`organize/1` 业务结构位于
`structured` 字段；顶层 `status`、`evidence_status`、`claims`、`evidence_refs`、`meta`
等字段属于共享运行契约，`run_meta` 仅记录本次真实验收环境与指标，不属于
`organize/1` 字段契约。当前样例来自真实 BM25 RAG 与学校模型运行；提交前仅将
`run_id`、`session_id` 替换为脱敏占位值，政策内容、证据和验收指标保持原样。样例保留了
未摘要候选及 `full_text_covered=false`，不得据此宣称覆盖全库或任一政策全文。
