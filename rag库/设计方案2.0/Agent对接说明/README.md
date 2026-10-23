# Agent 与 RAG 四项对接交付

日期：2026-10-23。契约版本：**proposal-v0.3**。适用：当前本地SQLite＋Qdrant Local＋BM25S；首轮仅接原生text_chunk。

本目录可直接用于Agent与RAG开发对齐。接口形态为同步search/read，由Agent异步调用。**真实数据样例不等于已运行的生产接口；检索适配、常驻共享和续读工具仍待实现。**

## 1. 四项交付物

| 交付物 | 文件 | 内容与用途 |
|---|---|---|
| ① 接口适配说明 | [接口适配说明.md](接口适配说明.md) | 同步Protocol、Agent工具参数、search映射、read选择/续读、字段来源、坐标规则、错误状态、启动与共享 |
| ② 一条真实Hit样例 | [samples/Hit.json](samples/Hit.json) | 完整元数据、原始chunk证据ID、冻结版本、完整块与300字预览范围，供校验字段与证据映射 |
| ③ 一条真实Passage样例 | [samples/Passage.json](samples/Passage.json) | 上述块完整451字原文、所属章节及范围、ChunkSpan、完整/截断状态，供原文回读；章节内容补充见[章节Passage](samples/章节Passage.json) |
| ④ 差异清单 | [差异清单.md](差异清单.md) | 对照现行kb_contract.md和实际代码，列双方负责的改动、文件位置及后续验收行为 |

建议按①→②→③→④查看：先看契约，再核对同一块的预览和完整原文，最后确定双方实现范围。JSON可直接作为字段讨论与后续契约检查的输入；不把其中的真实来源信息替换为自编ID。

## 2. 样例版本和坐标

| 字段 | 统一约定 |
|---|---|
| contract_version | proposal-v0.3，表示当前对接提案，不表示生产API已发布 |
| offset_basis | body_first_non_whitespace |
| offset_unit | unicode_codepoint，0起点、左闭右开 |
| body_origin | 正文首字符在完整冻结Markdown中的绝对码点位置 |
| chunk_char_start/end | 完整块在正文中的固定范围 |
| char_start/end | 本次content的实际正文范围，可能只是预览或一页 |

跳过头部元数据及正文前面的空白，首字符记为0；从这里往后所有空格、换行和Markdown符号都保留并计数。body_start仍是元数据结束位置，不一定等于body_origin。数据库、切片、快照及原始ID保持不变；接口按正文原点统一换算。

```python
body = source_text[body_origin:]
assert content == body[char_start:char_end]
assert content == source_text[body_origin + char_start:body_origin + char_end]
```

[检索设计第六节](../检索设计方案.md#6-身份稳定性与统一原文坐标)保留完整坐标设计。旧坐标不能只换版本号后直接套用到新正文。

## 3. 两条主样例如何对应

planid=20699，《苏州市“十四五”民政事业发展规划》。来自此前真实BM25查询“养老服务”的首个命中，样例不是Mock；本轮未重跑检索或模型。

| 项目 | Hit | Passage |
|---|---|---|
| chunk_id | chunk_c5481643e7c67d653e29160c30719104 | 同一个ID |
| section_id / section_idx | section_aa3464a90b5898f38ba5c38fa030ce26 / 4 | 相同，目录idx尚未持久化发布 |
| body_start / body_origin | 253 / 254 | 253 / 254 |
| 完整块正文范围 | [10864,11315)，451码点 | 相同 |
| 本次content范围 | [10864,11164)，300码点 | [10864,11315)，完整451码点 |
| truncated / next_offset | true；Hit没有分页字段 | false / null |
| 章节 | 第四部分主要任务 | 返回所属章节完整范围[8286,19608) |
| ChunkSpan | 以chunk_id及完整范围定位 | chunks[0].complete=true |

按Hit的chunk_id回读应得到这一条Passage。若要读整章，目标输入为section_idx=4，示例[章节Passage.json](samples/章节Passage.json)返回正文[8286,14286)，6000码点、10个块交集，truncated=true、next_offset=6000；它与按块读取是两种请求。

## 4. 补充样例与证据

| 文件 | 用途与实际状态 |
|---|---|
| [长块第1页](samples/长块第1页.json)、[第2页](samples/长块第2页.json) | 同一真实6134码点块，planid=48787，按6000＋134分页；proposal-v0.3、页边界连续，next_offset为块内偏移；不是已实现的kb_read响应 |
| [真实RAG结果.json](samples/真实RAG结果.json) | 现有query形态的嵌套结果，保留内部文件坐标，仅作原始数据映射参考 |
| [核查结果.json](核查结果.json) | 此前核查基线3a94e13、发布revision=1的原文/ID/SQL证据；坐标仍为文件原点，不是新接口运行验收 |
| [核查并导出样例.py](核查并导出样例.py) | 保留此前的旧版导出器，会写回proposal-v0.2并覆盖当前样例；不应用来重建新版样例，待工程阶段升级 |

5份Hit/Passage样例已按各自版本冻结快照核对：完整原文范围、本次content、章节/页内块交集与分页拼接一致，文本和原始ID未改变。历史核查记录506篇文档、42516个原生块，其中41个块超过6000码点、最长91631；仅加chunk_id不足以替代offset续读。

## 5. 交付范围

已交付：接口契约说明、真实Hit、真实Passage/补充章节及分页样例、逐项差异与双方修改位置。文档和样例采用同一proposal-v0.3口径。

待后续工程：实现真实同步适配器、Agent类型/工具接入、原文分页、启动加载一次和多请求共享、引用范围合并及原句校验。原始Markdown和数据库不因本次对接材料交付而变更；没有新增HTTP服务或实施检索代码。
