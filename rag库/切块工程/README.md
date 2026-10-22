# RAG 2.0 文本切块工程

按照 `../设计方案2.0/切块方案.md` 实现 Markdown 结构识别、原文定位、父子块、必要关联、图片登记、断点恢复及独立校验。代码中的规则说明为中文；不执行 OCR、向量计算或检索问答，不调用生成模型，不上传语料。

首次阅读建议先看[代码阅读说明](代码阅读说明.md)：包含调用顺序、关键变量、对象/字段关系、表格与引用算法、token 计数、恢复和校验原理。各源码同步补充了对应位置的详细中文注释。

## 环境与复现

Python 3.12。实际安装的直接与间接依赖固定在 `requirements.lock.txt`；`requirements.txt` 表示兼容范围。Qwen tokenizer 修订及每个配置文件的 SHA-256 在 `model_profile.json`，不包含模型权重。恢复同一批次时配置、模型修订、代码与输入必须一致。

在本目录运行 PowerShell：

```powershell
# 本机已验证的 Python 3.12；其他机器可换成等效的 Python 3.12。
& 'C:\Users\39962\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe prepare_model.py
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.cache/pytest-local
.\.venv\Scripts\python.exe -X utf8 chunk.py build --sample
.\.venv\Scripts\python.exe -X utf8 chunk.py build --all --resume
```

`prepare_model.py` 仅连接 Hugging Face 官方模型仓库下载分词及 processor 配置；已存在 profile 时严格使用记录的修订。处理、恢复、测试及校验均离线运行。

```powershell
# 将下面路径替换为实际批次，独立重读 JSONL、快照和来源进行校验。
.\.venv\Scripts\python.exe -X utf8 chunk.py validate --run 'outputs/<批次>'

# 可指定新批次目录，输入变化或规则变化时另建批次，旧快照不覆盖。
.\.venv\Scripts\python.exe -X utf8 chunk.py build --all --resume --run 'outputs/<新批次>'
```

默认输入是仓库内 `语料库/Agent标准引用库` 的 `corpus` 与 `edge_cases`。`--source` 可指定相同目录结构的输入库，用于离线迁移及回归测试。输入根下其他目录和训练集不被扫描。输出不能位于输入库内。

若隔离环境的系统临时目录不可写，可将当前 PowerShell 进程的 TEMP/TMP 指向本目录 `.cache/tmp`；这不需要修改系统级环境变量。

## 规则与代码入口

| 模块 | 职责 |
|---|---|
| `source.py` | 严格解码、字节快照、Unicode 码点范围、原子写入、输入哈希清单 |
| `structure.py` | Markdown 线索、中文编号、目录和标题判断、基础单元、章节树 |
| `chunking.py` | 父子块、保守乱码标记、安全句群、HTML rowspan/colspan 与 Markdown 行组 |
| `relations.py` | 共同条件、定义、例外、明确条号/前条/前款、必要上下文与重新计数 |
| `tokens.py` | 官方 tokenizer + 官方聊天模板的纯文本输入，无截断计数 |
| `media.py` | 图片资源去重登记、每次出现位置、缺失引用、可读性和尺寸 |
| `pipeline.py` | 单 worker、完整检查点、缓存校验、批次汇总、中文报告 |
| `validation.py` | 独立逐字复核、覆盖与包含、依赖、输入 token、资源及来源哈希对账 |

位置都是完整源文件的 0 起点、左闭右开 Unicode 码点，包括 front-matter。原文通过 `read_bytes().decode('utf-8')` 获取，不转换 CRLF。Markdown 解析结果只提供线索，原文文本始终直接按范围取回。行号仅辅助展示，采用 CR/LF 换行定义；不能把 UTF-16 下标直接当本工程位置。

子块目标 768 token，普通父块参考 3072 token，完整检索输入上限 8192 token。完整原子单元可以超过目标；只能沿安全句群或表格逻辑行组处理超限。纯代码块不按政策句子拆分。条件、单位、表头和附注加入输入后重新计数；无法安全处理的对象保留为 pending。

