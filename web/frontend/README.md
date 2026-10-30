# 管策智研 Web 前端

管策智研提供统一的政策研究工作区，支持政策问答、要素提取、文献梳理、政策比较和多方案分析。研究模式由用户选择；研究问答与文献梳理通过多轮对话准备请求，范围充分后自动提交正式研究，并在当前对话展示结果和证据。

一、使用方式

1. 在首页选择研究模式，用自然语言描述问题，然后发送。
2. 若系统需要补充条件，会显示后端返回的问题与快捷选项；也可以直接输入回答。已确认的条件和执行边界可展开查看。
3. 准备完成后，前端将后端返回的正式请求提交给研究服务。准备状态本身不代表检索或研究已经完成。
4. 研究结果作为对话中的独立消息显示。可从结果中的引文入口打开证据面板；不同研究会话在侧栏分别保留。
5. “文献”入口打开政策文献目录。关键词使用标题和正文的字面连续匹配，结果数量为应用筛选后的全库精确篇数；字面命中不等于全部语义相关政策。地区、规划期和政策类型使用精确筛选。打开文献可查看元数据、章节目录和原文；正文按 4,000 个 Unicode 字符分页读取。

首页、研究工作区和文献目录共用侧栏。要素提取、政策比较和多方案分析收到自然语言问题后，会提示补充政策对象，自动展开高级选择并保留研究问题，不调用研究准备接口，也不提前执行研究。填写有效政策编号后，通过原有高级入口正式执行；系统不会自动定位比较对象。

高级选择中的问题、编号、维度和展开状态按会话保存，切换首页、工作区、文献目录或其他研究会话后仍可继续填写。“指定政策对象”会展开表单并聚焦首个待填写的编号；也可通过表单中的目录入口查找编号。提取需要一个对象，比较与多方案分析需要 2—8 个不同对象。

研究记录与准备状态保存在当前前端会话内，刷新页面后会清空。当前没有账户认证或云端历史持久化。

二、Real 与 Demo

运行模式通过 `VITE_RESEARCH_MODE` 配置，值为 `real` 或 `demo`。Real 模式只请求真实后端；请求失败时显示错误，不会切换到演示数据。Demo 模式使用明确标记的本地合成数据，不代表真实模型、检索库或研究结果。两个模式的研究接口和目录数据相互隔离。

统一准备与对话流程依赖 PR #9 的 `/api/research/prepare` 接口。PR #9 已合并到 `main`。字段与状态规则见 [`RESEARCH_PREPARATION_CONTRACT.md`](../backend/RESEARCH_PREPARATION_CONTRACT.md)。

政策目录使用 `/api/policies`、`/api/policies/{planid}` 和 `/api/policies/{planid}/content`。接口能力、筛选语义、分页和错误状态见 [`POLICY_CATALOG_API.md`](../backend/POLICY_CATALOG_API.md)。真实目录服务不可用时会显示错误和重试入口，不会显示合成目录记录。

三、本地运行

需要 Node.js 22.12 或更新版本，以及 npm。

（一）安装依赖并启动前端

```bash
npm install
npm run dev
```

Real 模式默认使用开发 API 地址。也可通过 `VITE_API_BASE_URL` 配置后端地址，并通过 `VITE_RESEARCH_MODE=real` 显式启用真实模式。不要把私有端点、凭据或本机配置文件加入仓库。

（二）启动 Demo 模式

```bash
VITE_RESEARCH_MODE=demo npm run dev
```

在 PowerShell 中可以先设置 `$env:VITE_RESEARCH_MODE = "demo"`，再执行 `npm run dev`。切换到 Real 时将变量设为 `real`，并确保后端已运行。

四、检查与验收

```bash
npm run typecheck
npm test
npm run build
npm run test:e2e
npm run test:e2e:mock
```

`test:e2e` 使用 Demo 前端数据。`test:e2e:mock` 使用 Real 客户端并截取 HTTP 响应，覆盖 API 联调逻辑，但不调用真实模型或 RAG。两者都不能替代真实后端验收。

Real 浏览器冒烟需要明确 opt-in，并要求本地后端、模型与已发布的 RAG 目录可用：

```bash
RUN_REAL_E2E=1 VITE_API_BASE_URL=http://127.0.0.1:8000 npm run test:e2e:real
```

Real 冒烟覆盖政策问答、文献梳理和目录搜索/正文阅读，不会拦截或模拟后端请求。真实模型调用可能消耗服务资源；请只在受控本地验收环境执行。可用 `PLAYWRIGHT_BROWSER_CHANNEL=chrome` 指定 Chrome，并用 `PLAYWRIGHT_OUTPUT_DIR` 指定浏览器测试产物目录。

五、主要目录

- `src/components/`：首页、统一研究表单、对话结果、证据面板、侧栏和政策目录。
- `src/api/`：Real 与 Demo 客户端及目录接口适配。
- `src/data/`：本地演示研究数据。
- `e2e/`：不调用真实服务的 Playwright 测试。
- `e2e-real/`：需显式启用的真实后端浏览器冒烟。
