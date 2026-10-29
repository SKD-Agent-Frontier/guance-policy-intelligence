# 管策智研 Web 前端

## 一、项目说明

管策智研是基于政策文献开展问答、要素提取、文献梳理、政策比较和多方案分析的研究工作台。当前版本是可交互的前端演示，结果由本地演示数据生成，尚未接入真实模型、检索系统或后端 API。

### （一）视觉与字体

主色 `#B22924` 根据用户提供的 A3 色值设置；标题与正文按 A2 字体类别配置本地字体栈，缺少字体时由浏览器回退，不包含商业字体文件。首页建筑线描为原创抽象示意，不代表真实校园。

## 二、本地运行

需要 Node.js 22.12 或更新版本，以及 npm。

### （一）安装依赖

```bash
npm install
```

### （二）启动开发页面

```bash
npm run dev
```

按终端显示的本地地址在浏览器中打开页面。

### （三）构建与检查

```bash
npm run typecheck
npm test
npm run build
```

浏览器交互检查使用 Playwright：

```bash
npm run test:e2e
```

默认使用 Playwright 自带的 Chromium。也可通过 `PLAYWRIGHT_BROWSER_CHANNEL=chrome` 指定本机 Chrome；截图和测试产物目录分别可由 `PLAYWRIGHT_ARTIFACT_DIR`、`PLAYWRIGHT_OUTPUT_DIR` 指定。

## 三、研究模式与演示状态

首页提供政策问答、要素提取、文献梳理、政策比较和多方案分析五种研究模式。表单草稿会在模式切换时保留。比较任务要求填写 2—8 个不同的政策编号；示例编号仅适用于演示数据。

可在「演示设置」中选择正常完成、证据不足、无可用证据、执行失败或达到执行限制。研究记录只保存在当前页面会话中。演示结果会明确标记为演示数据，不代表真实检索或模型调用。

## 四、接入真实 API

页面通过 `src/api/client.ts` 中的 `researchClient.submit(request, scenario)` 统一提交研究请求。连接真实服务时，可将该客户端实现替换为 HTTP 请求，并将响应映射到 `src/types.ts` 中的 `RunResult`。当前 `scenario` 参数只用于演示状态选择，接入真实 API 时应从调用参数中移除。

## 五、主要目录

- `src/components/`：首页研究表单、结果和引用证据面板。
- `src/data/`：与前后端契约对应的本地演示数据。
- `src/api/`：统一研究客户端接口。
- `e2e/`：浏览器端交互检查。
