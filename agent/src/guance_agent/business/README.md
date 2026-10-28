# 业务模块接入契约

## 一、边界与当前状态

业务模块通过 Agent 统一入口使用现有工具、模型客户端、运行预算和证据核验。模块只返回业务结果，不构造最终 `RunResult`，也不自行初始化 RAG Store 或模型客户端。

普通 `import guance_agent.business` 只加载契约，注册表保持为空。已验收的政策比较 `compare` 与多方案分析 `compare_plans` 由统一入口 `guance_agent.main` 显式注册；实现及输出契约见 [compare/README.md](compare/README.md)。`qa`、`extract` 保留现有路径；未注册的任务类型会被拒绝。不得注册测试或占位模块。

## 二、模块签名与结果

业务代码从 `guance_agent.business` 导入公开契约，例如 `BusinessOutcome`、`BusinessError`、`BusinessModule` 和 `BusinessServices`。实现 `BusinessModule` 协议：

```python
class BusinessModule(Protocol):
    name: str

    async def __call__(
        self, task: TaskRequest, ctx: RunContext, services: BusinessServices
    ) -> BusinessOutcome: ...
```

`BusinessOutcome` 字段为 `answer: str`（必填）、`claims: list[Claim]`（默认空列表）、`structured: dict`（默认空对象）、`insufficient: bool`（默认 `False`）。`Claim` 沿用共享模型：`text`、`chunk_ids`、`quotes`、`dimension`、`anchors`。业务结构放在 `structured`，不得复制定义公共 Claim 或来源证据模型。

## 三、底座服务

编排层创建 `BusinessServices(ctx, rt, deadline)` 并注入模块；模块直接使用传入的 `services` 参数，不自行构造 Runtime 或服务对象。`deadline` 是单调时钟下的绝对截止时间。可调用：

```python
await services.search(query, area=None, fyp=None, top_k=None)  # -> ToolResult
await services.read(planid, section_idx=None, chunk_id=None, offset=0)  # -> ToolResult
await services.call_model(messages, max_tokens=None)  # -> str
```

`search` 和 `read` 返回现有 `ToolResult`，其 `status` 为 `ok`、`empty`、`no_text`、`not_found`、`invalid_argument`、`timeout` 或 `backend_error`；成功数据是 `list[Hit]` 或 `Passage`。这两项复用工具参数校验、异常转换、证据登记、调用日志和工具预算。检索使用当前 `ctx.scope`；原文读取不能越过该范围。章节目录读取可不带 `section_idx` 与 `chunk_id`；二者不能同时传入。分页续读应原样使用返回的 `next_offset`。

`call_model` 复用已配置客户端、thinking 设置和非空的 temperature 配置，返回文本；输出 token 必须为正整数，超出配置上限时裁到上限。缺少文本返回时抛 `BusinessError("OUTPUT_PARSE")`。`services.telemetry` 只记录逻辑模型调用数、输入/输出 token、finish reason、耗时及状态，不包含提示词或答案；SDK 内部有限重试不另算逻辑调用。

每次服务调用都受整体 deadline 约束。工具调用受 `max_tool_calls` 限制；`call_model` 的逻辑调用受 `max_turns` 限制；单次模型调用连同 SDK 有限重试不超过 `min(llm_request_timeout, 剩余任务时间)`，生成上限不超过 `llm_max_output_tokens`。达到整体截止时间返回 `RUN_TIMEOUT`；单次请求提前超时返回 `MODEL_UNAVAILABLE`。限制错误码使用 `TOOL_BUDGET`、`MAX_TURNS`、`RUN_TIMEOUT`；其他业务错误只能使用 `output.ERRORS` 中已有代码，未知代码映射为 `INTERNAL`。检索后端可能通过同步线程执行；任务取消会取消等待，不保证能强制终止已经运行的后端线程。

## 四、目标对象与证据要求

入口 `submit(question, session_id=None, user_id=None, settings=None, task_type="qa", dimensions=None, targets=None)` 中，显式 `targets` 接受 1–8 个不重复有效 planid，并优先于问题中的自然语言线索。编排层把完整目标集合一次传给模块，并将 `ctx.scope` 固定到该集合；不得逐对象调用后再拼结果。`compare`、`compare_plans` 始终至少需要两个对象，即使模块允许开放主题也不例外。比较任务中的多个唯一标题可分别跨地区或规划期定位；存在歧义仍要求澄清，不取首个候选。

没有传 `targets` 时，模块默认要求目标对象；只有在能唯一定位对象时才调用模块，否则返回提示。模块可以显式声明 `requires_targets = False` 接受开放主题；未声明时默认为 `True`。开放主题下 `task.targets is None` 且 `ctx.scope is None`。开放主题或需要按标题定位时，编排路由中唯一的地区和规划期会自动用于 `services.search`；业务不得用互相冲突的 `area` 或 `fyp` 覆盖它们。此类路由含多个地区或规划期时，系统要求缩小范围，不会悄悄挑第一个。若自然语言 planid 已唯一确定对象，或显式 `targets` 已确定对象集合，可按这些对象跨地区或规划期比较。开放主题的 `services.read` 只允许读取本轮成功 `services.search` 已发现的 planid。所有检索与回读仍走现有预算和证据登记流程。未知且未注册的 `task_type` 返回 `INVALID_INPUT`。