HTML 行组只在网格能验证且所有 rowspan 已结束的位置划分。缺少可靠表头、网格不规则或无法安全分组的超长表格待处理。父块始终保留整个原表。未唯一解析的引用、外部文件引用和循环依赖不会被猜测补全。

## 输出与下游接口

每个 `outputs/<批次>/` 内包含：

- `documents.jsonl`：来源版本、planid、原始元数据、源路径、快照、哈希、编码和状态。
- `units.jsonl`：排他基础单元。顺序拼接 `text` 必须等于完整源文件；`disposition` 为 represented、pending 或 excluded，后两者附原因。
- `structures.jsonl`：可嵌套章节与条文，包含文档根、原文范围、标题路径及编号作用域。无可靠标题时根标题为空。
- `parents.jsonl`：父块、完整范围、单元成员、章节归属。主要用于回取，不直接承诺可独立入索引。
- `chunks.jsonl`：全部子块，包括 ready 和 pending。`source_segments` 保存证据原文；`context_segments` 保存必要原文上下文；`retrieval_text` 仅用于检索。
- `relations.jsonl`：触发原文、来源父块、目标类型/ID、规则及 resolved/unresolved 状态。明确关联可展开其必要依赖，循环保持待处理，不推理新关系。
- `media_assets.jsonl`：字节相同图片共用 asset 身份，并保留全部物理路径与文档关联。
- `media_occurrences.jsonl`：图片每次引用的独立身份和原文位置，包含缺失状态；未引用图片只登记资源，不猜测出现位置。
- `issues.jsonl`：异常及位置，区分源数据问题、待处理内容和处理错误。
- `usable_chunks.jsonl`：**只有独立校验通过后才生成的 ready 子块成员清单**，包含子块/文档/父块 ID、token 数与实际模型输入哈希。
- `snapshots/`：原始 Markdown 字节快照；本轮媒体仅登记路径、哈希与尺寸，不生成冻结图片副本或识别结果。
- 配置、输入清单、检查点、环境、统计、`validation.json`、`校验报告.md` 和 `切块样例.md`。

**下游应通过 usable 清单选择 chunks，不能将 chunks 全表直接入索引。** pending 块可能超限或缺少已知必要上下文；父块与 chunks 的原文范围不等于可无条件引用的完整证据。检索增强字段与原文引句必须分开使用。

文档版本由 `planid + source_hash` 决定；结构、父子块 ID 同时包含原文位置和配置/规则指纹。代码变化也进入规则指纹。完全相同的正文来自不同 planid 时仍是独立来源，不合并政策身份。正文内容指纹只提供给后续去重，不删除内容。

文档状态包括 ready、partial、no_body、media_only、source_suspect、title_only、error。图片缺失本身不阻断不依赖图片的原生正文；所有图片均标记未识别，不能据此推断图中事实。

## 恢复、验证与已知边界

检查点采用完整 JSON 原子写入及 SHA-256；缓存损坏时重算。新批次可复用配置、来源、媒体清单均未变化的缓存。增量更新另建完整批次，旧引用快照保留。恢复模式不会在旧批次中混入变化后的来源或规则。

代码指纹按源码字节计算，包含注释；因此补注释也会使后续构建生成新批次标识。已有批次及原验收记录保持不变，其指纹对应当时执行版本，详情见[本次说明](代码阅读说明.md#9-本次只增加解释已有结果如何处理)。

独立校验会重新装配输入、重新分词、核对逐字范围、结构归属、必要上下文、源文件和媒体哈希，并检查文档成员完整性。校验失败时可用清单清空，CLI 返回非零。单文档处理错误保留 error 记录，不伪装成功；源数据待处理内容与程序错误分开报告。

规则是确定性的保守启发式，不能保证识别全部目录、损坏文本、隐含条件或跨页表格关系。极度断行文档可跳过 Markdown 解析线索，转为原文边界规则；该降级在 issue 中保留。程序校验通过不是政策语义准确率，也不是问答效果指标。

原始数据、环境、模型配置缓存及大体积生成数据不提交 Git；代码、固定配置、依赖锁、模型 profile、运行说明和实际验收摘要本地提交。未进行远端推送。
