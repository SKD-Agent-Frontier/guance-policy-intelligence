# 检索接口契约 V0.1

状态：字段与定位参数已实现并通过离线测试；待 RAG 负责人确认，尚未冻结。
当前仓库未提供 `metadata/sample_plans.csv`，额外规划期别名及政府工作报告分类待确认。

后端实现 `guance_agent.kb.KnowledgeBase`（同步 `typing.Protocol`），不依赖 Agent SDK。
仅在 `get_kb()` 增加后端分支即可接入；工具负责参数验证、超时和统一结果包装。

## 调用

`search(query: str, area: str | None, fyp: str | None, top_k: int) -> list[Hit]`

- query 去掉首尾空白后为 1–200 字；area、fyp 最长 50 字；top_k 为 1–TOP_K_MAX。
- area 原样传入，包含匹配地区全称；不把地区简称补成猜测的行政区划。
- fyp 经 `normalize_fyp()` 换算后精确匹配；未指定 fyp 时允许缺失规划期的记录。
- 十一五、十二五、十三五、十四五、十五五分别映射为第十一个至第十五个五年规划。
  匹配别名前忽略空白与中英文引号；未知值原样保留。
- 结果按 score 降序、chunk_id 升序排序；无结果返回 `[]`。
- 模拟打分为 query 中不重复二字片段在完整 chunk 内容中的命中个数；单字查询合法但不命中。

`read(planid: str, section_idx: int | None, max_chars: int) -> Passage`

- planid 匹配 `^[A-Za-z0-9_-]{1,64}$`；section_idx 为非负整数。
- 不指定章节：返回 sections 目录（idx / path / chars），content 为空、偏移为 null、chunks 为空。
- 指定章节：返回章节开头至 max_chars 的原文，truncated 标记截断；chunks 只列完整包含的片段。
- 所有偏移均为完整 body 中的 Python Unicode 字符索引、左闭右开，不是字节偏移。
- Hit 截断到 SEARCH_SNIPPET_CHARS，char_end 对应实际返回内容；chunk_id 保持真源 ID。
- 回看：`passage.content[ref.char_start-passage.char_start:ref.char_end-passage.char_start]`。

## 字段与真源

完整机器字段定义见 `src/guance_agent/schemas.py` 的 Hit、Passage、ChunkSpan、ToolResult。
Hit 必含 planid、chunk_id、title、area、fyp、admin_level、plantype_label、section_idx、
section_path、content、char_start、char_end、score。元数据缺失的 nullable 字段必须返回 null。
Passage 包含相同篇目元数据、section_idx、section_path、content、偏移、truncated、chunks、sections。
工具和模型均不能生成 planid、chunk_id、章节、偏移或缺失元数据。

## 异常与状态

后端不存在篇目抛 KBNotFound(planid)，章节不存在抛 KBNotFound(planid, section_idx)；
篇目无正文抛 KBNoText(planid, title)；
其他异常由工具转为 backend_error。后端必须另设自身查询超时：to_thread 超时不会杀死后台线程。
工具统一返回 ToolResult JSON；目录与非 ok 结果不登记证据。

| status | 含义 | message |
| --- | --- | --- |
| ok | 检索或读取成功 | 空字符串 |
| empty | 未命中，不代表政策不存在 | 未检索到相关政策原文（检索词：{query}；地区：{area}；规划期：{fyp}） |
| no_text | 元数据存在、无正文 | 《{title}》（planid={planid}）没有可用正文，无法引用原文 |
| not_found | 篇目或章节不存在 | 未找到 planid={planid} 的篇目 / 该篇没有第 {section_idx} 节 |
| invalid_argument | 参数被拒绝 | 参数不合法：{字段}{原因} |
| timeout | 工具超时 | 检索超时，检索服务暂时不可用 |
| backend_error | 后端异常 | 检索服务异常，暂时不可用（详情只写日志） |

empty 的未指定筛选值展示为“未指定”。
mock_data 全部为合成数据，不代表任何真实政策；TEST-003 无正文、TEST-004 含伪指令、
TEST-005 无章节标题且正文超过读取上限。
