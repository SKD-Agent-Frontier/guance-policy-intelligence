# 一、接口概览

本契约定义研究准备接口，用于在真正提交 Agent 任务前，逐轮补全研究请求并返回可供后续执行的准备状态。此接口只解析、归纳和校验请求，不调用 Agent，不检索知识库，不执行 `/api/runs`，也不声称过滤或研究已经执行。

- 方法：`POST`
- 路径：`/api/research/prepare`
- 用途：创建或续接研究准备状态

请求和响应均使用 `Content-Type: application/json`。除文中定义的字段外，所有对象均禁止额外字段。字段名、枚举值和状态值区分大小写。

# 二、请求

## （一）请求体

- `mode`（`ResearchMode`）：必填，必须由用户显式提供。只接受 `qa`、`extract`、`organize`、`compare`、`compare_plans` 五个字面值；未知值返回 HTTP 422。模式由请求字段决定，不从 `message` 或澄清回答推断、补填或切换。
- `message`（`string`）：必填。原始用户请求；去除首尾空白后不得为空，长度不得超过 4,000 个 Unicode 字符。服务端保留原文，不静默改写或截断。
- `state`（`object`）：可选。续接已有准备状态时必填，仅允许 `state_id` 和 `revision`。
- `state.state_id`（`string`）：不透明状态标识，由服务端签发；必须为非空字符串。客户端不得解析、构造或替换其内容。
- `state.revision`（`integer`）：服务端签发的非负版本号。布尔值、浮点数和字符串均无效。

不带 `state` 的请求创建新状态，并将显式 `mode` 绑定到该状态；带 `state` 的请求须同时提交该状态最近一次响应中的 `state_id` 和 `revision`。续接请求的 `mode` 必须与状态绑定的 mode 相同，否则返回 HTTP 409。状态创建后，服务端不得因 message 内容或澄清回答而自动切换 mode；可向用户建议另建所需 mode 的新状态，但不得替用户切换。不得在请求中传入状态正文、过期时间、已确认条件或其他客户端自报状态。

缺少字段、多余字段、JSON 类型不符、未知 mode、空白消息、超过 4,000 字符的消息、畸形状态 token 或非法 revision 均返回 HTTP 422，响应体固定为 `{"detail":"请求参数无效"}`。五个已知 mode 中的 `extract`、`compare` 和 `compare_plans` 是结构有效但当前准备接口不支持的模式，按 HTTP 200 + `blocked` 处理。非 JSON 或无法解析的 JSON也返回相同 envelope。

## （二）TypeScript 请求类型

```ts
export type ResearchMode =
  | "qa"
  | "extract"
  | "organize"
  | "compare"
  | "compare_plans";

export interface PreparationToken {
  state_id: string;
  revision: number;
}

export interface ResearchPreparationRequest {
  mode: ResearchMode;
  message: string;
  state?: PreparationToken;
}
```

未知 mode 不属于业务 blocked 情形，严格按 422 拒绝。业务 blocked 仅用于五个已知 mode 中当前准备接口不支持的模式或其他无法安全继续的约束。

# 三、响应

所有业务状态均返回 HTTP 200。响应对象严格包含以下字段，不以字段缺失表达空值：

- `status`（`PreparationStatus`）：`needs_clarification`、`ready` 或 `blocked`。
- `mode`（`ResearchMode`）：本轮请求中由用户显式提交的合法模式。
- `message`（`string`）：面向调用方的本轮说明；不代表 Agent 答案。
- `options`（`PreparationOption[]`）：可供用户选择的澄清项；无待澄清项时为空数组。
- `state`（`PreparationState`）：服务端保存并签发的当前权威状态。
- `request`（`PreparedRequest | null`）：`ready` 时为可交给后续执行端的请求；其他状态为 `null`。
- `warnings`（`string[]`）：时间精度、范围或其他执行边界提示；无提示时为空数组。

`PreparationOption` 形状为 `{ "id": string, "question": string, "choices": string[] }`。`choices` 可为空数组，表示用户用自然语言回答。选项只表示待用户确认的信息，不代表已确认事实。

`PreparationState` 包含以下全部字段：

