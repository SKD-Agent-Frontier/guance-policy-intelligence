# 向量化与存储工程（开源组件版切块输入）

使用`../切块工程（开源组件版）/当前批次.json`指向的冻结结果，执行与原版相同的文本/图片向量化及存储规则，依据设计方案2.0。**这不是设计方案1.0与2.0的对照，而是两版切块输入的对照。**

用户已确认同时实施图片识别、语义切块和多模态向量化。详细实现、文件职责、限制和公共命令见[共享工程README](../向量化与存储工程/README.md)。

## 当前状态

2026-10-23已按完整覆盖规则补录并激活：**506篇文档，43,542个原始文本子块全部入库，48,516个真实向量点**。23个超长子块形成71段完整覆盖的输入，因此原生文本对应43,590条向量。逐点对账及5篇来源/父块回取通过，共享实现35项离线回归通过。历史基线已有备份恢复验证，本次补录未重复整库恢复演练，证据单独保留。详见[验收报告](验收报告.md)、[验收摘要](验收摘要.json)、[实施记录](实施记录.json)及[最新批次](outputs/README.md)。

已有3,562个图片识别子块（含56个不确定块）及919处现存原图出现均有向量。490张唯一图片中，479张有可解析转录，11张OCR未完成但原图已向量化；原有285处缺图引用没有实际文件，无法生成图像向量。原始pending及质量提示保留，不等于未向量化，详见[图片识别质量清单](待处理图片.md)。

本次成功运行复用46,438个缓存向量，新增计算84个唯一输入向量。模型计算是增量的；存储采用新集合全量写入和核对，再切换发布入口，并非直接向旧集合追加差额。这一方式保留旧批次，但补录耗时较长。中断、代理路由修复及失败批次记录见共享README和outputs/README.md。

## 当前目录与作用

```text
向量化与存储工程（开源组件版）/
├─ README.md              # 本版输入、隔离、状态与运行方式
├─ AGENTS.md              # 继承共享工程的维护要求
├─ config.json            # 开源组件切块输入、rag_oss schema、guance_oss collection前缀
├─ 当前批次.json          # 已激活generation、collection、覆盖状态与统计
├─ run.py                 # 只调整输入配置，调用兄弟目录的共享实现
├─ 实施记录.json          # 本版实际执行及未完成项
├─ 验收摘要.json          # 本版实际数量与验收状态
├─ 验收报告.md            # 来源、point、父块回取与备份恢复证据
├─ 待处理图片.md          # 失败原图入口及不确定区域清单
├─ .gitignore             # 排除数据、缓存和私密配置
├─ .gitattributes         # 保持配置和代码指纹的原始字节
├─ .cache/                # 本版准备/识别工作数据，Git忽略
└─ outputs/README.md      # 本版准备、识别、向量批次的时间和正式入口
```

模型调用代码、数据库迁移、运行环境、凭证及计算缓存集中在`../向量化与存储工程/`，避免两份同功能代码和不一致参数。每版的来源、派生OCR对象、PG关系与发布指针仍独立；相同图片的识别和相同实际输入的向量只共享计算，不合并政策身份。

数据库schema为`rag_oss`，知识库ID为`guance_oss_text`，collection前缀为`guance_oss`。上游只读，不重写原始结果。按用户2026-10-23的要求，`coverage_policy=all_existing_content`将原生子块全部纳入，质量问题保留提示；超长输入完整分片，父块保留原文。已确认跨父块的“确/保”另补一个完整父级上下文并关联相应表示。已有不确定转录及OCR失败资源的原图也向量化，缺少实际文件的引用仍保留缺图记录。详细职责见共享README中的text_inputs.py和coverage.py。

## 运行

依赖和数据库就绪后，在本目录执行：

```powershell
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py prepare --sample
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py prepare --all
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py build --sample --allow-partial
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py build --all --allow-partial
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py verify
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py smoke
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py backup
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py restore-drill
```

prepare仅冻结和核验，不联网、不生成向量。build才执行真实OCR与向量生成；只有全量对账通过后才生成`当前批次.json`。本版文件、批次或状态变化时同步更新本README与outputs索引。
