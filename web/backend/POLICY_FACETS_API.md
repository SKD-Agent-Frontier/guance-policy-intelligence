# 政策目录 Facets 与地区树 API 契约

版本：v1.0冻结契约；接口尚未实现。本文在不改变现有目录行为的前提下，定义四维 Facets、行政区筛选、完整大陆地区树及跨请求版本固定。前端仅在相应实现合并并部署后方可调用新增能力。

一、目标与兼容边界

（一）四个一级维度

1. `admin_level`：文献行政级别，取值为 `national`、`province`、`prefecture`、`county`、`unclassified`。当前 metadata 原值映射为：`国家`/`国家级` → `national`；`省`/`省级` → `province`；`市`/`市级` → `prefecture`；`区县`/`区县级`/`县`/`县级` → `county`。其它缺失或未登记原值为 `unclassified`，不得用地区名称或代码猜级别。此项分类与地区树 `tier` 是不同字段。
2. `year`：目录现有算法推导的参考年份；未知年份用专用字面值 `unknown` 查询，响应值仍为 `null`。
3. `fyp`：规划时期，继续使用现有目录规划期规范化与精确匹配规则。
4. `plantype`：政策类型，继续使用现有政策类型精确匹配规则。
5. 省、市、县地区选择依赖 `admin_level`，不是第五个一级 Facet。地区选择按真实父子链分为 `province_code`、`prefecture_code`、`county_code` 三个参数；无地区映射的文献通过独立 `region_status=unclassified` 筛选，不改变其原 `admin_level`。

（二）当前已实现接口

1. `GET /api/policies`：已有 `q`、`area`、`fyp`、`plantype`、`page`、`page_size`；按筛选条件统计全库 `matched_documents`，对完整匹配集合排序后分页。
2. `GET /api/policies/{planid}`：返回单篇元数据与章节目录。
3. `GET /api/policies/{planid}/content`：按正文码点偏移分页读取冻结正文。
4. 所有已实现目录响应使用 `dataset.generation_id` 与 `dataset.revision` 表示当前发布；目录不可用时返回 503，不回退 demo 数据。详见仓库内 [政策目录 API 契约](POLICY_CATALOG_API.md)、[目录路由](app/policy_routes.py)、[目录服务](app/policy_catalog.py)。

（三）兼容要求

1. 新增能力为增量扩展。旧请求无需传新增筛选或版本参数；不得移除 `area` 的 `metadata.area` 精确匹配、现有 `fyp` 别名、`plantype` 精确匹配、分页语义、排序规则或 `dataset.generation_id/revision`。
2. 现路由拒绝未知参数为 HTTP 400；类型及 FastAPI 参数范围错误沿用现有 422 行为。任何新增查询参数必须同步加入 allowlist、服务筛选逻辑、响应模型、OpenAPI 和测试。
3. `GET /api/policies/facets` 与静态路由 `GET /api/policies/regions` 必须在动态路由 `GET /api/policies/{planid}` 之前注册，避免路径冲突。
4. Facets、地区计数和扩展后的列表必须使用同一个目录快照及已批准地区映射版本。不得暴露本机绝对路径、数据库路径、冻结文件名、内部 `document_id` 或审查环境信息。

二、真实数据字段与目录语义

（一）经身份校验的发布元数据

1. 只读字段审计基于与发布清单的 generation/revision 身份匹配的隔离副本，共 506 份文档。审计没有从 Release 分卷重新恢复，因此不证明 Release 附件与副本逐字节一致，也不证明当前线上实例正在运行此副本。
2. `admin_level`：506 条非空，四值为区县 290、市 155、省 43、国家 18。
3. `area_name`：506 条非空，416 个不同值；`area_level`：506 条非空，415 个不同值；没有 `area_id`、`parent_id`。`parent_area_name` 有 488 条非空、18 条为空。
4. `fyp_name`：17 条空值；六种非空原值中，第十四个五年规划 262、第十三个五年规划 64、第十二个五年规划 34、第十一个五年规划 22、第十个五年计划 12、政府工作报告 95。
5. `plantype` 有 3 个值：`1` 为 87 条、`2` 为 409 条、`3` 为 10 条；`plantype_label` 分别为总体规划纲要 87、专项规划 409、其他 10。
6. metadata 没有 `year` 字段。参考年份必须由已实现 `reference_year` 算法计算，不能称作真实原始年份列。
7. 数据库中存在相同的 `area_level` 对应历史县名与当前市名的记录。因此不能把该字段直接视为官方地区树唯一代码，也不能仅凭代码或名称补造父级。地区映射审查中 447 条记录得到唯一直接父级候选（省 43、市 155、县 249）；另有 41 条县级记录尚未得到唯一候选；国家级 18 条是 `national`，不属于地区未分类。
8. 以上匹配数量仅为当前身份匹配副本上的候选核查结果，并非已批准或已发布的地区映射结果。不得在实现、响应示例、验收或产品说明中承诺这些候选数就是上线映射数。无法唯一审定的地区必须保持 `region_status=unclassified`。

