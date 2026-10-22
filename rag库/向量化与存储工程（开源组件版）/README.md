# 向量化与存储工程（开源组件版切块输入）

使用`../切块工程（开源组件版）/当前批次.json`指向的冻结结果，执行与原版相同的文本/图片向量化及存储规则，依据设计方案2.0。**这不是设计方案1.0与2.0的对照，而是两版切块输入的对照。**

用户已确认同时实施图片识别、语义切块和多模态向量化。详细实现、文件职责、限制和公共命令见[共享工程README](../向量化与存储工程/README.md)。

## 当前状态

2026-10-22正在实施。共享实现的23项回归通过，真实文本与图像Embedding接口验证通过，本机PostgreSQL与Qdrant已启动；两版全量图片识别共享实际计算，来源导入正在执行。尚无已激活的正式向量批次。执行状态见[实施记录](实施记录.json)及[批次索引](outputs/README.md)。

## 当前目录与作用

```text
向量化与存储工程（开源组件版）/
├─ README.md              # 本版输入、隔离、状态与运行方式
├─ AGENTS.md              # 继承共享工程的维护要求
├─ config.json            # 开源组件切块输入、rag_oss schema、guance_oss collection前缀
├─ run.py                 # 只调整输入配置，调用兄弟目录的共享实现
├─ 实施记录.json          # 本版实际执行及未完成项
├─ .gitignore             # 排除数据、缓存和私密配置
├─ .gitattributes         # 保持配置和代码指纹的原始字节
├─ .cache/                # 本版准备/识别工作数据，Git忽略
└─ outputs/README.md      # 本版准备、识别、向量批次的时间和正式入口
```

模型调用代码、数据库迁移、运行环境、凭证及计算缓存集中在`../向量化与存储工程/`，避免两份同功能代码和不一致参数。每版的来源、派生OCR对象、PG关系与发布指针仍独立；相同图片的识别和相同实际输入的向量只共享计算，不合并政策身份。

数据库schema为`rag_oss`，知识库ID为`guance_oss_text`，collection前缀为`guance_oss`。上游及原版工程只读，不因已知切块问题重写原结果；阻断问题在本版资格清单记录排除。

## 运行

依赖和数据库就绪后，在本目录执行：

```powershell
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py prepare --sample
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py prepare --all
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py build --sample
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py build --all
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py verify
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py smoke
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py backup
..\向量化与存储工程\.venv\Scripts\python.exe -X utf8 run.py restore-drill
```

prepare仅冻结和核验，不联网、不生成向量。build才执行真实OCR与向量生成；只有全量对账通过后才生成`当前批次.json`。本版文件、批次或状态变化时同步更新本README与outputs索引。
