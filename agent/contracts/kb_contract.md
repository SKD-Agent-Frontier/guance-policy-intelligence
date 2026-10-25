# 检索接口契约

状态：已冻结。对齐 RAG 侧 `proposal-v0.3`（原生文本）；媒体走 `proposal-v0.4`，本契约不含媒体。

后端实现 `guance_agent.kb.KnowledgeBase`（同步 `typing.Protocol`），不依赖 Agent SDK。
**接入真实后端不是"加一个分支"就够**：类型、配置、工具参数透传、证据登记、生命周期
都要联动改。`kb_backend` 现在支持 `mock` / `sample` / `rag`，真实后端由 `kb_rag.RagKB` 适配。

## 调用

```python
search(query: str, area: str | None, fyp: str | None, top_k: int) -> list[Hit]
read(planid: str, section_idx: int | None, max_chars: int, *,
     chunk_id: str | None = None, offset: int = 0,
     document_id: str | None = None, generation_id: str | None = None) -> Passage
```

可选能力（真实后端提供，mock/sample 没有）：

```python
get_chunk_source(chunk_id: str, *, document_id=None, generation_id=None) -> Passage
verify_quote(chunk_id: str, quote: str, observed_ranges: list[tuple[int, int]], *,
             document_id=None, generation_id=None) -> bool
```

## 坐标

- 原点：跳过 YAML 与正文前导空白后的正文首字符记为 0；之后空格、换行、Markdown 全保留
- 单位：Unicode 码点，0 起点，左闭右开
- 恒等式：`len(content) == char_end - char_start`
- 包含关系：`chunk_char_start <= char_start < char_end <= chunk_char_end`
- `offset` / `next_offset` 是**对象内**偏移，不是正文坐标；续读时原样回传

## 读取三态

| 输入 | `read_scope` | 说明 |
|---|---|---|
| 都不给 | `index` | 章节目录，content 为空 |
| `section_idx` | `section` | 该章节的一页，页内 `chunks` 带 `complete` |
| `chunk_id` | `chunk` | 该片段完整原文，可从块内 `offset` 起分页 |

`section_idx` 与 `chunk_id` 互斥。

## 证据诚信（Agent 侧三层确定性校验，均不依赖模型自证）

1. **坐标自洽**：跨度非空且不倒置；文本长度与跨度一致；块不越出登记章节
   （后端已标 `chunk_outside_section` 时接受但降级）
2. **引文逐字**：每条 `quotes` 换回后端原文，只在本次 `observed_ranges` 内比对
3. **正文数字**：`claim.text` 里的数字必须出现在**该条结论自己所引**的原文已见范围内；
   答案里没有对应结论的数字单独标记；取不到原文记为"核验未完成"，不当作通过

以上只做字面核对，不判断语义是否支撑结论。

## 错误

后端异常带 `.status`，直接映射为 `not_found` / `no_text` / `invalid_argument` / `timeout`，
其余归 `backend_error`。
