# 政策 Facets 本地资源准备

一、用途与数据边界

本说明覆盖完整大陆行政树资源准备、真实发布目录地区候选核验和批准后映射部署。准备过程离线运行，只读打开当前 RAG 运行库 SQLite，不修改运行库、不调用模型、不访问网络。

官方地区树的再分发许可尚未确认。准备产物必须留在本机 `Documents/CC-Projects/guance-facets-runtime/regions`，不要提交公开仓库。公开代码仓库仅包含本构建脚本和本说明；不包含原始响应、地区树快照、真实政策映射、审查记录或本机路径。

未提供逐篇审查决议时，脚本只产生地区树和待审候选证据，`manifest.json` 中 `mapping_status` 为 `unapproved`，没有 `policy-region-map.json`，运行时地区 Facets 必须不可用并返回契约规定的 503。脚本生成的唯一候选不能自动视为批准。

二、资源格式与版本规则

1. `mainland-province-prefecture-county.json` 保存经来源 hash 固定的原始过滤树，节点保留 `code`、`name`、来源 `level`、来源 `type` 与 `children`。文件 SHA-256 必须为 `64d2152a679749298436d1806a9256e146ee0c94a85203ceeb70471ce634c446`。
2. API 节点形状由 loader 根据 `type` 映射 `source_type` 和 `tier`。Canonical 节点序列化按 UTF-8、`ensure_ascii=False`、对象键排序、紧凑分隔符；节点 canonical SHA 必须为 `sha256:94e2bdbeb629a4e523a1c0581f8d234844a81a36066ade59f06e08eb143e9cdb`，字节数 313818。两个 hash 表示不同内容，不能互换。
3. `manifest.json` 记录来源、请求参数、抓取时间、`snapshot_as_of`、`official_version`、原始响应和过滤树 SHA、节点计数、`region_version`、当前目录 `generation_id`/`revision`、目录完整数及 `partial` 状态、候选证据 SHA 与未归类原因。当前样本为 31 省、333 地级、2847 县级，共 3211 节点；不含乡镇、港澳台。
4. `policy-region-review.json` 为完整目录的逐篇候选审查证据，按字符串 `planid` 排序。每条记录含源 metadata `data_hash`、`admin_level`、`area_name`、`parent_area_name`、这些字段的 canonical hash、行政级别约束下的所有同名候选、候选完整祖先链及直接父级名称精确比较结果。唯一匹配状态仍是 `candidate_pending_independent_review`。
5. 只有主审独立审查后，才可准备 `review-decisions.json` 并将其显式传给脚本。该决议必须列出当前 generation/revision 下全部 planid、每条 `region_status` 和 `region_code`、审批者、审核时间及绑定的 `reviewed_audit_sha256`。脚本验证映射集合完整，mapped 只能选择唯一且直接父级精确相等的候选，national 必须是 `not_applicable`。随后才生成 canonical `policy-region-map.json`、`region_mapping_version` 和 `mapping_status=approved`。该 map 仍只写入本地资源目录。

三、确定性准备流程

在 PowerShell 中运行以下命令。把 `<METADATA_DB>` 替换为当前 RAG 运行库 `metadata.sqlite3` 的路径，把 `<RELEASE_MANIFEST>` 替换为与活动 generation/revision 对应的发布数据清单，把 `<PREPARED_DOCUMENTS>` 替换为该 release prepared 目录下 `documents.jsonl` 的路径，把 `<RAW_RESPONSE>` 替换为经过原始响应 SHA 校验的官方响应文件。省略 `--output-dir` 时产物写入用户 `Documents/CC-Projects/guance-facets-runtime/regions`。

```powershell
python web/backend/scripts/prepare_policy_regions.py `
  --metadata-db "<METADATA_DB>" `
  --release-manifest "<RELEASE_MANIFEST>" `
  --prepared-documents "<PREPARED_DOCUMENTS>" `
  --raw-response "<RAW_RESPONSE>"
```

当前已核验的本地目录身份为 506 篇、`revision=1`、发布状态 `ready`、`partial=true`。每次准备都在一个 SQLite 只读事务中读取活动 runtime、release 和 documents，校验逐行 `data_hash`；同时比对发布数据清单的 generation/revision/count/partial，并要求 prepared `documents.jsonl` 的完整内部 id 集合与数据库一致。目录版本改变时必须重新生成并审查，不可复用旧决议。

四、候选审查与批准后生成

候选证据按直接父级精确匹配建立。`admin_level` 限定候选层级；`province` 根节点的显式父级规则为“中华人民共和国”；市级仅允许地级树节点或 source_type 为“直辖市”的省级根节点；县级只允许县级树节点。名称相似、代码前缀、历史地名推断和通用父级类别不构成映射证据。