- `state_id`（`string`）：服务端签发的不透明状态标识。
- `revision`（`integer`）：本状态版本；每次成功接受的新一轮消息后递增。
- `expires_at`（`string`）：RFC 3339 UTC 时间戳，表示状态失效时刻。
- `mode`（`ResearchMode`）：创建状态时由用户显式提交并绑定的模式；澄清过程不可更改。
- `original_request`（`string`）：用户最初提交的原始消息。
- `topic`（`string | null`）：已识别的研究主题。
- `time_scope`（`TimeScope | null`）：已识别的时间范围及精度。
- `area`（`string | null`）：已识别的地域范围；开放范围可为 `null`。
- `dimensions`（`string[]`）：已确认的分析或提取维度。
- `confirmed_conditions`（`Condition[]`）：用户明确确认的研究条件。
- `pending_conditions`（`Condition[]`）：尚待用户确认或补充的条件。
- `completed_questions`（`CompletedQuestion[]`）：已回答的澄清项及其回答。
- `clarification_count`（`integer`）：已完成的澄清轮数，从 0 开始。
- `condition_statuses`（`ConditionExecutionStatus[]`）：各条件的准备/执行适用状态；此接口只产生准备态，不执行条件。

`TimeScope` 为 `{ "start": string | null, "end": string | null, "precision": "exact" | "year" | "range" | "unspecified", "timezone": string | null }`。`Condition` 为 `{ "id": string, "text": string }`；`CompletedQuestion` 为 `{ "question_id": string, "answer": string }`。

`ConditionExecutionStatus` 为 `{ "condition_id": string, "status": "executable_filter" | "request_only" | "unrestricted" | "pending", "value": string | null, "reason": string }`。这是准备阶段的条件分类，不是执行结果。prepare 阶段没有任何过滤已执行：

- `executable_filter`：地区条件仅在单一地区可被现有确定性路由识别时使用。时间条件仅限当前路由明确支持的五年规划专名：“十一五”“十二五”“十三五”“十四五”“十五五”。该状态表示后续调用 `/api/runs` 时条件可从 `question` 解析并交给 RAG 硬过滤；不表示 prepare、路由或 RAG 已执行过滤。真正的执行状态只能根据后续 `/api/runs` 返回的 `RunResult` 及其中的 `route` 判断。任何其他时间表述，包括任意公历年份、年份区间、起止日期及相对日期，均不得标为时间类 `executable_filter`。
- `request_only`：条件只能保留在 `question` 的自然语言请求中，不能保证作为结构化过滤执行；`value` 保留用户条件，`reason` 说明限制。
- `unrestricted`：用户明确确认不限制该条件；`value` 写明“不限”及对应条件，`reason` 说明由用户确认开放范围。
- `pending`：条件尚待用户回答或确认；`value` 为已知表达或 `null`，`reason` 说明待确认原因。

所有公历年份、年份区间、起止日期、日期/时刻和相对日期（包括“2021—2023年”“近十年”）一律标为 `request_only`，并在 `warnings` 明确写出“不保证按发布日期精确过滤”。只有前述五个被当前路由支持的具名五年规划时期可作为时间类 `executable_filter`。用户确认按原条件继续后可进入 `ready`，但必须保留 `request_only` 状态和 warning；也可让用户调整条件。不得将其他年份或时间范围伪装成规划时期。

`PreparedRequest` 必须直接符合现有 `POST /api/runs` 的请求 schema：`{ "task_type": ResearchMode, "question": string, "targets"?: string[] | null, "dimensions"?: string[] | null }`。其中 `task_type` 和 `question` 必填，`targets`、`dimensions` 可省略或为 `null`，遵循现有 `/api/runs` 契约；不得扩展 `/api/runs` schema。准备响应的 `request` 可原样作为 `/api/runs` JSON 请求体发送；不得要求调用方把 mode/message/topic/time_scope/area/conditions 再映射为其他字段。每个 `executable_filter` 条件必须无损写入 `question`，确保后续确定性路由可解析；如果不能无损写入，必须将其降为 `request_only` 或继续澄清，不得标为可执行过滤。所有其他条件也必须准确保留在 `question` 或现有 `targets`、`dimensions` 中；`warnings` 和条件分类仍须保留在准备响应中供调用方展示。

服务端在字段尚未识别或用户尚未确认时使用 `null`、空数组或 `pending`，不得把推断内容伪装成用户确认值。`request` 中的消息是整理后的执行请求；`state.original_request` 始终保留用户原话。

```ts
export type PreparationStatus =
  | "needs_clarification"
  | "ready"
  | "blocked";

export interface PreparationOption {
  id: string;
  question: string;
  choices: string[];
}

export interface TimeScope {
  start: string | null;
  end: string | null;
  precision: "exact" | "year" | "range" | "unspecified";
  timezone: string | null;
}

export interface ConditionExecutionStatus {
  condition_id: string;
  status: "executable_filter" | "request_only" | "unrestricted" | "pending";
  value: string | null;
  reason: string;
}

export interface PreparedRequest {
  task_type: ResearchMode;
  question: string;
  targets?: string[] | null;
  dimensions?: string[] | null;
}
```

