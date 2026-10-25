# Agent 与 RAG 四项对接交付

更新日期：2026-10-25。当前API：**proposal-v0.4**；原生文本响应使用proposal-v0.3，媒体响应使用proposal-v0.4。五份原生文本样例已由当前`RagKnowledgeBase.search/read`从真实运行库重新生成，并以`get_chunk_source`核验长块分页。适用SQLite＋Qdrant Local＋BM25S，现已接入原生文字、OCR和原图。新增必读：[多模态交接说明](多模态交接说明.md)。

本目录可用于Agent与RAG开发对齐。接口形态为同步search/read，由Agent异步调用。**RAG侧适配器、常驻共享和续读已在[独立检索工程](../../检索工程/README.md)实现，并通过[完整真实库验收](../../检索工程/验收报告.md)。Agent源码本轮未修改，仍须由同事接入。** 本目录是对接材料；工程运行还需代码、已有语料与索引，不能只发送本目录就运行完整RAG。

## 1. 四项交付物

| 交付物 | 文件 | 内容与用途 |
|---|---|---|
| ① 接口适配说明 | [接口适配说明.md](接口适配说明.md) | 同步Protocol、Agent工具参数、search映射、read选择/续读、字段来源、坐标规则、错误状态、启动与共享 |
| ② 一条真实Hit样例 | [samples/Hit.json](samples/Hit.json) | 完整元数据、原始chunk证据ID、冻结版本、完整块与300字预览范围，供校验字段与证据映射 |
| ③ 一条真实Passage样例 | [samples/Passage.json](samples/Passage.json) | 上述块完整451字原文、所属章节及范围、ChunkSpan、完整/截断状态，供原文回读；章节内容补充见[章节Passage](samples/章节Passage.json) |
| ④ 差异清单 | [差异清单.md](差异清单.md) | 对照现行kb_contract.md和实际代码，列双方负责的改动、文件位置及后续验收行为 |

建议按①→②→③→④查看：先看契约，再核对同一块的预览和完整原文，最后确定双方实现范围。五份文本JSON是当前RAG响应的直接序列化，可用于字段讨论与后续契约检查；导出器不会补写响应字段或替换真实来源ID。

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

Agent接入原生文本时须遵守：

- Agent不得自行读取原始Markdown计算坐标或重新清洗正文；原文统一通过`kb_read`（调用RAG同步`read`）取得，直接使用RAG返回的坐标。
- `content`、`body_origin`、`char_start/end`及来源/版本信息须成组取自同一次响应；不得拿其他响应的原点或范围解释当前`content`，也不得重复换算坐标。分页续读仍按`next_offset`规则处理。

下例仅用于RAG侧核验原文一致性，不是Agent自行打开Markdown的接入步骤。

```python
body = source_text[body_origin:]
assert content == body[char_start:char_end]
assert content == source_text[body_origin + char_start:body_origin + char_end]
```

[检索设计第六节](../检索设计方案.md#6-身份稳定性与统一原文坐标)保留完整坐标设计。旧坐标不能只换版本号后直接套用到新正文。

## 3. 两条主样例如何对应

planid=20699，《苏州市“十四五”民政事业发展规划》。由当前RAG实例以BM25执行查询“养老服务”（area=苏州、fyp=十四五、source_types=text_chunk）后选取；Hit由同一实例按其chunk_id回读为Passage。两者均为运行时实际API响应。

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
| [长块第1页](samples/长块第1页.json)、[第2页](samples/长块第2页.json) | 当前RAG read对planid=48787、chunk_id=chunk_594a17c56902e7fdac2a38662282ebda的实际响应，按6000＋134分页；next_offset为块内偏移。导出时将两页拼接并与同一实例的完整块读取逐字核对；Agent kb_read仍待接入 |
| [真实RAG结果.json](samples/真实RAG结果.json) | 现有query形态的嵌套结果，保留内部文件坐标，仅作原始数据映射参考 |
| [核查结果.json](核查结果.json) | 此前核查基线3a94e13、发布revision=1的原文/ID/SQL证据；坐标仍为文件原点，不是新接口运行验收 |
| [检索工程导出器](../../检索工程/scripts/export_agent_text_samples.py) | 通过当前`RagKnowledgeBase` API重建五份文本样例；全部查询、分页读取和完整块核对共用同一实例 |

从仓库根目录对已恢复的存储工程运行导出器（使用项目已安装依赖的Python环境）：

```powershell
$storeProject = Read-Host '已恢复的存储工程目录'
python .\rag库\检索工程\scripts\export_agent_text_samples.py --store-project $storeProject
```

导出器使用BM25文本搜索，不调用向量模型；jieba缓存位于系统临时目录并在进程结束时清理。固定历史查询和样例ID仍须在目标运行库中有效，否则脚本会失败，不会生成替代数据。

5份Hit/Passage样例已按各自版本冻结快照核对：完整原文范围、本次content、章节/页内块交集与分页拼接一致，文本和原始ID未改变。历史核查记录506篇文档、42516个原生块，其中41个块超过6000码点、最长91631；仅加chunk_id不足以替代offset续读。

## 5. 交付范围

已交付：原生文本V0.3契约与原样例，媒体V0.4接口、真实样例和双方修改清单。文本正文坐标不变，媒体使用独立OCR/图片坐标；当前search默认混合类型，旧Agent不能直接套用纯文本类型。

RAG侧已实现真实同步适配器、原文分页、启动加载一次和多请求共享，以及对调用方已见范围的原句校验。完整BM25和混合检索验收均已逐页重建41个长块137页，最长91631码点；真实向量调用3次。原始Markdown、数据库业务数据与ID保持不变，没有HTTP服务。

同事后续负责：Agent类型、配置、生命周期注入、工具关键字参数、引用已见范围登记合并、输出校验及端到端问答联调。五份文本样例保留proposal-v0.3响应契约；实际Passage页内ChunkSpan另包含可选section_id/section_path，以准确表达父章节读页中各子块自身章节。目录中的旧版`核查并导出样例.py`仅属历史材料，不应用于重建当前样例。

## 6. 本轮媒体扩展交付

| 文件 | 内容 |
|---|---|
| [多模态交接说明](多模态交接说明.md) | search/read/read_image接口、联合类型、图片消息、版本/坐标隔离、Agent修改位置与联调项 |
| [MediaHit.json](samples/MediaHit.json)、[MediaPassage.json](samples/MediaPassage.json) | 同一真实OCR来源的检索与回读，原图、区域、识别状态及独立坐标 |
| [ImageData摘要.json](samples/ImageData摘要.json) | 实际区域图片响应的哈希/尺寸摘要；明确省略data_url，完整read_image会返回图片数据 |
| [多模态验收报告](../../检索工程/多模态验收报告.md) | 4481个来源全量回读、490份原图核验、OCR/原图真实召回及原图/区域像素回取 |

Agent源码未改。RAG已提供媒体能力，Agent后续须扩展类型和工具，把图片作为模型真正的图片输入，并分开登记文字、OCR及图片实际已见状态。
