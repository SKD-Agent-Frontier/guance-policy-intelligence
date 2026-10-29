# Web 前后端集成验收记录（PR #7）

## 一、本轮完成范围

在前端接入真实 FastAPI `/api/runs`，同时保留显式演示模式。

## 二、改动清单

| 文件 | 说明 |
|---|---|
| `web/frontend/src/api/config.ts` | 新增。读取 `VITE_RESEARCH_MODE` / `VITE_API_BASE_URL`；非法模式直接抛错，不静默回退 |
| `web/frontend/src/api/realClient.ts` | 新增。`POST /api/runs`，携带 `session_id`；超时 210 秒；错误分类 network / timeout / http / malformed |
| `web/frontend/src/api/demoClient.ts` | 新增。原有演示逻辑原样迁出 |
| `web/frontend/src/api/client.ts` | 改为按模式二选一，**不做自动回退** |
| `web/frontend/src/types.ts` | `ResearchRequest` 补 `session_id` |
| `web/frontend/src/vite-env.d.ts` | 新增。启用 `import.meta.env` 类型 |
| `web/frontend/src/App.tsx` | 演示文案按模式切换；Real 模式隐藏「演示数据」徽标 |
| `web/frontend/src/api/realClient.test.ts` | 新增 5 条：请求体契约、session_id、422/503/500 映射、网络失败、非 JSON |
| `web/frontend/src/api/client.test.ts` | 改为直接测 `demoClient`（这些用例本来就是演示行为） |
| `web/frontend/.env.example` | 新增 |

## 三、验证结果

```
npm run typecheck   ✅ 通过
npm run test        ✅ 4 文件 / 16 测试全过
npm run build       ✅ 1589 modules，dist 产物正常
```

## 四、错误处理覆盖

| 场景 | 表现 |
|---|---|
| 后端未启动 / 网络中断 | 「无法连接研究服务，请确认后端已启动。」 |
| 前端超时 | 「请求超时。Agent 可能仍在后台执行，请稍后重试。」 |
| HTTP 422 | 「请求参数无效，请检查研究问题与政策编号。」 |
| HTTP 503 | 「服务尚未就绪，后端仍在加载模型或知识库，请稍后重试。」 |
| HTTP 500 | 「服务内部错误，请稍后重试。」 |
| 响应非 JSON | 「服务返回内容无法解析。」 |

优先使用后端返回的中文 `detail`，缺失时回落到上述文案。不展示 traceback，不泄露密钥与路径。

## 五、真实端到端验收状态

**未完成 —— 外部环境阻塞。**

当前执行环境不在校园网内，`10.27.66.12:80/443` 均 TCP 超时，无法调用学校模型与真实 RAG。

因此本轮：

- ✅ 完成前端构建、TypeScript、单元测试（使用模拟 HTTP 响应）
- ❌ **未执行**五项功能的真实模型联调
- ❌ **未执行**浏览器端 `/api/runs` 真实请求

**未把演示结果当作真实测试通过。**

## 六、恢复校园网后的验收步骤

```bash
# 1. 后端（使用真实 .env）
cd web/backend
GUANCE_ENV_FILE=<原 .env 绝对路径> python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2. 前端
cd web/frontend
VITE_RESEARCH_MODE=real npm run dev
```

逐项记录：HTTP 状态、task_type、Agent status、evidence_status、耗时、claims 数、evidence_refs 数、structured 是否正确展示。

## 七、未完成 / 待办

| 项 | 原因 |
|---|---|
| 五项功能真实模型联调 | 校园网不可达 |
| 浏览器端截图 | 同上 |
| 研究历史中 `session_id` 的端到端续接 | 待真实联调确认后端会话行为 |
| 比较/多方案的 `structured` 降级展示细化 | 现有类型已兼容，但缺少真实样本验证 |