对 candidate sidecar 的每条记录进行独立判定。对保留映射，决议中的 `region_code` 必须是该记录唯一候选代码；对无法证实或不唯一的记录，使用 `region_status=unclassified` 且 `region_code=null`。全国级记录使用 `region_status=not_applicable` 且 `region_code=null`。审核决议 JSON 形状如下，`entries` 必须覆盖 sidecar 中全部记录：

```json
{
  "approved_by": "reviewer identity",
  "reviewed_at_utc": "2026-10-31T00:00:00Z",
  "review_method": "independent record-by-record evidence review",
  "reviewed_audit_sha256": "<candidate audit SHA-256>",
  "entries": [
    {"planid": "example-id", "region_code": "110101000000", "region_status": "mapped"}
  ]
}
```

经主审完成逐篇核验后，运行：

```powershell
python web/backend/scripts/prepare_policy_regions.py `
  --metadata-db "<METADATA_DB>" `
  --release-manifest "<RELEASE_MANIFEST>" `
  --prepared-documents "<PREPARED_DOCUMENTS>" `
  --raw-response "<RAW_RESPONSE>" `
  --review-decisions "<REVIEW_DECISIONS>"
```

loader 只接受 `manifest.json` 标记为 `approved` 且 generation、revision、canonical 映射 SHA 和完整 planid 集合均匹配的工件。未审决议、旧目录版本、缺记录、重复记录或校验失败时不得开放地区 Facets。

部署本地后端时，将 `GUANCE_POLICY_REGIONS_DIR` 指向 `<LOCAL_RESOURCES_DIR>\regions`；该目录应包含 `manifest.json`、地区树、候选审查记录和经批准的 `policy-region-map.json`。PowerShell 设置方式：

```powershell
$env:GUANCE_POLICY_REGIONS_DIR = "<LOCAL_RESOURCES_DIR>\regions"
```

修改政策发布 generation/revision 后，必须重新运行 builder 并重新逐篇审核，旧映射会因版本绑定不匹配而失效。

需要单独启动只读政策 API 时，在 backend 工作目录使用实现分支提供的 `app.policy_app:app`。它只初始化政策目录读服务，不启动 Agent 热加载或模型；单独端口可避开已被 RAG 服务占用的 8000。设置两个资源环境变量后运行：

```powershell
$env:RAG_STORE_PROJECT = "<LOCAL_RAG_RUNTIME_DIR>"
$env:GUANCE_POLICY_REGIONS_DIR = "<LOCAL_RESOURCES_DIR>\regions"
uvicorn app.policy_app:app --host 127.0.0.1 --port 8010
```

若部署时使用集成应用，可在原有单进程 `app.main:app` 中挂载政策路由，不必另外启动只读政策进程。

五、来源和复核限制

地区数据来自[民政部国家地名信息库](https://dmfw.mca.gov.cn/9095/xzqh/getList?code=0&maxLevel=3)公开查询接口，参数 `code=0`、`maxLevel=3`；本次抓取时刻为 `2026-10-31T06:49:42.361Z`。该时间是绑定在已核验原始响应上的历史抓取时刻，不是脚本现在抓取的时间。当前 snapshot 和官方历史版本均为 `null`，不能把结果描述成某个历史版本。原始响应 SHA-256 为 `2e50f7fb652571132487508e0dd9c8fae1f141e63adaab46fec7389447180739`。来源接口可查询公开，但再分发许可没有确认。

给定经过SHA校验的历史原始响应时，builder按原树嵌套关系过滤省级根代码 `810000000000`、`820000000000` 和来源缺码哨兵 `资料暂缺`，不按地区名称或代码前缀推断范围；要求剩余31个根。它保留原始节点字段和顺序，并以 UTF-8、`ensure_ascii=False`、二空格缩进、CRLF换行且无末尾换行序列化。此规则已经在原始 SHA `2e50...0739` 上实测得到逐字节一致的过滤树 SHA `64d2...c446`。随后 builder规范化节点并校验 canonical SHA 和长度。若官方原始响应内容变化，脚本会因原始或派生 hash 不匹配而停止，不能静默刷新生产版本。构建脚本不执行网络请求。

地区树计数和 hash 只证明本地工件与已记录快照一致；不证明官方历史发布版本，也不证明发布目录与 Release 压缩包逐字节一致。当前发布清单 `partial=true`；这不改变身份绑定，但报告和部署界面必须保留此状态。
