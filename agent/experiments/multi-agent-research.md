# 管策智研｜多 Agent 研究实验

实验分支：experiment/multi-agent-research，基于 Hyperresearch 机制实验分支。没有修改 main。

## 已实现架构

Lead Agent 仅对从原始问题提取的要求 ID 进行有效分组。规划模型超时或输出错误时回退确定性分配，不能发明新的用户授权地区、时期或文献范围。

Research Workers 复用当前 OpenAI Agents SDK 原生 run_sdk_task，启用已有 Hyperresearch 式缺口审查和原文核验。每个 Worker 具有隔离会话和受限时间/工具预算，共用原始研究目标及用户明确指定的 planid 范围。当前最多两名 Worker，串行运行；防止校内 Qwen 的服务吞吐和 180 秒约束被并行抢占。

Evidence Ledger 仅合并经过现有逐字引句、数字和语义审核后的研究块。自动去重，检查每个报告块与已注册事实的关系；同一 chunk_id 的文档或生成版本冲突时拒绝混合。

Lead Synthesis 使用证据账本中的事实 ID 提出少量综合分析；Critic Agent 独立逐段评估这些分析是否得到对应事实和引句支持，系统额外确定性拒绝引用未知事实或凭空增加数字。没有通过审查的综合内容不进入最终报告。已通过旧版原文与语义审查的 Worker 段落继续保留。

Scheduler 控制总截止时间、任务交接、失败降级、结果交付。Workspace 持久化脱敏阶段和计数，不持久化原始问题、语料、答案或 API 密钥。无法声称已经实现进程级断点恢复。

## 本地启动

1. git fetch origin && git switch --track origin/experiment/multi-agent-research
2. 保留原有 agent/.env 中的 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL 和恢复后的 RAG_STORE_PROJECT；把 QA_ENGINE 改为 multi。
3. 继续沿用原有 Web 前后端启动方式。完整枚举请求在该实验模式下允许受限研究，不代表可以保证全库完整性。
4. 多 Agent 仅应用于 qa；复杂上下文指代问题由原有单 Agent 引擎接管，以免破坏已有会话引用处理。

## 三版本真实对照

在 agent 目录下执行：python scripts/compare_hyper_live.py --allow-real

脚本依次尝试 sdk、hyper、multi，并把详细回答、原文来源和调用统计存入 gitignored 的 agent/data/；该真实测试调用校园模型，有成本和耗时。保持题目、模型、库版本和总体时间预算相同，重点比较需求覆盖、证据支持性、错误率、耗时和调用数。

自动化测试仅证明可运行与程序化信任边界，不代表真实 Qwen 研究效果已经提高。当前默认时间限制下，多 Worker 可能被预算截断；只能将实际覆盖结果标为 partial，不得无依据声称全部政策。审查者仍可能与 Lead 共享同源模型偏差。

参考：Hyperresearch（研究要求与补证机制）、AutoSwarm（委派与执行状态）、PaperOrchestra（分阶段研究与引用登记），均未全量移植。
