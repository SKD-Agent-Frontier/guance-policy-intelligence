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
| analysis_gaps | 未生成或被撤回的分析诊断。每项含 field、reason、message；异同另含 dimension，方案另含 planid。不包含被拒绝的分析文本。 |

每个 `matrix.cells` 项含 `cell_id`、`planid`、`status`。cell_id 为 `d{维度零基下标}:{planid}`。
`found` 项另含 text、chunk_ids、quotes，三者与本轮对应 Claim 一致。事实采用原文选句，
text 为所选原句以中文分号组合的文本，quotes 保存各原句；不采用模型自写正文，不改写主体或条件。
模型概括只允许放在 analysis 判断中，不伪装成原文事实。source_contexts 保存所引原文的章节路径，
便于核对本期目标与历史背景。模型引用内部证据编号，由程序核对对应政策和原文。
每格保留一个完整语义单元，避免先合并不同条款再重组条件；“培养期内”句保留取证时绑定的前句对象。

- found：已取得有原文依据的事实，仍需基础层统一核验。
- not_retrieved：本轮未取得相关正文，不能推断政策没有规定。
- insufficient：读取了部分原文，但没有形成该维度的充分证据；reason 说明原因。
- 本模块有限取证不声称检查全文，因此不输出 not_in_document。

`matrix.difference` 含 text、supporting_cells、kind="analysis"、status。
status 为 supported 或 insufficient。supported 必须关联本行全部对象的 found 单元格，
否则撤回为无法判断。difference 是分析判断，不是原文引句或语义核验通过的事实。
有效分析另含 scope="supporting_cells_only"，text 由程序显式加上“就本次所列条款而言”，
限制侧重、对象边界等概括的覆盖范围。对整份政策或全部对象的排他性推断被撤回；
范围前缀不豁免主体、条件、政策动作和引文依赖的检查。

`matrix.comparability` 同样含 comparable、reason。文件性质、时期、单位、实施状态不同或
统计定义未核实时说明限制，不做未经证实的数值大小排序；不会静默换算口径。

## 多方案附加字段

`compare_plans` 增加 evaluation_criteria 和 scheme_analysis：

- evaluation_criteria：原样来自用户问题的明确评价标准；没有时为空，不自行设置权重。
- scheme_analysis：按对象顺序排列，每项含 planid、advantages、limitations、applicability。
- 三类分析均为判断数组，每项含 text、supporting_cells、kind="analysis"。
- limitations 每项仅依赖本方案一个单元格，分别解释该条款的边界。
- applicability 仅选择本方案相关单元格，由程序逐条附上完整原文条件，标记 derivation="extractive_conditions"；不采用模型改写的个人资格，也不合并为共同条件。
- 优势概括遗漏或混淆服务主体时，若确有本方案的支持条款，可保守列出该原文支持特征，标记 derivation="extractive_support_features"。不推断实际效果、兑现或相对优越性；没有明确支持证据时不补填。
- 每项依赖的单元格均须 found，并至少包含当前方案自身的事实。缺乏依据时为空数组，正文说明无法判断。
- 不产生绝对最佳方案或排名；缺失条款不被解释为方案缺陷，也不把规划安排包装为申领承诺。

`analysis_gaps` 区分 `source_evidence_missing`（相关来源不足）、`analysis_not_generated`
（已有事实但模型未产出分析）和 `invalid_dependencies` 等约束撤回原因。其他原因见
result.py 的 GAP_MESSAGES；这些是诊断状态，不是政策事实或政策缺陷。若同类候选分析
有的保留、有的撤回，仍记录被撤回候选的原因；消费者以实际判断数组决定显示内容。
初稿诊断会交给原有复核调用纠错，最终结果重新校验；复核不能改变事实或补造来源。

## 核验、消费与降级

业务模块只返回 BusinessOutcome，由基础层生成最终 RunResult。消费者必须同时展示 warnings、
evidence_status 和各单元格状态；不能只展示分析文字。基础层剔除 Claim 或重建回答时，
当前契约会清空整个 structured。消费者遇到空对象时展示底座给出的证据不足说明，不缓存旧矩阵。
没有任何有效 Claim 时，基础层至多保留真实检索发现的文献身份列表，不保留业务事实矩阵。

普通问答沿用 20 句引文上限；比较与要素提取共用 120 句上限，逐句核验规则不变。
引文与数字核验只确认来源及字面一致性，不代替语义支持判断。

`sample.json` 仅在真实学校模型与真实RAG验收成功后生成，不使用合成响应占位。