# 四、状态与生命周期

## （一）状态含义

- `needs_clarification`：仅 `qa`、`organize` 可用。服务端返回一个或多个待回答选项并保存本轮状态；`request` 为 `null`。
- `ready`：仅 `qa`、`organize` 可用，且必要信息已明确或用户确认按 request_only 条件继续；`options` 为空数组，`request` 是可直接提交给 `/api/runs` 的请求。
- `blocked`：已知但当前准备接口不支持的 `extract`、`compare`、`compare_plans`，或其他无法安全继续的约束。响应的 `message` 说明原因；`request` 为 `null`。这三个已知模式不得伪造 `targets` 或返回可执行请求。

未知 mode 是结构校验错误，返回 HTTP 422，不返回业务状态。

`qa` 的单一主题或笼统请求（例如“数字经济”）应追问用户想了解的方面，不固定追问时间范围。用户已提出明确问题时（例如“什么是数字经济？”），可直接返回 `ready`；除非请求本身存在其他必要歧义，不要强制增加澄清轮次。

`ready` 仅表示准备信息完整，不表示检索、分析或回答已经执行成功。客户端必须由用户或上层流程另行决定是否调用 `/api/runs`。

## （二）服务端状态信任边界

`state_id` 和 `revision` 是定位及版本检查凭据，不是身份认证凭证。服务端必须从自己的存储读取准备状态；不得信任客户端提供或回显之外的状态正文，也不得仅凭 `state_id` 推断用户身份或授权。客户端传入状态对象只允许包含 token 两字段。

状态中保留的 `original_request`、主题、时间、地域、维度、条件、问题回答和状态标记均由服务端产生或按用户本轮回答更新。响应中的状态正文仅供显示；下一轮只发送 `state_id`、`revision` 和新 `message`。不得从客户端传回的显示数据恢复或覆盖服务端状态。

## （三）过期、并发与幂等

- `expires_at` 是服务端状态期限的权威值。已过期或无法找到的状态返回 HTTP 409；不得默默创建新状态或丢弃之前的澄清上下文。
- 模式与已存状态不匹配，或 revision 与服务端当前 revision 冲突时，返回 HTTP 409。错误体为 `{"detail":"研究准备状态已失效或冲突"}`。调用方应取得最新状态后由用户决定是否继续，不应自动重放可能改变含义的消息。
- 每轮成功接受的消息原子地消费当前 revision 并生成下一 revision，避免并发更新覆盖已确认条件。两个并发请求竞争同一 revision 时，至多一个推进状态；另一请求若不是同一请求的幂等重试，返回 409。
- 对同一状态版本、相同 `mode` 和相同 `message` 的立即重试必须幂等：返回第一次处理的同一 HTTP 状态及 JSON 响应，不重复计数、不新增澄清轮次，也不重复推进 revision。实现应在服务端识别该重试；不得把任意旧 revision 的不同消息当作重试。
- 无状态的新请求如因网络重试被重复提交，服务端可分别建立状态；客户端应保存首次响应的 token 并用它继续交互。

此接口不启动 Agent、不调用 `/api/runs`，也不在后台排队执行。一次澄清轮次只更新研究准备状态。

# 五、校验与错误

- HTTP 200：返回完整准备响应，状态为 `needs_clarification`、`ready` 或 `blocked`。
- HTTP 409：响应体为 `{"detail":"研究准备状态已失效或冲突"}`，用于状态已过期、未知、模式不匹配或 revision 冲突。
- HTTP 422：响应体为 `{"detail":"请求参数无效"}`，用于 JSON 形状、严格类型、额外字段、消息长度/空白或状态 token 格式无效。
- HTTP 503：响应体为 `{"detail":"服务尚未就绪"}`，表示 Web API 尚未就绪。
- HTTP 500：响应体为 `{"detail":"服务内部错误"}`，表示未预期服务端异常；不得向客户端返回堆栈、异常文本或私有配置。

HTTP 错误响应与 HTTP 200 的业务状态是不同层次。只有 200 响应具有本契约定义的准备响应结构。状态 token 字段类型/形状错误属于 422；格式正确但找不到对应状态属于 409。

# 六、时间范围与执行限制

用户明确给出的年份、起止日期、单一日期或时刻必须原样保留其精度和边界。`exact` 表示请求中的时间点或边界必须精确满足；不得擅自扩大为整年、邻近日期、默认时间窗或“近期”。对后续执行不能保证精确发布日期过滤的日期/时刻及“近十年”，须先澄清是否按 `request_only` 继续或调整条件；用户确认继续后可返回 `ready`，但必须保留 `request_only` 状态并在 `warnings` 明确写出“不保证按发布日期精确过滤”。不得返回声称可执行但已改变时间范围的 `ready` 请求，也不得声称已经完成过滤。

