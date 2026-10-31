# 政策文献目录与精确检索 API

本契约描述「政策文献目录服务」的 HTTP 接口。该服务独立于 Agent、五项业务模块与任务准备层：不调用任何大模型、不写文件、不做语义检索，只读取**当前已发布的 RAG 冻结运行库**。

默认 Base URL：`http://127.0.0.1:8000`。所有接口均为 `GET`、无认证（与现有本地 MVP 一致），响应为 UTF-8 JSON。

## 一、接口列表

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/policies` | 列举当前发布库文献目录；支持字面关键词检索、筛选与分页 |
| `GET` | `/api/policies/{planid}` | 单篇政策身份元数据与章节目录 |
| `GET` | `/api/policies/{planid}/content` | 按偏移分页读取冻结正文 |

接口在 Web 服务启动后按需加载目录；未配置真实 RAG 运行库或发布档案不可读时，三个接口统一返回 **503**，不会返回任何伪造文献数据。

## 二、数据来源与版本

| 项 | 说明 |
| --- | --- |
| 数据源 | RAG 运行库中 `runtime` 表指向的**当前发布**（`generation`） |
| 元数据 | `metadata.sqlite3` 的 `releases / documents / objects` 表 |
| 原文 | release manifest 中 `prepared_path` 指向的冻结快照（只读，校验 SHA-256） |
| 历史发布 | **不读取**：只加载 `release.document.jsonl` 中属于当前 generation 的文档 |
| 版本标识 | 每个响应都带 `dataset.generation_id` 与 `dataset.revision` |
| partial | 发布清单的媒体缺口标记（	rue 表示该发布带图片/OCR 缺口说明；不影响文本目录与文本检索） |

## 三、列举目录 / 关键词检索 / 筛选

### （一）请求参数

`GET /api/policies`

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `q` | string | 无 | 字面连续子串查询，匹配**标题或正文**；省略时仅列举目录 |
| `area` | string | 无 | 地区筛选，与元数据 `area` **精确相等** |
| `fyp` | string | 无 | 规划期筛选，与元数据 `fyp` 精确相等；接受 `十四五` 等展示别名（规范化到 `第十四个五年规划`，不做日期推断） |
| `plantype` | string | 无 | 政策类型筛选，与元数据 `plantype_label` 精确相等 |
| `page` | int | `1` | 分页页码，从 1 开始 |
| `page_size` | int | `20` | 每页条数，1–100 |

### （二）匹配规则

1. `q` 采用**原文中的字面连续子串**匹配（区分大小写），不扩展同义词、不做分词、不使用 BM25 Top-K 结果推断全库数量。
2. 同一篇政策的标题与正文可同时命中；同一篇政策命中多次，`matched_documents` 仍只计 **1 篇**。
3. `matched_documents` 是应用全部筛选条件后的**全库精确数量**，与 `page_size` 完全无关。
4. 命中片段 `snippet` 直接来自冻结原文（前后各 60 字符，可能包含原文换行）；`offset` 是该字段字符串内的 Unicode 码点偏移，可用 `字段字符串[offset:offset+len(q)] == q` 复核。
5. 精确检索与 Agent 的 `kb_search`（BM25 相关性检索）是**两套不同能力**：本接口不返回语义相关结果，也不对语义相关性做全库计数。
6. 全部筛选与字面匹配完成后，先对完整匹配集合排序，再分页：标题命中优先于仅正文命中；同档按参考年份降序、行政层级（国家、省、市、区县、未知）、政府工作报告优先、`planid` 升序。没有 `q` 时所有文献处于同一命中档。

### （三）响应

```json
{
  "dataset": {
    "generation_id": "local_generation_e125cd0f…",
    "revision": 1,
    "total_documents": 506,
    "partial": true,
    "source": "current_published_frozen_release"
  },
  "query": {
    "q": "数字经济",
    "area": null,
    "fyp": null,
    "plantype": null,
    "match_mode": "literal"
  },
  "matched_documents": 90,
  "page": 1,
  "page_size": 20,
  "total_pages": 5,
  "items": [
    {
      "planid": "12915",
      "title": "云南省2019年政府工作报告",
      "area": "云南省",
      "fyp": "政府工作报告",
      "plantype_label": "专项规划",
      "year": 2019,
      "matches": [
        {"field": "body", "offset": 735,
         "snippet": "…推动数字经济快速萌芽，落地一批工业和农业精深加工大项目…"}
      ]
    }
  ]
}
```

- 上述示例取自真实当前发布库：`q=数字经济`，且没有 `area/fyp/plantype` 筛选；`matched_documents=90` 是去掉地区条件后的全库精确数量。若加 `area=云南省`，同一请求会变为 `matched_documents=2`（示例政策属于云南省）。其中列表项用于展示真实字段，不代表新排序规则下的首条记录；示例中的真实元数据与数值保持不变。
- 无 `q` 时 `query.match_mode` 为 `null`，`items[].matches` 为空数组。
- 数据缺失时 `title/area/fyp/plantype_label` 为 `null`，不编造。
- `page` 超出 `total_pages` 时返回空 `items`，`matched_documents` 不变。
- `year` 为参考年份；其规则见下文，不代表发布日期。

## 四、单篇政策详情

`GET /api/policies/{planid}`

```json
{
  "dataset": {"generation_id": "…", "revision": 1, "total_documents": 506,
              "partial": true, "source": "current_published_frozen_release"},
  "policy": {
    "planid": "12915",
    "title": "云南省2019年政府工作报告",
    "area": "云南省",
    "fyp": "政府工作报告",
    "plantype_label": "专项规划",
    "year": 2019,
    "body_chars": 17674,
    "sections": [
      {"index": 0, "level": 4, "path": "一、2018年工作回顾", "start": 96, "end": 5297},
      {"index": 1, "level": 4, "path": "二、2019年工作重点", "start": 5297, "end": 17674}
    ]
  }
}
```

- 上述示例取自真实当前发布库（同一篇 `planid=12915`）。
- 列表和详情中的 `year` 使用同一推导规则：标题含单一明确文献年份时使用该年（忽略“2035年远景目标”等远景年份）；标题年份有歧义时不从中择取，继续检查规范规划期。已知第十至第十五个五年规划/计划按起始年映射，分别为 2001、2006、2011、2016、2021、2026；仍无法确定时为 `null`。不从正文提取年份，`year` 不是发布日期。
- 章节 `start/end` 为相对正文起点的 Unicode 码点偏移，`0 <= start < end <= body_chars`。
- 章节列表不包含发布结构中的全文根节点（`kind=root`，路径为空）；只有真实章节会出现在 `sections`。
- 不暴露任何文件系统路径、快照文件名或内部 document_id。

## 五、正文分页读取

`GET /api/policies/{planid}/content?offset=0&limit=4000`

| 参数 | 类型 | 默认 | 上限 / 约束 |
| --- | --- | --- | --- |
| `offset` | int | `0` | ≥0；不得超过 `body_chars`（等于时返回空串） |
| `limit` | int | `4000` | 1–20000 |

```json
{
  "dataset": {"generation_id": "…", "revision": 1, "total_documents": 506,
              "partial": true, "source": "current_published_frozen_release"},
  "planid": "12915",
  "title": "云南省2019年政府工作报告",
  "offset": 0,
  "limit": 4000,
  "total_chars": 17674,
  "has_more": true,
  "text": "2019年1月27日在云南省第十三届人民代表大会第二次会议上…"
}
```

- 上述示例取自真实当前发布库（同一篇 `planid=12915`，`total_chars=17674`）。

- 偏移口径：**正文起点的 Unicode 码点偏移**（不含快照文件的元数据头），与搜索 `matches[].offset` 的 `body` 字段同口径。
- `text` 是冻结原文的连续切片，不做摘要、改写或二次生成。
- 不提供"读取任意文件"能力：`planid` 必须属于当前发布，路径只来自发布清单。

## 六、错误状态

| HTTP | 场景 | 响应体 |
| --- | --- | --- |
| 400 | 业务参数不合法：q 为空/超长、筛选超长、offset 超出正文长度、planid 格式错误 | `{"detail": "具体原因"}` |
| 404 | planid 不在当前发布库 | `{"detail": "未找到该政策"}` |
| 422 | HTTP 层参数错误：类型错误（如 `page=abc`），或 `page/page_size` 超出接口声明范围（如 `page=0`、`page_size=101`） | `{"detail": "请求参数无效"}` |
| 503 | 未配置真实 RAG 运行库 / 发布档案不可读 / 服务尚未就绪 | `{"detail": "政策目录服务不可用…"}` |

错误响应不包含文件系统路径、内部异常或任何文献数据。

## 七、能力边界

1. 只做**字面**检索：不返回语义相关但字面未命中的政策；不做同义词、拼音、分词或模糊匹配。
2. 筛选为元数据**精确相等**；不做地区层级推断、不把"近十年"等时间表达映射到规划期。
3. 目录为进程内快照：服务启动后读取一次当前发布；发布库更新后需重启 Web 服务才能看到新版本（响应中的 `generation_id` 可用于判断）。
4. 性能（真实库 506 篇）：加载约 3 秒、常驻内存约 35–60MB；无关键词的目录请求 <1ms；全库字面检索约 7–12ms。全部查询在内存完成，不初始化 BM25/向量索引、不调用模型。
5. 并发：快照加载完成后只读不可变；CPU 检索在线程池执行，不阻塞 FastAPI 事件循环；SQLite 只在加载阶段使用并在加载结束后关闭，不在请求间共享连接。
6. 本服务不写入、不修改发布库原始文件，也不新增索引。

## 八、前端接入示例

```ts
export type PolicyListResponse = {
  dataset: { generation_id: string; revision: number; total_documents: number;
             partial: boolean | null; source: "current_published_frozen_release" };
  query: { q: string | null; area: string | null; fyp: string | null;
           plantype: string | null; match_mode: "literal" | null };
  matched_documents: number;
  page: number; page_size: number; total_pages: number;
  items: Array<{
    planid: string; title: string | null; area: string | null;
    fyp: string | null; plantype_label: string | null; year: number | null;
    matches: Array<{ field: "title" | "body"; offset: number; snippet: string }>;
  }>;
};

