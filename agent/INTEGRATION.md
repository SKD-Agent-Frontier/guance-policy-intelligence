# 一、统一入口集成指南

## （一）调用准备

从 `guance_agent.main` 导入 `submit`，用 `load_settings()` 读取配置。普通导入业务包或单个业务模块不会注册模块；生产入口导入时注册 `organize`、`compare`、`compare_plans`。QA 与要素提取沿用既有路径。

```python
from guance_agent.config import load_settings
from guance_agent.main import submit

settings = load_settings()
```

真实 RAG 使用者需在应用启动时预热共享检索后端，并在应用关闭时释放它和共享模型客户端：

```python
import asyncio

from guance_agent.client import aclose_client
from guance_agent.kb import close_kb, warm_up

await asyncio.to_thread(warm_up, settings)  # 启动时调用一次
# 应用退出时：
close_kb()
await aclose_client()
```

`warm_up()` 对非 RAG 后端不做操作。配置与检索生命周期说明见 [agent/README.md](README.md)。

调用可省略 `session_id` 以创建会话；传入它可在同一会话续接。设置 `user_id` 后，该会话会绑定该身份，后续调用须使用同一身份。输入长度、目标数量及格式由 `submit()` 校验。

## （二）五种调用示例

以下调用均需传入已加载的 `settings`。`extract` 可用 `targets` 指定政策；`organize` 可不传对象而开放检索；`compare` 和 `compare_plans` 需确定 2–8 个不同对象，可传 `targets` 或在问题中提供可唯一定位的标题。

```python
# 政策问答；task_type 默认为 qa
qa = await submit("甲市农业重点有哪些？", settings=settings)

# 要素提取；targets 指定政策，dimensions 指定要提取的维度
extraction = await submit(
    "提取支持对象和资金规模",
    task_type="extract", targets=["37982"],
    dimensions=["支持对象", "资金规模"], settings=settings,
)

# 政策整理；不传 targets 即开放主题
organization = await submit(
    "梳理十四五时期的科技创新政策",
    task_type="organize", settings=settings,
)

# 政策比较；也可在问题中给出可唯一定位的两个标题
comparison = await submit(
    "比较两项人才政策的支持措施",
    task_type="compare", targets=["37982", "38366"], settings=settings,
)

# 多方案分析；dimensions 可选
plans = await submit(
    "分析两项人才方案的优势、限制和适用条件",
    task_type="compare_plans", targets=["37982", "38366"],
    dimensions=["支持措施", "适用对象"], settings=settings,
)
```

`extract` 未指定 `dimensions` 时使用默认提取维度；`organize` 显式传 `targets` 时接受 1–8 个不重复 planid。比较任务要求 2–8 个对象。唯一标题可由路由定位，存在歧义时需补充对象信息。

## （三）RunResult 与证据关联

`submit()` 返回 `RunResult`。应用至少应保留并按需展示 `run_id`、`session_id`、`task_type`、`status`、`evidence_status`、`answer`、`claims`、`evidence_refs`、`warnings`、`error`、`route` 和 `structured`。`status` 的值为 `completed`、`failed`、`limit_reached`；`evidence_status` 的值为 `verified`、`insufficient`、`none`。不能只显示 `answer`，还要呈现与本轮结果相关的警告和证据状态。

`claims` 保存本轮结论及其 `chunk_ids`；`evidence_refs` 按 `chunk_id` 提供对应来源及本轮已见范围。业务结构应能回连到这些结论与来源：organize 的 `structured.policies[].claim_indexes` 是指向 `claims` 的零基索引；compare 的矩阵单元 `chunk_ids`、`quotes` 与对应 Claim 一致，再通过相同 `chunk_id` 关联 `evidence_refs`。逐字引文核验不代表语义支持判断。

`qa` 的 `structured` 为 `null`。`extract` 的结构化结果由 `dimensions`、`documents`、`elements`、`missing` 组成；结构由 [orchestrator.py](src/guance_agent/orchestrator.py) 装配，QA/Extract 共用结果契约见 [schemas.py](src/guance_agent/schemas.py)。QA/Extract 当前没有独立业务 Schema 或真实样例文件。organize 和 compare 的契约与真实样例见 [organize SCHEMA](src/guance_agent/business/organize/SCHEMA.md)、[organize sample](src/guance_agent/business/organize/sample.json)、[compare SCHEMA](src/guance_agent/business/compare/SCHEMA.md)、[compare sample](src/guance_agent/business/compare/sample.json)。

`structured` 为空或 `null` 时不得沿用上次调用的表格或矩阵；展示本轮 `answer`、`warnings` 和证据状态。无有效 Claim 时，基础层可能只保留本轮实际发现的文献身份；若统一核验剔除 Claim 或重建答案，会清空业务结构化结果。QA/Extract 的现有路径和运行边界见 [agent/README.md](README.md)。