未指定时间范围时，使用 `precision: "unspecified"`、`start: null`、`end: null`，不得自行填入默认年份或日期。开放范围（例如未限定时间的主题研究）允许 `time_scope` 为 `null` 或 `unspecified`，不得仅因范围开放而阻断。

本契约不设定可检索的最早年份、精确时间跨度上限、时区默认值或状态存储的固定存活分钟数；服务端不得在没有明示配置和用户告知的情况下编造这些限制。若部署实现存在此类边界，必须以 `warnings` 或 `blocked` 说明，并保持用户原始时间约束不变。`expires_at` 必须由服务端实际状态存储期限生成。

# 七、`/api/runs` 兼容

现有 `POST /api/runs` 契约保持不变。该接口仍接收 `question`、可选 `task_type`、`targets`、`dimensions` 和 `session_id`，支持 `qa`、`extract`、`organize`、`compare`、`compare_plans`，并按原契约返回完整 `RunResult`。研究准备接口不改变 `/api/runs` 的请求、响应、状态码、Agent 调用或证据语义。

只有 `ready` 响应包含执行请求。调用方可将 `response.request` JSON 对象原样发送到 `/api/runs`，无需改名、重组或二次映射。`needs_clarification` 和 `blocked` 的 `request` 必须为 `null`。准备接口的 warning 和 condition 状态不是 RunResult，也不能被解释为 Agent 已完成过滤或任务执行。

# 八、示例

## （一）QA 单一主题需要澄清

请求：

```json
{"mode":"qa","message":"数字经济"}
```

响应（HTTP 200，字段结构示意）：

```json
{
  "status":"needs_clarification",
  "mode":"qa",
  "message":"请问你想了解数字经济的哪个方面？",
  "options":[{"id":"aspect","question":"你想了解数字经济的哪个方面？","choices":["概念与发展","产业政策","发展现状","其他"]}],
  "state":{"state_id":"opaque-token","revision":0,"expires_at":"2026-10-30T12:00:00Z","mode":"qa","original_request":"数字经济","topic":"数字经济","time_scope":null,"area":null,"dimensions":[],"confirmed_conditions":[],"pending_conditions":[{"id":"aspect","text":"希望了解的方面"}],"completed_questions":[],"clarification_count":0,"condition_statuses":[{"condition_id":"aspect","status":"pending","value":null,"reason":"单一主题未说明关注方面"}]},
  "request":null,
  "warnings":[]
}
```

## （二）QA 明确问题直接就绪

```json
{
  "status":"ready",
  "mode":"qa",
  "message":"研究请求已准备完成。",
  "options":[],
  "state":{"state_id":"opaque-token","revision":0,"expires_at":"2026-10-30T12:00:00Z","mode":"qa","original_request":"什么是数字经济？","topic":"数字经济","time_scope":null,"area":null,"dimensions":[],"confirmed_conditions":[],"pending_conditions":[],"completed_questions":[],"clarification_count":0,"condition_statuses":[]},
  "request":{"task_type":"qa","question":"什么是数字经济？"},
  "warnings":[]
}
```

## （三）多轮 organize 澄清

`organize` 可进行多轮澄清。每轮 mode 仍由调用方显式提交且必须与状态绑定值相同；请求只提交 token 和新回答，不回传完整状态：

```json
{"mode":"organize","message":"限定在 2021 年至 2023 年，按支持对象和工具分类","state":{"state_id":"opaque-token","revision":1}}
```

成功响应仍为 HTTP 200；2021—2023 是公历年份区间，必须始终将该时间条件标为 `request_only`，并在 `warnings` 写出“不保证按发布日期精确过滤”，无论路由是否支持某个五年规划专名。该范围及分类维度须保留在 `question`；用户确认继续或信息齐备后可返回 `ready`，request 仍直接使用 `task_type` 和 `question` 字段。例如响应的条件状态和提醒为：`{"condition_id":"time_scope","status":"request_only","value":"2021—2023年","reason":"公历年份区间不作为硬过滤条件"}`，`warnings: ["不保证按发布日期精确过滤"]`。若仍缺少必要条件，返回 `needs_clarification` 和下一组选项。

## （四）精确时间为 request_only

