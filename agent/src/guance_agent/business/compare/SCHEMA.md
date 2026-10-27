# 比较业务输出契约

入口为 `submit(..., task_type="compare" | "compare_plans", targets=[...], dimensions=[...])`。
两类任务均需 2–8 个不同的明确政策对象。显式 targets 优先；唯一标题可定位，歧义必须澄清。
不修改共享 BusinessOutcome、Claim、EvidenceRef。下列字段属于最终 RunResult.structured。

## 公共字段

| 字段 | 结构与含义 |
| --- | --- |
| objects | 按 targets 顺序排列；每项含 planid、title、area、fyp、plantype_label。身份信息不充当正文事实；未知值为 null。 |
| dimensions | 去重后的字符串数组。显式参数优先，其次问题识别，最后采用功能默认维度。 |
| matrix | 按 dimensions 顺序排列，每行包含 dimension、cells、difference、comparability。 |
| comparability | comparable 布尔值与 reason 字符串；不代替逐维口径说明。 |
| coverage_notes | 本轮取证失败或覆盖限制说明，不能作为政策事实引用。 |

每个 `matrix.cells` 项含 `cell_id`、`planid`、`status`。cell_id 为 `d{维度零基下标}:{planid}`。
`found` 项另含 text、chunk_ids、quotes，三者与本轮对应 Claim 一致。quotes 由程序从已读取、
与该政策绑定的原文精确切片回填，模型只引用内部证据编号，不自行填写原文。

- found：已取得有原文依据的事实，仍需基础层统一核验。
- not_retrieved：本轮未取得相关正文，不能推断政策没有规定。
- insufficient：读取了部分原文，但没有形成该维度的充分证据；reason 说明原因。
- 本模块有限取证不声称检查全文，因此不输出 not_in_document。

`matrix.difference` 含 text、supporting_cells、kind="analysis"、status。
status 为 supported 或 insufficient。supported 必须关联本行全部对象的 found 单元格，
否则撤回为无法判断。difference 是分析判断，不是原文引句或语义核验通过的事实。

`matrix.comparability` 同样含 comparable、reason。文件性质、时期、单位、实施状态不同或
统计定义未核实时说明限制，不做未经证实的数值大小排序；不会静默换算口径。

## 多方案附加字段

`compare_plans` 增加 evaluation_criteria 和 scheme_analysis：

- evaluation_criteria：原样来自用户问题的明确评价标准；没有时为空，不自行设置权重。
- scheme_analysis：按对象顺序排列，每项含 planid、advantages、limitations、applicability。
- 三类分析均为判断数组，每项含 text、supporting_cells、kind="analysis"。
- 每项依赖的单元格均须 found，并至少包含当前方案自身的事实。缺乏依据时为空数组，正文说明无法判断。
- 不产生绝对最佳方案或排名；缺失条款不被解释为方案缺陷，也不把规划安排包装为申领承诺。

## 核验、消费与降级

业务模块只返回 BusinessOutcome，由基础层生成最终 RunResult。消费者必须同时展示 warnings、
evidence_status 和各单元格状态；不能只展示分析文字。基础层剔除 Claim 或重建回答时，
当前契约会清空整个 structured。消费者遇到空对象时展示底座给出的证据不足说明，不缓存旧矩阵。
没有任何有效 Claim 时，基础层至多保留真实检索发现的文献身份列表，不保留业务事实矩阵。

普通问答沿用 20 句引文上限；比较与要素提取共用 120 句上限，逐句核验规则不变。
引文与数字核验只确认来源及字面一致性，不代替语义支持判断。

`sample.json` 仅在真实学校模型与真实RAG验收成功后生成，不使用合成响应占位。