### （一）文档分组与结果证据

`PolicyRef` 与 `group_by_document` 可从 `guance_agent.business` 导入。`PolicyRef` 字段为 `planid`、`title`、`area`、`fyp`、`admin_level`、`plantype_label`、`chunk_ids`；除 `planid` 外，其余元数据可为 `None`，`chunk_ids` 默认空列表。

`group_by_document(hits: list[Hit]) -> list[PolicyRef]` 按 `planid` 分组并保留文献首次出现顺序。元数据全部取该 planid 的首个 Hit，之后命中即使冲突也不覆盖、不拼接、不从后条补 `None`；`chunk_ids` 按首次出现顺序去重，同一文档不同章节的不同 chunk 都保留。函数不修改输入。组员 A、B 都应复用此函数；`plantype_label` 可用于辨别政策类型、明确比较口径，但只是命中文档的元数据，不能当作原文事实证据。写入业务 `structured` 前将模型转成 JSON 值：

```python
documents = [doc.model_dump(mode="json") for doc in group_by_document(hits)]
```

`PolicyRef` 不是 `EvidenceRef` 的替代品，也不会登记证据。`Claim` 必须关联本轮 `services.search/read` 实际登记的 `chunk_ids`。业务自行放入 `structured.documents` 的对象元数据不受信任。若本轮没有有效 Claim（包括没有 Claim），底座会重建结果，不保留原始业务 `answer` 或未核验的结构化事实；若本轮成功 `services.search` 实际发现文献，`structured` 只允许包含底座根据这些 Hits 生成的身份列表及如下非事实状态（示意中的空数组只表示字段形状），没有成功检索到文献时为 `{}`。这是无有效 Claim 时保留身份信息的安全例外，不代表保留业务答案或事实。显式 `targets` 本身不是检索证据，也不会被自动挂作已核验文档：

```json
{
  "documents": [],
  "verification": {
    "policy_facts": "unverified",
    "reason": "no_valid_claims"
  }
}
```

若统一核验删除已有 Claim、剔除引用编号，或因校验重建答案，集成层会清空业务 `structured` 并添加警告。成功的 anchors 引句回填和 quote 空白回贴不视为删除结论，不会单独触发清空。业务不得在结构化结果中继续呈现已失效结论；证据不足应明确设置 `insufficient=True`，不得把未检索到写成政策没有规定。

提供 `quotes` 时，引句须来自已读取范围；没有引句时，可提供 `anchors` 作为定位线索，由既有流程尝试回填原文句。通用核验只做来源、原文引句和数字的确定性字面检查，不判断语义是否支持结论，也不代表覆盖全文。后端没有引句核验能力时，系统不会把跳过核验伪称为已核验。

业务 `structured` 必须是 JSON 对象，底座在组装结果前检查并规范化：允许 `null`、字符串、布尔值、整数、有限浮点数、字符串键字典和列表；tuple 会规范化为 list。日期/日期时间、set、非字符串字典键、非有限数及标准 JSON 编码器不支持的自定义对象都不是 JSON 值，返回时会以 `OUTPUT_PARSE` 拒绝。`BusinessOutcome` 的签名不因此改变。

## 五、模块注册与文件归属

模块仅在统一入口加载时显式注册。普通 `import guance_agent.business` 只加载契约，注册表保持为空；导入统一入口 `guance_agent.main` 时，集成方显式导入已验收模块并调用 `register(instance)`。当前统一入口注册 `compare`、`compare_plans`；业务作者不构造 Runtime 或 BusinessServices。`get_module(name)` 对未注册名称返回 `None`。名称须匹配小写字母开头、后接小写字母/数字/下划线/连字符，长度不超过 64；`qa`、`extract` 和重复名称被拒绝。

组员 A 在 `business/organize/` 实现自己的模块，组员 B 在 `business/compare/` 实现自己的模块。各目录应包含 `SCHEMA.md` 和 `sample.json`，记录 structured 字段与案例。未经集成方协调，不修改 `contract.py`、`services.py`、`__init__.py`、统一 Schema、编排和核验文件；共享接口需求通过变更请求提出。

## 六、验收与完成标准

模块 PR 应包含自身模块测试、`SCHEMA.md`、`sample.json`，并通过针对性测试和代码检查。集成验收还需覆盖：注册与路由、原有 QA/Extract 回归、targets 全量一次传入、工具证据登记与范围约束、预算和超时、Claim 引文与数字核验，以及核验失败时答案和结构化结果的一致性。使用 mock 的结果必须标明为 mock；它不等于真实模型与真实 RAG 的业务闭环。

模块代码和单元测试通过，只表示模块实现完成，不等于产品功能完成。产品完成还需集成路由、统一结果核验、前端/导出消费及受控真实业务验收。