```json
{
  "status":"ready",
  "mode":"qa",
  "message":"已按你的确认保留精确发布日期要求；该条件仅随请求传递。",
  "options":[],
  "state":{"state_id":"opaque-token","revision":1,"expires_at":"2026-10-30T12:00:00Z","mode":"qa","original_request":"查询 2024-03-12 14:30 发布的政策","topic":"政策","time_scope":{"start":"2024-03-12T14:30:00","end":"2024-03-12T14:30:00","precision":"exact","timezone":null},"area":null,"dimensions":[],"confirmed_conditions":[{"id":"publication_time","text":"2024-03-12 14:30 发布"}],"pending_conditions":[],"completed_questions":[{"question_id":"continue_request_only","answer":"确认继续"}],"clarification_count":1,"condition_statuses":[{"condition_id":"publication_time","status":"request_only","value":"2024-03-12 14:30 发布","reason":"精确发布日期无法作为结构化过滤执行，仅保留在问题文本中"}]},
  "request":{"task_type":"qa","question":"查询 2024-03-12 14:30 发布的政策"},
  "warnings":["不保证按发布日期精确过滤"]
}
```

## （五）可执行筛选的含义和可调整路径

例如，用户确认单一地区“北京市”后，若现有确定性路由可以从 `question` 无损识别，可将地区条件标为 `executable_filter`。时间条件只有“十一五”“十二五”“十三五”“十四五”“十五五”这些具名且被当前路由支持的五年规划时期，才可标为时间类 `executable_filter`。prepare 仍未执行过滤；该标记仅表示后续 `/api/runs` 可将条件解析并交给 RAG 硬过滤。是否真正执行只能看后续 RunResult 的 `route`。若路由不能无损识别 `question` 中的条件，改标 `request_only` 或继续澄清；任意公历年份或区间都必须保持 `request_only`。

精确时间确认和可调整路径：

在用户确认继续之前，服务端将精确日期/时刻和“近十年”设为 `pending`，提供继续按 `request_only` 传递或调整条件的选项。用户确认后可返回 `ready`，同时将条件标为 `request_only` 并在 `warnings` 中写出“不保证按发布日期精确过滤”。服务端不得把它标成 `executable_filter`，也不得声称已执行过滤。用户选择调整时，更新自然语言请求和对应状态；mode 保持原值。

## （六）开放时间范围

用户请求“梳理北京市数字经济政策，不限时间”时，可返回 `ready`，将时间条件标记为 `unrestricted`，`value` 写明用户确认的“不限时间”，并在 `reason` 说明由用户明确开放范围；不得擅自代入默认起止时间。

## （七）已知但当前不支持的 mode

`extract`、`compare`、`compare_plans` 均为合法的 Literal mode，但研究准备接口当前不支持它们。每个模式都必须返回 HTTP 200 + `status: "blocked"`、`request: null`，解释当前不支持；不得伪造 `targets`、不得进入澄清/就绪状态。可建议用户新建 `qa` 或 `organize` 状态，但不得自动切换当前 mode。

以下展示 `extract`；将请求和响应的 mode 替换为 `compare` 或 `compare_plans` 时规则完全相同：

```json
{
  "status":"blocked",
  "mode":"extract",
  "message":"extract 当前不能通过研究准备接口执行。你可以另建 qa 或 organize 模式的请求。",
  "options":[],
  "state":{"state_id":"opaque-token","revision":0,"expires_at":"2026-10-30T12:00:00Z","mode":"extract","original_request":"提取政策支持要素","topic":null,"time_scope":null,"area":null,"dimensions":[],"confirmed_conditions":[],"pending_conditions":[],"completed_questions":[],"clarification_count":0,"condition_statuses":[]},
  "request":null,
  "warnings":[]
}
```

## （八）mode 不可自动切换

澄清答案中即使出现“改做比较分析”等内容，也只能作为本轮 message 处理，不得切换状态 mode。若请求 token 属于 `qa` 而显式 mode 提交为 `organize`，HTTP 409 返回 `研究准备状态已失效或冲突`；响应可提示用户新建 `organize` 请求。

## （九）ready 请求直接提交 `/api/runs`

本示例中的 2021—2023 公历年份区间在准备响应中仍必须标为 `request_only`，并带有 warning `不保证按发布日期精确过滤`；下方 JSON 仅展示可原样发送的 `PreparedRequest`，条件状态和 warning 留在准备响应，不扩展 `/api/runs` 请求 schema。

准备响应中的 `request`：

```json
{"task_type":"organize","question":"梳理 2021 年至 2023 年北京市数字经济政策，按支持对象和工具分类","dimensions":["支持对象","工具"]}
```

将该对象原样传给 `POST /api/runs`：

```json
{
  "task_type":"organize",
  "question":"梳理 2021 年至 2023 年北京市数字经济政策，按支持对象和工具分类",
  "dimensions":["支持对象","工具"]
}
```
