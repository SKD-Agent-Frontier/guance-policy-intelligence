# 管策智研 Web 前后端集成验收记录（PR #7）

## 一、范围

将现有 React 前端接入真实 FastAPI `/api/runs`，保留显式演示模式。
不重新设计界面，不修改 `agent/`、`rag库/`、`web/backend/` 的业务逻辑。

## 二、改动清单

| 文件 | 说明 |
|---|---|
| `web/frontend/src/api/config.ts` | 新增。读取 `VITE_RESEARCH_MODE` / `VITE_API_BASE_URL`；模式值非法直接抛错，不静默回退 |
| `web/frontend/src/api/realClient.ts` | 新增。`POST /api/runs`，携带 `session_id`；超时 210 秒；错误分类 network / timeout / http / malformed |
| `web/frontend/src/api/demoClient.ts` | 新增。原有演示逻辑原样迁出 |
| `web/frontend/src/api/client.ts` | 改为按模式二选一，**不做自动回退** |
| `web/frontend/src/types.ts` | `ResearchRequest` 补 `session_id` |
| `web/frontend/src/vite-env.d.ts` | 新增。启用 `import.meta.env` 类型 |
| `web/frontend/src/App.tsx` | 状态文案按模式切换；Real 模式隐藏「演示数据」徽标与演示状态选择器 |
| `web/frontend/src/api/realClient.test.ts` | 新增 5 条 |
| `web/frontend/src/api/client.test.ts` | 改为直接测 `demoClient` |
| `web/frontend/.env.example` | 新增 |

## 三、构建与测试

```
npm run typecheck   ✅ 通过
npm run test        ✅ 4 文件 / 16 测试全过
npm run build       ✅ 构建成功
```

## 四、错误处理

| 场景 | 表现 |
|---|---|
| 后端未启动 / 网络中断 | 无法连接研究服务，请确认后端已启动。 |
| 前端超时 | 请求超时。Agent 可能仍在后台执行，请稍后重试。 |
| HTTP 422 | 请求参数无效，请检查研究问题与政策编号。 |
| HTTP 503 | 服务尚未就绪，后端仍在加载模型或知识库，请稍后重试。 |
| HTTP 500 | 服务内部错误，请稍后重试。 |
| 响应非 JSON | 服务返回内容无法解析。 |

优先使用后端返回的 `detail`，缺失时回落到上述文案。不展示 traceback，不泄露密钥与路径。

## 五、五项功能真实 HTTP 调用

后端：`uvicorn app.main:app --port 8000`，加载真实 `.env`（BM25 运行库 + 学校模型）。
健康检查 `/api/health` 约 36 秒后就绪。

| task_type | HTTP | Agent status | evidence_status | claims | evidence_refs | structured | 耗时 |
|---|---:|---|---|---:|---:|---|---:|
| `qa` | 200 | completed | verified | 6 | 2 | null（符合预期） | 23.9s |
| `extract` | 200 | completed | insufficient | 0 | 0 | 4 键 | 17.5s |
| `organize` | 200 | failed | none | 0 | 0 | 空 | 53.5s |
| `compare` | 200 | completed | verified | 4 | 2 | 6 键（含 matrix） | 14.4s |
| `compare_plans` | 200 | completed | insufficient | 4 | 4 | 6 键 | 28.2s |

**Web 接入 5/5 通过**：请求体、`targets`、`dimensions` 正确送达，返回的 `RunResult` 可被解析，`structured` 键名与前端类型一致。

**业务层 4/5 成功**，两个失败点归属 Agent 执行层，不在本轮修改范围：

1. `organize` 返回 `OUTPUT_PARSE` —— 模型输出无法解析为约定结构。
2. `extract` 返回 0 条要素 —— 数字核验未排除用户输入的政策编号，导致结论被整体否决。

`compare_plans` 的 `insufficient` 是 fail-closed 的正常结果，不是缺陷。

## 六、本地启动

```bash
# 后端
cd web/backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（真实模式）
cd web/frontend
VITE_RESEARCH_MODE=real npm run dev
```

后端通过 `GUANCE_ENV_FILE` 指定外部 `.env`，仓库内不存放任何密钥。

## 七、未完成项

| 项 | 原因 |
|---|---|
| 浏览器端真实提交（QA / 政策比较）与引用面板检查 | 待补 |
| `organize` 输出解析失败 | Agent 侧问题，超出本轮范围 |
| `extract` 数字核验误否决 | 同上 |
| 研究历史 `session_id` 端到端续接 | 待真实联调确认 |
| 比较 / 多方案 `structured` 的边界样例 | 类型已兼容，缺真实异常样本 |