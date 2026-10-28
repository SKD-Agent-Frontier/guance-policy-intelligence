# 业务集成验证记录

## 一、集成改动

统一入口 `src/guance_agent/main.py` 新增 `OrganizeModule` 导入和一次注册，沿用 compare 的显式注册方式。业务模块实现、共享契约及 `business/__init__.py` 未修改。五种 `submit()` 调用方式见 [统一入口集成指南](INTEGRATION.md)。

## 二、离线验证

- 完整 Agent 测试：**640 passed，0 failed，34.73 秒**。
- RAG CI 测试：**10 passed，6 个子测试通过**。
- Ruff：**All checks passed**。

## 三、真实环境冒烟

运行在独立的 `origin/main` worktree，复用了用户原先指定的 `.env`、RAG 运行库绝对路径及虚拟环境。交付材料不记录这些本机路径或配置值。

首轮五项真实场景的汇总为 **4/5 通过**，首轮整体标记为未通过。QA 场景返回 `failed` / `OUTPUT_PARSE`，0 条 Claim、0 个 EvidenceRef；Extract、Organize、Compare、Compare Plans 均为 `completed` / `verified`。各场景计数与核验项见[脱敏冒烟摘要](BUSINESS_INTEGRATION_SMOKE.json)。

另行运行的聚焦 QA 使用相同原配置和 Prompt 参数，结果为 `completed` / `verified`，1 条 Claim、1 个 EvidenceRef、1 条通过引文。该诊断与首轮五项分开记录，不能替代或改写首轮 QA 失败。首轮 QA 的运行元信息显示输出触及 2000 token 上限；其后续调查与结论见第六节。

**首轮批次未全通过。** 首轮五项中 4/5 通过，QA 的 `OUTPUT_PARSE` 失败事实保留；后续针对性调查结论见第六节。首轮批次不得改记为全通过，也不得将聚焦 QA 或后续复测并入首轮计数。

## 四、既有真实验收材料及限制

### （一）organize

- `src/guance_agent/business/organize/sample.json`：既有单个主问题的真实 BM25 RAG 与学校模型样例，状态 `completed` / `verified`，含 10 条 Claim、7 个 EvidenceRef；覆盖统计为发现 8 篇、过滤后 8 篇、读取 6 篇、摘要 5 篇、读取 10 个 chunk。
- `src/guance_agent/business/organize/SCHEMA.md`：结构与覆盖边界说明。
- `scripts/organize_real_run.py`：定义三道顺序验收问题的运行脚本。

当前仓库只保留上述单个真实样例，没有 `agent/data/organize_runs/` 运行记录或独立 organize 验收报告；不能据此确认脚本定义的三题均实际完成。样例也不证明全库或全文覆盖。

### （二）compare 与 compare_plans

- `reports/compare/2026-10-28/acceptance-fix-v11/README.md`：记载六个真实场景来自同一最终候选版本的连续完整运行，27 次引文检查全部通过。
- 场景结果：`policy.json`、`schemes.json`、`cross_period.json`、`cross_topic.json`、`controlled_missing.json`、`schemes_missing.json`。
- 审计材料：`acceptance.json`、`evidence-audit.json`、`review.json`。
- `src/guance_agent/business/compare/sample.json`：包含政策比较与多方案分析两个样例结果。

该证据属于 compare v11 历史候选，不是当前集成分支上的本轮实测。报告说明有限回读不能证明全文覆盖；语义审阅由 AI 开发助手完成，不构成人工独立评审。

## 五、结论与后续

入口注册及离线验证已通过；真实首轮五项没有全部通过，QA 场景失败记录继续保留。后续调查未发现确定性的集成回归证据，现有证据不支持将该历史失败单独作为 PR 4 合并阻塞项。首轮与后续验证分开计数，不能表述为首轮全通过。

## 六、QA `OUTPUT_PARSE` 定向调查

对首轮 QA 原始记录的定向调查恢复到一条 3547 字符的 assistant 响应。`json.loads` 在位置 3521 报未闭合字符串；生产 parser 也拒绝了该响应，未能形成唯一完整的顶层 JSON 对象。首轮三次模型调用的输出 token 分别为 67、201、2000；原始记录没有 `finish_reason`。输出触及 2000 token 上限与末尾未闭合相符，但只能作为最可能的截断解释，不能据此声称原始调用的 `finish_reason=length`。

对原问题进行了两次复测，均使用原有运行环境和模型、RAG、Prompt 设置，总超时保持 180 秒，未修改默认参数：

- 标准 2000 token 上限：`stop`，输出 1605 tokens，`completed` / `insufficient`，2 条 Claim、2 个 EvidenceRef；引文核验 3 条 approved、6 条 rejected、0 条 unverified；耗时 45.506 秒。
- 仅将上限设为 4000：`stop`，输出 1370 tokens，`completed` / `insufficient`，4 条 Claim、4 个 EvidenceRef；引文核验 4 条 approved、3 条 rejected、0 条 unverified；耗时 44.180 秒。

两次复测均产生完整 JSON，且生产 parser 解析成功。标准上限下原问题可以完成，另有此前使用标准设置并达到 `verified` 的聚焦 QA，因此现有证据未显示确定性的集成回归，也未证明需要提高生产 token 上限。没有修改生产参数、源码或 QA / Extract 路径。

首轮 4/5 与 QA `OUTPUT_PARSE` 失败继续作为历史事实保留，不计作通过；基于后续调查，该历史失败不单独阻塞 PR 4 合并。复测结果为 `insufficient`，引文核验存在 rejected 项，这仍是已知质量限制；本记录不表示引文全部通过，也不证明任意 QA 输入均可靠。
