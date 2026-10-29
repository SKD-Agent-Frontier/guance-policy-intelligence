# 一、接口概览

本契约描述本地 React 前端调用管策智研 Web API 的方式。默认 Base URL 为 `http://127.0.0.1:8000`。一次 `POST /api/runs` 请求调用一次 Agent `submit()`；Web 层不自动识别任务意图、不组合多个任务，也不二次生成答案或重新核验引文。

## （一）接口列表

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | 检查 Web API 就绪状态 |
| `POST` | `/api/runs` | 提交一个 Agent 任务并等待完整结果 |

## （二）健康检查

就绪时返回 HTTP 200：

```json
{
  "status": "ok",
  "service": "guance-web-api"
}
```

服务尚未就绪时返回 HTTP 503，错误体见“HTTP 层错误”。健康检查只表示 Web API 服务已就绪，不证明真实 RAG 知识库或模型端点可用。

# 二、提交任务

## （一）请求体

`POST /api/runs` 使用 `Content-Type: application/json`。请求模型启用严格类型检查并禁止额外字段。

| 字段 | TypeScript 类型 | 要求 |
| --- | --- | --- |
| `question` | `string` | 必填；字符串类型由 HTTP 层检查，内容及长度等业务校验交给 Agent `submit()`。 |
| `task_type` | `TaskType` | 可选，省略时默认为 `qa`。前端应使用下列五种受支持值。 |
| `targets` | `string[] \| null` | 可选；目标数量、格式、重复项及特定任务的数量要求由 Agent 校验。 |
| `dimensions` | `string[] \| null` | 可选；提取或分析维度，由 Agent 处理。 |
| `session_id` | `string \| null` | 可选；用于 Agent 会话续接，不代表用户身份。 |

支持的 `task_type`：

| 值 | 功能 | 参数提示 |
| --- | --- | --- |
| `qa` | 政策智能问答 | 默认值。 |
| `extract` | 政策要素提取 | 可用 `targets` 指定政策、用 `dimensions` 指定提取维度；省略维度时 Agent 使用默认维度。 |
| `organize` | 政策文献自动梳理 | 可不传 `targets` 进行开放主题梳理；显式传入时由 Agent 校验目标。 |
| `compare` | 政策比较分析 | 通常需要 2–8 个不同对象，可通过 `targets` 指定。 |
| `compare_plans` | 多方案分析 | 通常需要 2–8 个不同对象；`dimensions` 可提供分析维度。 |

请求模型只做结构与严格类型校验，未将 `task_type` 限制为枚举。因此不支持的任务名、空问题、超长问题或不符合业务要求的目标可能通过 HTTP 结构校验，再由 Agent 返回 `status: "failed"` 的 RunResult。前端应只发送上述五种任务类型。自然语言中出现“比较”或“梳理”等词不会自动改变任务类型。

示例：

```json
{
  "question": "梳理十四五时期北京市数字经济相关政策",
  "task_type": "organize",
  "targets": null,
  "dimensions": null,
  "session_id": null
}
```

## （二）TypeScript 请求类型

```ts
export type TaskType =
  | "qa"
  | "extract"
  | "organize"
  | "compare"
  | "compare_plans";

export interface RunRequest {
  question: string;
  task_type?: TaskType;
  targets?: string[] | null;
  dimensions?: string[] | null;
  session_id?: string | null;
}
```

## （三）HTTP 请求校验

- 缺少必填字段、字段类型错误（包括数字、布尔值被当作字符串使用）或发送未声明的字段时，返回 HTTP 422。
- 数组元素也必须是字符串；例如 `targets: [123]` 返回 422。
- `task_type` 在 HTTP 层只校验为严格字符串。是否属于受支持任务、问题是否为空或超长、`session_id` 格式、对象数量和重复对象等业务条件由 Agent `submit()` 校验。
- 不要在前端假设所有业务无效输入都会触发 HTTP 422；Agent 正常返回的业务拒绝使用 HTTP 200 和失败 RunResult 表达。

# 三、RunResult 响应

Agent 返回的共享 `RunResult` 会完整序列化，保留其全部字段，包括业务层未来可能增加的合法字段。Web API 不过滤 `structured`、`claims` 或证据，也不改写状态。以下 TypeScript 类型对应当前共享 Agent schema；`structured` 的具体内容随 `task_type` 变化，应以 organize/compare 等 Agent 业务契约为准。

```ts
export type RunStatus = "completed" | "failed" | "limit_reached";
export type EvidenceStatus = "verified" | "insufficient" | "none";

export interface Claim {
  text: string;
  chunk_ids: string[];
  quotes: string[];
  dimension: string | null;
  anchors: string[];
}

export interface RunError {
  code: string;
  message: string;
}

export interface EvidenceRef {
  document_id: string | null;
  document_version_id: string | null;
  generation_id: string | null;
  source_hash: string | null;
  section_id: string | null;
  section_char_start: number | null;
  section_char_end: number | null;
  offset_basis: string | null;
  offset_unit: string | null;
  body_origin: number | null;
  contract_version: string | null;
  quality_warnings: string[];
  planid: string;
  chunk_id: string;
  parent_id: string | null;
  title: string;
  area: string | null;
  fyp: string | null;
  admin_level: string | null;
  plantype_label: string | null;
  section_idx: number | null;
  section_path: string;
  char_start: number;
  char_end: number;
  chunk_char_start: number | null;
  chunk_char_end: number | null;
  truncated: boolean;
  source: "kb_search" | "kb_read";
  observed_ranges: Array<[number, number]>;
  complete: boolean;
  out_of_section: boolean;
}

export interface RunResult {
  run_id: string;
  session_id: string;
  task_type: string; // 正常调用为 TaskType；INVALID_INPUT 可能回显无效值。
  status: RunStatus;
  evidence_status: EvidenceStatus;
  answer: string;
  claims: Claim[];
  evidence_refs: EvidenceRef[];
  dropped_chunk_ids: string[];
  structured: Record<string, unknown> | null;
  warnings: string[];
  error: RunError | null;
  route: Record<string, unknown> | null;
  meta: Record<string, unknown>;
}
```