（二）年份与规划期

1. `year` 继续完全复用 `reference_year(title, fyp)`：标题有唯一明确年份、无年份范围且非远景目标年份时采用标题年；否则对已识别的规范五年规划时期采用起始年；都无法确定时结果为 `null`。
2. 现有已知规划期映射为第十至第十五个五年规划/计划起始年 2001、2006、2011、2016、2021、2026；第十个五年计划按当前 `normalize_fyp` 规范为第十个五年规划。
3. 当前身份匹配副本按同一算法的年份统计为：2021 年 262、2016 年 72、2011 年 41、2006 年 23、2001 年 13、2010 年 12、2012 年 11、2020 年 10、2017 年 9、2018 年 6、2019 年 6、2009 年 5、2015 年 5、2007 年 4、2008 年 4、2014 年 3、2013 年 2、2005 年 2、2004 年 1，未知 15。该派生值不是发布日期字段。
4. 规划时期的目录规范化结果为第十四个五年规划 262、第十三个五年规划 64、第十二个五年规划 34、第十一个五年规划 22、第十个五年规划 12、政府工作报告 95，另有 17 条原值为空。Facet 不补造缺失规划期。

三、大陆行政区划树与地区映射

（一）来源与完整范围

1. 官方数据源为民政部国家地名信息库公开查询接口，本次实际请求参数为 `code=0`、`maxLevel=3`，来源为[民政部国家地名信息库查询接口](https://dmfw.mca.gov.cn/9095/xzqh/getList?code=0&maxLevel=3)；抓取时间为 `2026-10-31T06:49:42.361Z`。
2. 本次过滤树共 3211 个节点：31 个省级节点、333 个地级节点、2847 个县级节点。来源节点保留实际父子关系；直辖市下辖县级节点直接挂在省级节点，部分省级行政区的县级节点也会直接挂省级节点。来源第三层是数据源层级字段，不能据此声称县级节点必有地级父级。
3. 范围限中国大陆，不含乡镇、香港、澳门和台湾。`GET /api/policies/regions` 返回整个范围的树，不从政策数据有无命中裁剪节点，计数为 0 的节点也保留。
4. 本次树 `snapshot_as_of` 为 `null`，`official_version` 为 `null`。官方页面的默认表格参数不属于本次树请求，不能据此宣称树属于某个 2025 版本或其它历史版本。`region_version` 按节点 canonical JSON 生成：节点递归保留 `code`、`name`、原始 `source_type`、映射后的 `tier`、`children`；对象键按 `sort_keys=True` 排序，数组保持来源顺序，采用 `ensure_ascii=False` 与紧凑分隔符 `separators=(",", ":")` 序列化为 UTF-8，再计算 SHA-256。Canonical JSON 为 3211 节点、313818 字节，所得 `region_version` 为 `sha256:94e2bdbeb629a4e523a1c0581f8d234844a81a36066ade59f06e08eb143e9cdb`。含抓取时刻等元信息的整个过滤树文件 SHA 另记为 `artifact_sha256`，不能拿它替代 `region_version`。
5. 本次完整过滤树工件 `artifact_sha256` 为 `64d2152a679749298436d1806a9256e146ee0c94a85203ceeb70471ce634c446`；原始响应 SHA-256 为 `2e50f7fb652571132487508e0dd9c8fae1f141e63adaab46fec7389447180739`；覆盖审计 SHA-256 为 `cbeff06bc2afde2994d82f49fa3d9ab2d290cfbb5b87118bd739f6176bbf8b85`。生产 `region_version` 为节点 canonical SHA `sha256:94e2bdbeb629a4e523a1c0581f8d234844a81a36066ade59f06e08eb143e9cdb`；canonical JSON 为 3211 节点、313818 字节。工件 hash 与 canonical nodes hash 分开保存；二者均不等于官方历史发布版本。

（二）节点字段与层级

1. 每个地区节点公开 `code`、`name`、`source_type`、`tier` 和 `children`。`code` 使用来源提供的 12 位字符串；`source_type` 保留来源类型；父级由嵌套关系决定。
2. `tier` 只由 `source_type` 映射，不从名称、代码前缀或 `area_level` 反推：
   - `省`、`自治区`、`直辖市` → `province`；
   - `地级市`、`地区`、`自治州`、`盟` → `prefecture`；
   - `市辖区`、`县`、`自治县`、`县级市`、`旗`、`自治旗`、`特区`、`林区` → `county`。
3. 未登记的新 `source_type` 返回 `tier: "unknown"`，并标记映射待维护；不得根据名称或代码推测。
4. 来源子节点顺序作为同层稳定顺序。`children: []` 明确表示叶节点，不用缺字段或 `null` 代替空数组。

（三）地区映射发布门槛

1. 将政策地区关联树节点必须有显式审核、可复现的映射资源。候选判定结合 `admin_level`、`area_name`、`parent_area_name` 与来源树直接父级；父名称为缺失值或“市辖区”等通用类别时，不得用字符串猜测父代码。
2. 父节点只能在当前地区树中唯一确定时补齐；多匹配、历史更名、旧县/现市名冲突、通用父级名或没有可证实父级时不得自动选择。无唯一已批准映射即设 `region_status=unclassified`，保留原 `admin_level`。
3. 全国级文献以 `admin_level=national` 表示，不属于未分类地区；它不插入某个省节点，也不计入 `region_status=unclassified`。
4. 地区树、官方原始响应、清单和政策映射分别记录来源、请求参数、抓取 UTC、`snapshot_as_of`、`official_version`、解析/映射算法版本与 SHA-256。`region_mapping_version` 只在映射审核通过后发布，输入为 canonical 对象 `{"algorithm_version":"<version>","entries":[...]}`；`entries` 按 `planid` 升序并记录每个文档的 `planid`、`region_code`、`region_status`。使用 UTF-8、`ensure_ascii=False`、紧凑分隔符和对象键排序序列化，再计算 SHA-256。清单同时绑定该映射适用的 `generation_id`、`revision`，并校验映射 `planid` 集合与该目录文档集合完全一致。映射版只在映射条目或算法版本变化时更新。
5. 若后续许可与审查允许将数据作为源码运行资源，预定仓库相对路径为：`web/backend/app/resources/regions/mainland-province-prefecture-county.json`、`web/backend/app/resources/regions/manifest.json`、`web/backend/app/resources/regions/policy-region-map.json`。本次仅定义路径与契约，不将官方响应、过滤树或映射快照提交到公开仓库；当前公开契约只记来源、必要校验值和版本规则。

四、筛选参数与 Facet 计数

（一）通用过滤规则

1. 新列表筛选参数为 `admin_level`、`province_code`、`prefecture_code`、`county_code`、`region_status`、`year`、`fyp_status`、`plantype_status`；旧参数 `q`、`area`、`fyp`、`plantype`、`page`、`page_size` 保持原名和语义。
2. `q` 始终参与所有 Facet 和地区节点计数。它继续按现有规则匹配标题或正文中的字面连续子串；省略表示不做关键词检索。
3. 旧 `area` 仍对 `metadata.area` 做精确相等筛选。它与任一新地区代码条件同时出现时按 AND 组合，不改为层级匹配或别名匹配。
4. Facet 计数是去重文档数，一个 `planid` 最多计一次；与 `page` 和 `page_size` 无关。列表仍按完整匹配集合排序后分页。
5. 同一参数重复出现一律拒绝，不得采用第一个或最后一个值。多种地区选择必须符合树中的直接父链，不相容的组合返回 HTTP 400。

（二）单值参数及范围

1. `admin_level` 接受 `national`、`province`、`prefecture`、`county`、`unclassified` 五个枚举。`unclassified` 仅匹配行政分类无效或缺失的记录，不匹配地区未映射但行政分类仍有效的县级文献。
2. `province_code` 必须是当前地区树的省级 12 位代码；`prefecture_code` 必须是当前树中的地级代码；`county_code` 必须是当前树中的县级代码。不存在、层级不符、重复或彼此父链冲突时返回 400。
3. `prefecture_code` 可省略。单独传 `prefecture_code` 时，从树中补齐唯一 `province_code`；单独传 `county_code` 时补齐实际存在的省级父级及（仅当县节点真实隶属地级节点时）`prefecture_code`。响应 `query` 回显规范化后补齐的祖先链。县级节点若直接隶属省级节点（例如直辖市辖区或省直辖县级节点），只需传 `province_code` 与 `county_code`；不得创建虚构的地级占位节点。
4. 多个地区代码按路径交集处理：传入的省、地、县代码必须是所选节点的真实祖先；任意父子关系冲突返回 400。省代码单独筛省及其有效后代；地代码单独筛该地及其有效县级后代；县代码只筛该县节点。`admin_level=national` 与任何省/地/县代码同时出现均返回 400，因为全国级记录不属于地区树；与 `region_status=unclassified` 组合按有效交集返回 200 零结果，与 `region_status=not_applicable` 组合时正常命中国家级记录。`admin_level=county` 可与省代码及其直接县级子节点组合；直辖市与其它无地级节点省份在 `admin_level=prefecture` 下没有地级候选项，不造假地级项，选择县级后即可选真实县级子节点。
5. `region_status` 接受 `mapped`、`unclassified`、`not_applicable`，分别匹配已映射地区、未能唯一映射地区、以及不适用地区树的国家级记录。省/地/县代码与 `unclassified` 或 `not_applicable` 同传返回 400。它不改变 `admin_level`，例如县级未映射记录仍可用 `admin_level=county` 与 `region_status=unclassified` 组合筛选。
6. `year` 查询值按字符串解析，只接受 `1900` 至 `2099` 的四位数字或 `unknown`。不匹配此格式、越界或重复参数一律返回 HTTP 400；响应 query 中已知值规范为整数、未知值规范为 `unknown`。响应中年份选项用整数表示；未知年份用 `value: "unknown"`，列表中 `year` 仍为 `null`。
7. `fyp` 与 `plantype` 沿用现有最大 50 字符、非空字符串和任意精确值规则。合法但目录中没有的值返回零结果，不拒绝未知字符串。`fyp_status=unclassified` 和 `plantype_status=unclassified` 分别筛选缺失值；每个 status 参数只接受 `unclassified`，并且不能与对应的 `fyp` 或 `plantype` 同时出现。使用独立状态参数避免与可能存在的真实规划期或类型字符串冲突。
8. `q` 继续最多 100 字符并拒绝空白查询；`page` 从 1 开始，`page_size` 为 1–100。新的筛选不放宽现有范围。

（三）各 Facet 的 self-exclusion 口径

1. 四个一级 Facet 精确命名为 `admin_level`、`year`、`fyp`、`plantype`；`admin_level` Facet 子对象内包含 `regions` 地理选择数据。
2. `year` Facet 计数保留 `q`、其它行政/地区条件、`fyp`、`fyp_status`、`plantype` 和 `plantype_status`，排除当前 `year`。
3. `fyp` Facet 计数保留 `q`、其它行政/地区条件、`year`、`plantype` 和 `plantype_status`，排除当前 `fyp` 及 `fyp_status`。
4. `plantype` Facet 计数保留 `q`、其它行政/地区条件、`year`、`fyp` 和 `fyp_status`，排除当前 `plantype` 及 `plantype_status`。
5. `admin_level` 分类选项计数保留 `q`、`year`、`fyp`、`fyp_status`、`plantype`、`plantype_status`，排除当前 `admin_level`、`region_status`、`province_code`、`prefecture_code`、`county_code` 和旧 `area` 条件。所有五个 `admin_level` 选项，包括 `unclassified`，始终返回，即使计数为 0。
6. 省级地区选项计数保留 `q`、`admin_level`、`year`、`fyp`、`fyp_status`、`plantype` 和 `plantype_status`；排除全部地区代码、旧 `area` 与 `region_status`，显示所有省级选项，包括计数为 0 的项。
7. 未选省级时，地级选项数组为空；选中有效省级后，地级选项计数保留该 `province_code` 及 `q`、`admin_level`、`year`、`fyp`、`fyp_status`、`plantype` 和 `plantype_status`；排除 `prefecture_code`、`county_code`、旧 `area` 和 `region_status`。若省级条件与候选地级节点不在同一父链中，该候选计数为 0；无地级节点的省份不造假选项。
8. 县级选项计数保留有效的 `province_code` 和（若存在）`prefecture_code` 上级选择及 `q`、`admin_level`、`year`、`fyp`、`fyp_status`、`plantype` 和 `plantype_status`；排除 `county_code`、旧 `area` 和 `region_status`。只选择省级且未选地级时，县级列表只含该省直接子节点，不展开其下地级节点；选中地级后才列出该地的县级子节点。对直接挂在省级下的县级节点，省级祖先保留、地级祖先不存在；不插入假地级节点。
9. 对所有省、地、县级地区节点，`count` 都是在对应地区 Facet 保留条件下，`region_status=mapped` 且 `region_code` 等于候选节点或其真实后代的去重 `planid` 数；若请求提供 `admin_level`，文献规范级别必须匹配该值。国家级记录不计入任何省、地、县节点。
10. `regions.unclassified` 计数保留 `q`、`admin_level`、`year`、`fyp`、`fyp_status`、`plantype` 和 `plantype_status`，排除全部地区代码、`region_status` 和旧 `area`。它只计地区映射未定的记录；全国级 18 条不得计入其中。`regions.not_applicable` 使用相同排除规则，只统计全国等不适用地区树的记录。
11. 所有 Facet 候选值来自当前目录可识别的完整值集合；在其它条件下计数为 0 时仍返回。`fyp.unclassified`、`plantype.unclassified` 以及 `year=unknown` 选项始终返回，零值时保留 `count: 0`。地区树无论计数为 0 与否都完整返回；不因当前筛选或匹配结果裁剪结构。

（四）值、空值与排序

1. `admin_level` 的服务端标签为“国家级”“省级”“市级”“区县级”“未分类”；底层原值分别规范到 `national`、`province`、`prefecture`、`county`、`unclassified`。国家级不是地区未分类。
2. `year` 选项使用 `{value: 整数, label: 年份字符串, count: 非负整数}`；未知年份使用 `{value: "unknown", label: "年份未知", count: 非负整数}`。列表响应仍用整数或 `null`。
3. `fyp` 和 `plantype` 的非空选项使用实际值 `{value, label, count}`；缺失值计数放在独立 sibling 对象，例如 `fyp.unclassified: {label: "未分类", count}`、`plantype.unclassified: {label: "未分类", count}`。查询通过各自 status 参数筛选，绝不把 sentinel 放进真实 `options[].value`。
4. 行政层级按国家、省、地、县、未分类；年份按已知年份降序、未知值最后；`fyp` 与 `plantype` 按规范值 Unicode 码点升序；地区兄弟节点按来源顺序。排序不依赖哈希表遍历顺序。

五、新增与扩展接口

（一）Facets

`GET /api/policies/facets`

1. 接受第四节列出的过滤条件及第六节版本 pin 参数；不接受 `page` 或 `page_size`。未知参数仍返回 HTTP 400。
2. 响应含完整 `dataset`、规范化 `query` 和四个一级 facet 键：`admin_level`、`year`、`fyp`、`plantype`。`facets.admin_level.regions` 内含 `province_options`、`prefecture_options`、`county_options`、地区 `unclassified` 和地区 `not_applicable` 计数。省级列表可完整显示；未选省级时地级、县级列表为空；地区树接口仍提供完整结构。不返回第五个顶层 `region` facet。
3. 下方是小型契约 fixture 示例，不代表生产地区全集，也不声称示例值为当前线上计数。生产返回完整 3211 节点树；本接口地区选项只展示本次请求的有效父链范围。

契约示例统一使用以下三条完整 fixture 文献；其 `dataset.total_documents` 在列表与 Facets 中均为 3：

1. `p1`：标题“数字经济促进政策2021年”，正文包含“数字经济”；`area=北京市`，`fyp=第十四个五年规划`，`year=2021`，`plantype_label=专项规划`，`admin_level=county`，映射到东城区。
2. `p2`：标题“数字经济实施方案2020年”，正文包含“数字经济”；`area=北京市`，`fyp=政府工作报告`，`year=2020`，`plantype_label=总体规划纲要`，`admin_level=county`，映射到西城区。
3. `p3`：标题“数字经济基础设施方案”，正文为“数字经济”；`area=北京市`，`fyp=null`，`year=null`，`plantype_label=其他`，`admin_level=county`，没有唯一地区映射，`region_status=unclassified`。

以下 fixture versions 均为合法 SHA-256 形状的示例值：`region_version=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`，`region_mapping_version=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`。

请求示例：

```http
GET /api/policies/facets?q=数字经济&area=北京市&admin_level=county&year=unknown&generation_id=fixture-gen&revision=1&region_version=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&region_mapping_version=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
```

完整响应示例：

```json
{
  "dataset": {
    "generation_id": "fixture-gen",
    "revision": 1,
    "total_documents": 3,
    "partial": false,
    "source": "current_published_frozen_release",
    "region_version": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "region_mapping_version": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "query": {
    "q": "数字经济",
    "area": "北京市",
    "admin_level": "county",
    "province_code": null,
    "prefecture_code": null,
    "county_code": null,
    "region_status": null,
    "year": "unknown",
    "fyp": null,
    "fyp_status": null,
    "plantype": null,
    "plantype_status": null
  },
  "facets": {
    "admin_level": {
      "options": [
        {"value": "national", "label": "国家级", "count": 0},
        {"value": "province", "label": "省级", "count": 0},
        {"value": "prefecture", "label": "市级", "count": 0},
        {"value": "county", "label": "区县级", "count": 1},
        {"value": "unclassified", "label": "未分类", "count": 0}
      ],
      "regions": {
        "province_options": [
          {"code": "110000000000", "name": "北京市", "source_type": "直辖市", "tier": "province", "count": 0}
        ],
        "prefecture_options": [],
        "county_options": [],
        "unclassified": {"label": "地区未分类", "count": 1},
        "not_applicable": {"label": "不适用地区树", "count": 0}
      }
    },
    "year": {
      "options": [
        {"value": 2021, "label": "2021", "count": 1},
        {"value": 2020, "label": "2020", "count": 1},
        {"value": "unknown", "label": "年份未知", "count": 1}
      ]
    },
    "fyp": {
      "options": [
        {"value": "政府工作报告", "label": "政府工作报告", "count": 0},
        {"value": "第十四个五年规划", "label": "第十四个五年规划", "count": 0}
      ],
      "unclassified": {"label": "未分类", "count": 1}
    },
    "plantype": {
      "options": [
        {"value": "专项规划", "label": "专项规划", "count": 0},
        {"value": "其他", "label": "其他", "count": 1},
        {"value": "总体规划纲要", "label": "总体规划纲要", "count": 0}
      ],
      "unclassified": {"label": "未分类", "count": 0}
    }
  }
}
```

2. 真实字符串值 `"unclassified"` 若存在，仍以 `options[].value` 原样返回；缺失值计数始终放在独立 `unclassified` sibling 对象，以免冲突。

（二）完整地区树

`GET /api/policies/regions`

1. 返回完整大陆树，不分页；此树独立于政策目录，可在目录未就绪时单独读取。
2. 下面是只含一个省和两个县级节点的 fixture，仅说明结构，不代表生产返回被裁剪；生产 `data` 必须是全部 3211 个节点。

请求示例：

```http
GET /api/policies/regions
```

完整 fixture 响应：

```json
{
  "region_version": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "artifact_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "source": "国家地名信息库",
  "source_url": "https://dmfw.mca.gov.cn/9095/xzqh/getList?code=0&maxLevel=3",
  "retrieved_at_utc": "2026-10-31T06:49:42.361Z",
  "snapshot_as_of": null,
  "official_version": null,
  "node_count": 3,
  "data": [
    {
      "code": "110000000000",
      "name": "北京市",
      "source_type": "直辖市",
      "tier": "province",
      "children": [
        {"code": "110101000000", "name": "东城区", "source_type": "市辖区", "tier": "county", "children": []},
        {"code": "110102000000", "name": "西城区", "source_type": "市辖区", "tier": "county", "children": []}
      ]
    }
  ]
}
```

3. 本例 fixture 的 `node_count` 与数据节点数均为 3；生产响应中完整 `node_count` 为 3211，必须与完整 `data` 节点总数一致。生产节点及其未裁剪树由 `region_version` 内容 SHA 约束；`artifact_sha256` 记录含抓取元数据的完整文件 SHA。
4. `GET /api/policies/regions` 可接受可选 `region_version`。版本一致返回 200；版本不一致返回 409 并给出当前版本；未知参数返回 400。完整 tree 不包含文献计数，计数由 Facets 接口按目录版本计算。

（三）扩展后的政策列表

`GET /api/policies`

1. 保留原有分页与列表字段，仅增加第四节定义的筛选参数及响应 `dataset.region_version`、`dataset.region_mapping_version` 和规范化 `query` 字段。现有字段名、类型和默认值均不变。
2. 当 `fyp_status=unclassified` 时 `query.fyp` 保持 `null`，新增 `query.fyp_status` 显式说明状态筛选；`plantype` 同理。地区各层级和 `region_status` 也在 `query` 中原样回显。
3. 请求示例：

```http
GET /api/policies?q=数字经济&area=北京市&admin_level=county&year=unknown&generation_id=fixture-gen&revision=1&region_version=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&region_mapping_version=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
```

完整 fixture 响应：

```json
{
  "dataset": {
    "generation_id": "fixture-gen",
    "revision": 1,
    "total_documents": 3,
    "partial": false,
    "source": "current_published_frozen_release",
    "region_version": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "region_mapping_version": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "query": {
    "q": "数字经济",
    "area": "北京市",
    "admin_level": "county",
    "province_code": null,
    "prefecture_code": null,
    "county_code": null,
    "region_status": null,
    "year": "unknown",
    "fyp": null,
    "fyp_status": null,
    "plantype": null,
    "plantype_status": null
  },
  "matched_documents": 1,
  "page": 1,
  "page_size": 20,
  "total_pages": 1,
  "items": [
    {
      "planid": "p3",
      "title": "数字经济基础设施方案",
      "area": "北京市",
      "fyp": null,
      "plantype_label": "其他",
      "year": null,
      "admin_level": "county",
      "region_code": null,
      "region_status": "unclassified",
      "matches": [
        {"field": "title", "offset": 0, "snippet": "数字经济基础设施方案"},
        {"field": "body", "offset": 0, "snippet": "数字经济"}
      ]
    }
  ]
}
```

六、版本固定与错误响应

（一）版本组

1. Facets 与列表 `dataset` 增加 `region_version` 和 `region_mapping_version`，保留现有 `generation_id`、`revision`。完整版本组为四元组：`generation_id`、`revision`、`region_version`、`region_mapping_version`。`generation_id` 是 1–128 位 `[A-Za-z0-9_-]`；`revision` 为正整数；地区与映射版本为 `sha256:` 加 64 位小写十六进制。映射不可用时，列表 `dataset.region_mapping_version` 为 JSON `null`，pin 中用字面 `unavailable` 表示 null 版本。
2. 旧请求无需提供 pin。调用方若对列表或 Facets 传入四项中的任一项，必须传入完整四项；缺项或字段格式不合法返回 HTTP 400，revision 无法解析或小于 1 返回 HTTP 422。服务端对照实际 catalog generation/revision、地区树和审核映射内容版本；任一版本与当前不同返回 HTTP 409，不能静默切换到当前版本。同一完整版本组下，所有响应的 `dataset.total_documents` 与 `dataset.partial` 必须一致；若同一版本组产生不一致元数据，返回 HTTP 409 并记录内部一致性错误，不得静默返回不同快照。
3. 同一交互中的 Facets 与列表请求必须回传第一次响应中的完整四元组。地区树接口单独 pin `region_version`；它不依赖 RAG 版本或映射版本。
4. 没有已批准映射时，旧列表请求仍按当前 catalog 返回 200，不得因映射缺失而把既有接口整体降级为 503；它返回 `dataset.region_mapping_version: null`，新增 `region_code` 和 `region_status` 字段为 `null`（`admin_level` 仍按 metadata 映射）。地区树接口仍可用；需要地区计数/筛选的 Facets 接口返回 503，不得返回伪造的零计数。映射一经批准，canonical 映射内容 SHA 作为 `region_mapping_version`。

（二）状态码与完整错误示例

1. `200`：正常响应；合法的零匹配请求仍返回 200，`items` 为空，`matched_documents=0`，可用选项计数为零。
2. `400`：未知参数、同参数重复、地区代码无效、父子选择冲突、状态 sentinel 与实体值冲突。沿用现有 unsupported query / `InvalidParameter` 语义。
3. `404`：现有详情或正文接口对应政策不存在；Facets 或地区树本身没有“未找到”结果。
4. `409`：版本 pin 与服务实际数据版本冲突。
5. `422`：数值参数不能按声明类型解析或违反范围约束（例如 `page=0`）。沿用项目当前全局自定义验证处理器，响应体为 `{"detail":"请求参数无效"}`；这不是 FastAPI 标准错误数组。字符串筛选值格式错误按本契约明确规则返回 400。保持既有状态码和自定义错误体。
6. `503`：目录未配置、发布不可用、地区映射尚未审查发布或地区树资源不可读。地区树自身不依赖 RAG；树资源可读时地区树仍可返回 200。

400 示例：

```http
GET /api/policies?province_code=110000000000&prefecture_code=130100000000
```

```json
{"detail": "地区选择的父级关系冲突"}
```

409 示例：

```http
GET /api/policies/facets?generation_id=old-generation&revision=1&region_version=sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc&region_mapping_version=sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
```

```json
{"detail": "请求版本与当前目录或地区版本不一致", "current": {"generation_id": "gen-current", "revision": 2, "region_version": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "region_mapping_version": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}
```

422 示例：

```http
GET /api/policies?page=0
```

```json
{"detail":"请求参数无效"}
```

503 示例：

```http
GET /api/policies/facets
```

```json
{"detail": "地区映射尚未就绪"}
```

（三）边界输入

1. 地区代码与旧 `area` 有效但交集为空时返回 200 空结果；非法地区节点或父链不可能时返回 400。
2. `admin_level=county&region_status=unclassified` 合法，表示行政分类仍是县级但无唯一地区映射；`admin_level=national&region_status=unclassified` 返回零结果，`admin_level=national&region_status=not_applicable` 返回国家级记录。国家级搭配地区代码返回 400。
3. `fyp_status=unclassified`、`plantype_status=unclassified` 是缺失值筛选；真实 `fyp` 或 `plantype` 即使字符串恰为 `unclassified` 仍按真实值处理，状态选项与值选项在响应 schema 中分离。
4. 未知但语法合法的 `fyp` / `plantype` 字符串返回正常零匹配/零计数，不因新增 Facets 改变现有任意精确值语义；空字符串继续按现有规则拒绝。

七、响应字段结构要求

（一）`GET /api/policies/facets`

响应 `query` 必须回显所有已支持条件：`q`、`area`、`admin_level`、`province_code`、`prefecture_code`、`county_code`、`region_status`、`year`、`fyp`、`fyp_status`、`plantype`、`plantype_status`。未指定值为 JSON `null`。

`facets.admin_level.options` 是行政层级选项。`facets.admin_level.regions` 含 `province_options`、`prefecture_options`、`county_options`、`unclassified` 和 `not_applicable` 状态计数。每个地区节点选项字段为 `code`、`name`、`source_type`、`tier`、`count`。省级/地级/县级 options 按第四节的不同 self-exclusion 规则分别生成；地区未分类项单独使用 `region_status=unclassified`。

`facets.year.options` 使用已知年份和 `unknown` sentinel。`facets.fyp.options` 与 `facets.plantype.options` 只列实际值；missing 状态单独使用 sibling 对象 `facets.fyp.unclassified` 和 `facets.plantype.unclassified`。每个实际值项使用 `{value, label, count}`；缺失项不占用字符串值空间，即使真实值恰为 `"unclassified"` 也不会与缺失状态冲突。

（二）`GET /api/policies/regions`

响应包含 `region_version`、64位小写十六进制 `artifact_sha256`、`source`、`source_url`、`retrieved_at_utc`、`snapshot_as_of`、`official_version`、`node_count`、`data`。生产 `data` 包含 3211 个地区节点并保留来源嵌套关系；树资源版本由节点 canonical SHA 标识；完整 JSON 工件 hash 另存为 64 位小写十六进制 `artifact_sha256`（不带前缀）。该端点不返回政策匹配计数。

（三）扩展后的 `GET /api/policies`

列表 `items[]` 增加 `admin_level`、`region_code`、`region_status`；保持原 `planid`、`title`、`area`、`fyp`、`plantype_label`、`year`、`matches` 字段。`region_code` 在没有已批准映射的记录上为 `null`，`region_status` 在映射已加载时为 `mapped`、`unclassified` 或 `not_applicable`；地区映射未加载时为 `null`。不会把国家级记录标为地区未分类。

八、实现状态与验收边界

（一）现已实现

1. 现有列表、详情、正文接口及其参数、版本字段、字面检索、精确筛选和分页行为。
2. 列表和详情的 `year` 参考年份推导；它不是原始 metadata 字段。
3. 现有路由将未知 query 参数返回 400，并按项目全局自定义验证处理器返回 422：`{"detail":"请求参数无效"}`。

（二）尚待实现

1. `/api/policies/facets`、`/api/policies/regions`、地区层级三选择参数、地区状态筛选及已审核政策地区映射。
2. 四维 Facet self-exclusion、完整零计数地区树、`region_mapping_version`、四元组 pin/409 行为和各响应模型。
3. 所有 schema、OpenAPI、文档和测试须与本契约同步。新增静态路由均放在 `/{planid}` 之前。
4. 未实现并核验前不得对前端宣称新接口可用。

（三）拟纳入源码的资源位置

如后续确认来源数据可再分发并批准资源映射，资源分别放在 `web/backend/app/resources/regions/mainland-province-prefecture-county.json`、`web/backend/app/resources/regions/manifest.json`、`web/backend/app/resources/regions/policy-region-map.json`。本次仅定义路径和格式，不提交原始响应、地区树快照或映射资源。公开提交契约时仅携必要来源说明和内容版本规则，不带审查机路径、Release附件、凭证或测试环境标识。

（四）代码基线

契约按远端 `main` HEAD `918d300c2d755e2c9cb8656d5538314bad8ed064` 核查。对应实现文件为 `web/backend/app/policy_routes.py`、`web/backend/app/policy_catalog.py`、`web/backend/app/policy_schemas.py`、`web/backend/POLICY_CATALOG_API.md` 和 `web/backend/tests/test_policy_catalog.py`。仓库根没有 `AGENTS.md`；新增接口的代码和文档审查以仓库自身规则及既有目录实现为准。