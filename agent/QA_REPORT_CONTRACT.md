# QA 研究报告契约

## 一、用途与边界

本契约描述 QA 研究报告的结构化输入、核验、撤回和最终输出。模型负责声明研究维度、组织段落并标明事实依赖；程序核验引文、事实中的数字与身份，并在事实核验后进行一次有时限的语义筛查。该审核计入同一轮任务预算，与现有 `RUN_TIMEOUT` 共用默认 180 秒总超时，不作为额外时限追加。

维度由模型声明，程序只能检查这些已声明维度是否有保留段落，不能保证识别用户问题中所有隐含意图。语义筛查是概率判断，不是事实真理证明。

## 二、输入结构

QA 输出可包含 `report`：

```json
{
  "report": {
    "dimensions": ["政策工具"],
    "blocks": [
      {
        "id": "fact-1",
        "title_path": ["政策安排"],
        "dimension": "政策工具",
        "kind": "fact",
        "text": "报告段落正文。",
        "supporting_claim_indexes": [0]
      }
    ]
  }
}
```

`dimensions` 需包含 1–8 个不重复且非空的维度；`blocks` 最多 12 项，全部段落的 Claim 依赖合计最多 96 项。每个段落的 `id` 唯一，`title_path` 含 1–2 级标题（每级 1–80 字符），`dimension` 必须属于已声明维度，`kind` 只能是 `fact` 或 `analysis`，`text` 为 1–1200 字符。`supporting_claim_indexes` 使用原始 `claims` 的零基整数索引，不得重复；事实块至少依赖一条 Claim，分析块至少依赖两条不同 Claim。

多对象 QA 按对象隔离维度和块 ID：维度及标题带对象标签，块 ID 带对象标识；再把各对象的报告合并为单份结果。合并结果仍受 8 个维度、12 个段落和 96 个依赖项上限约束，超过 8 个维度或 12 个段落时撤回全部合并报告块，并标记为 `partial`。没有结构化报告的对象不会把自由文本分析混入其他对象的审核报告。没有取得证据的对象以 `planid <对象标识>（未取得证据）` 加入缺失维度，防止误报为完整覆盖。

## 三、证据核验与段落保留

1. 每个块的依赖索引先绑定到原始 Claim 的 `(text, chunk_ids)` 签名。后续 Claim 因引文或证据核验而被删除时，系统按签名重新关联，避免索引移动后错绑到其他事实。
2. 块只有在所有依赖 Claim 仍存在、其 chunk 均有本轮登记的证据来源且满足块类型的依赖数量后才进入下一步核验。引用存在本身不代表其语义支持结论。
3. 引文先经过既有逐字核验；事实 Claim 还须通过引文语义筛查。报告块的数字和身份受程序检查，随后按列出的事实依赖进行块级语义筛查。
4. 对剩余内容至多发起一次模型审核，超时上限为 30 秒且受本轮剩余预算约束。审核模型只返回逐项支持/不支持判定，不改写正文。调用失败、超时、预算不足或返回结构无效时，未获明确支持的内容全部撤回。
5. 任一事实被语义审核判为不受支持时，该 Claim 从最终 `claims` 中撤回，其依赖段落随之撤回；剩余 Claim 按最终数组重新编号，报告块引用新的索引。其他保留段落的 `text` 原文不重写。
6. 审核因超时、预算或服务问题跳过时，未审核的报告块不予展示；结构化结果将标记 `verification.facts` 和 `verification.blocks` 为 `not_reviewed`，并在警告中说明审核未完成。没有 `report` 的旧式 QA 输出仍使用既有答案路径与安全回退。

本轮没有进行真实 Qwen/密云模型或真实知识库复测；离线 mock 核验不等同于真实环境验收，也不得据此声称所有问题都已完整覆盖。

## 四、最终结构

有 `report` 的 QA 最终结果写入 `RunResult.structured`，其版本为 `qa-report/1`：

```json
{
  "schema_version": "qa-report/1",
  "dimensions": ["政策工具"],
  "blocks": [
    {
      "id": "fact-1",
      "title_path": ["政策安排"],
      "dimension": "政策工具",
      "kind": "fact",
      "text": "报告段落正文。",
      "supporting_claim_indexes": [0],
      "supporting_chunk_ids": ["本轮证据块编号"],
      "verification": "model_reviewed_evidence_linked"
    }
  ],
  "coverage": {
    "covered": ["政策工具"],
    "missing": [],
    "status": "complete"
  },
  "verification": {
    "facts": "semantic_reviewed_against_quotes",
    "blocks": "model_reviewed_evidence_linked_semantics_not_proven"
  }
}
```

`supporting_claim_indexes` 指向最终保留的 `RunResult.claims`；`supporting_chunk_ids` 列出这些 Claim 使用的本轮证据块编号。`coverage.covered` 是至少有一个保留段落的维度，`missing` 是没有保留段落的已声明维度，也包含未取得证据的对象标记，例如 `planid <对象标识>（未取得证据）`。状态只有在至少覆盖一个维度、无缺失维度、无段落撤回且模型未声明证据不足时才为 `complete`，否则为 `partial`。有缺失、撤回、模型声明证据不足或没有保留块时，结果证据状态为 `insufficient`。

## 五、结果解释

`structured` 只包含通过核验与审核的报告块。逐字引文核验说明引句在本轮已读原文中存在；语义筛查只能降低明显不受支持内容被展示的风险，不能替代人工判断或保证完整性。`meta.qa_report` 记录接收、保留、撤回、覆盖与审核状态，供调用方诊断；它不是报告正文。

## 六、实现兼容边界

- `agent/src/guance_agent/qa_analysis.py` 中 `QAAnalysisDraft` 的公共字段由最新 `main` 定义；适配报告核验时须保留这些字段及其含义。
- 新增报告核验逻辑放在 `agent/src/guance_agent/qa_report_verification.py`，对应测试放在 `agent/tests/test_qa_report_verification.py`。
- 既有 `agent/src/guance_agent/qa_report.py` 与 `agent/tests/test_qa_report.py` 属于旧实现及其兼容守卫，应保留，不应把新核验模块覆盖到旧路径。
- 不包含 `report` 的旧式 QA 仍走现有答案路径与安全回退；报告核验改动不得改变该兼容行为。

## 七、验证结果

本次最终代码基于 `cd4ee0a` 合入分支后验证：

- Agent 全量测试：825 项通过；Ruff 检查通过。
- Backend 全量测试：250 项通过，6 项真实知识库开关测试跳过；按仓库 Backend CI 配置执行的 Ruff 检查通过。
- 未进行真实模型或知识库请求。真实 QA 诊断因缺少 RAG 运行依赖 `filelock` 在调用前终止；未安装依赖。