RunResult 中的空值应按本轮响应处理。尤其当 `structured` 为 `null` 时，应清除界面中上一次请求留下的结构化表格、政策分组、比较矩阵或方案分析，不得沿用旧内容或自行补造结果。

## （一）status 与 evidence_status

| 字段 | 值 | 前端处理 |
| --- | --- | --- |
| `status` | `completed` | Agent 任务正常完成；仍需结合证据状态、警告和业务结构解释结果。 |
| `status` | `failed` | Agent 返回业务失败；查看 `error`、`warnings` 和 `answer`。该结果仍是 HTTP 200。 |
| `status` | `limit_reached` | Agent 达到运行限制；展示限制状态及返回的部分信息，不要标记为完成。 |
| `evidence_status` | `verified` | Agent 的共享证据状态为已核验。 |
| `evidence_status` | `insufficient` | 证据不足；不得显示为已核验。 |
| `evidence_status` | `none` | 没有可用证据。 |

`status` 与 `evidence_status` 是不同维度。例如 `completed` 可以同时对应 `insufficient`；前端必须原样保留两者，不可把它转换成 `verified`。字面引文核验不等同于语义支持判断，也不代表覆盖全文。

## （二）结构化业务结果

`structured` 是共享 RunResult 中的 `object | null`，其内部结构取决于任务类型。不要为所有任务共用一套业务结构，也不要丢弃未识别字段。

- `qa` 的 `structured` 为 `null`。
- `extract` 的结构包含提取维度、文档、要素和缺失项；实际形状以 Agent 输出为准。
- `organize` 通常使用 `organize/1` 结构，包含范围、覆盖、政策、主题分组和核验边界；无有效 Claim 时可能返回不同的降级结构或空对象。
- `compare` 与 `compare_plans` 使用比较业务结构，包含对象、维度、矩阵、覆盖说明及分析缺口；多方案任务还可包含评价标准与方案分析。

业务结构内的 Claim 索引、chunk ID 和原文引句用于关联顶层 `claims` 与 `evidence_refs`。前端应同时展示结构化结果、警告和证据状态，不应将候选覆盖说成全文或全库覆盖。

# 四、错误响应

HTTP 层错误与 Agent 业务结果是两类响应。下表只列出应用代码显式定义的 422、503、500 响应及其 `detail` 字符串；不涵盖 FastAPI 路由层或 CORS 中间件产生的其他状态码和响应格式。

## （一）HTTP 层错误

| HTTP 状态 | 响应体 | 场景 |
| --- | --- | --- |
| 422 | `{"detail":"请求参数无效"}` | JSON 请求缺字段、字段类型错误或包含额外字段。具体 Pydantic 诊断不会向前端展开。 |
| 503 | `{"detail":"服务尚未就绪"}` | Web API 尚未就绪。 |
| 500 | `{"detail":"服务内部错误"}` | 调用路径发生未预期异常。响应不包含异常堆栈或私有配置。 |

HTTP 错误示例：

```json
{
  "detail": "请求参数无效"
}
```

## （二）Agent 业务失败

当 `submit()` 正常返回 `failed` 或 `limit_reached` 的 RunResult 时，Web API 仍返回 HTTP 200，并完整保留 `error`、`warnings`、证据状态和其他字段。前端应先检查 HTTP 状态，再检查 JSON 中的 RunResult `status`。

# 五、跨域、请求时长与会话

## （一）CORS

默认允许 `http://localhost:5173` 和 `http://127.0.0.1:5173`。通过 `GUANCE_CORS_ORIGINS` 设置逗号分隔的 Origin 列表；不允许 `*`。服务允许 GET、POST，允许 `Content-Type` 请求头，不启用跨域凭据。

## （二）长时间请求

`POST /api/runs` 会保持 HTTP 请求，直到异步 `submit()` 完成。模型运行可能较慢，Agent 总执行上限约为 180 秒。前端应保持加载状态，并确保浏览器请求及任何代理层超时不短于所需任务时长；不要因请求时间长而展示虚假的成功结果。MVP 不提供 SSE、WebSocket 或后台任务轮询接口。

## （三）会话边界

省略 `session_id` 时由 Agent 创建会话；提供已有 ID 时请求可续接该会话。`session_id` 不是身份认证凭证，也不提供用户隔离或权限控制。未加入认证前，服务仅适用于受信任的本地开发和演示环境，不要直接暴露到公网。