export async function fetchPolicies(params: {
  q?: string; area?: string; fyp?: string; plantype?: string;
  page?: number; page_size?: number;
}, baseUrl = "http://127.0.0.1:8000"): Promise<PolicyListResponse> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value));
  }
  const response = await fetch(`${baseUrl}/api/policies?${query}`);
  if (!response.ok) throw new Error(`文献目录请求失败：${response.status}`);
  return response.json();
}

// 点击政策卡片后读取第一段正文
export async function fetchPolicyContent(planid: string, offset = 0, limit = 4000,
  baseUrl = "http://127.0.0.1:8000") {
  const response = await fetch(
    `${baseUrl}/api/policies/${encodeURIComponent(planid)}/content?offset=${offset}&limit=${limit}`);
  if (!response.ok) throw new Error(`政策正文请求失败：${response.status}`);
  return response.json();
}
```

## 九、测试

- `web/backend/tests/test_policy_catalog.py`：固定数据 + 假冻结发布目录，覆盖目录、去重、字面命中标题/正文、筛选组合、分页稳定性、非法参数、版本隔离、快照哈希校验、片段可定位、无 RAG 配置时 503。
- `web/backend/tests/test_policy_catalog_real.py`：设置 `GUANCE_CATALOG_STORE_PROJECT` 后对真实运行库做有限验收（独立复核 `数字经济` 命中数、片段定位、筛选、正文分页）。
