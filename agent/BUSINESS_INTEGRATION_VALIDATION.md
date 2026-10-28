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

另行运行的聚焦 QA 使用相同原配置和 Prompt 参数，结果为 `completed` / `verified`，1 条 Claim、1 个 EvidenceRef、1 条通过引文。该诊断与首轮五项分开记录，不能替代或改写首轮 QA 失败。首轮 QA 的运行元信息显示输出触及 2000 token 上限；这是诊断线索，尚不能据此确认失败根因。

**真实首轮未全通过。** 因而整体真实业务验收标准尚未达成。当前仅能确认四个首轮场景通过和聚焦 QA 单独通过；不能将五项首轮标记为全通过。广泛 QA 问题的输出容量是当前需明确的边界，根因仍待确认。

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

入口注册及离线验证已通过；真实首轮五项没有全部通过，尤其 QA 场景失败仍需保留并处理。最终总验收不得表述为首轮全通过，也不得将聚焦 QA 诊断合并进首轮计数。
