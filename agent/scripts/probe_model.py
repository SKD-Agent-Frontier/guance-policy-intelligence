"""在可访问学校接口的机器上运行；不会用离线结果替代接口实测。"""
import asyncio
import json
import statistics
from datetime import datetime, timezone
from time import perf_counter

from guance_agent.config import ROOT, load_settings
from guance_agent.runtime import get_client


async def probe():
    s = load_settings()
    report = ROOT / "docs/model_probe.md"
    report.parent.mkdir(exist_ok=True)
    lines = ["# 模型接口实测", "", f"日期：{datetime.now(timezone.utc).isoformat()}",
             f"模型 ID：{s.llm_model}", ""]
    if not s.llm_base_url or not s.llm_api_key:
        lines += ["状态：待联调。当前环境缺少 LLM_BASE_URL / LLM_API_KEY，未发送模型请求。",
                  "", "建议保持 STRUCTURED_OUTPUT=off、LLM_TEMPERATURE 留空，完成实测后再调整。",
                  "鉴权是否校验 Key：待校方确认；本记录不推断接口可免鉴权。"]
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("待联调：请在本机设置模型环境变量，随后重新运行。")
        return 1
    client = get_client(s)
    results, durations = [], []

    async def check(name, prompt, validate, **parameters):
        started = perf_counter()
        try:
            response = await client.chat.completions.create(model=s.llm_model,
                messages=[{"role": "user", "content": prompt}], **parameters)
            message = response.choices[0].message
            valid = bool(validate(message))
            elapsed = round((perf_counter() - started) * 1000)
            results.append((name, "通过" if valid else "未通过", f"{elapsed} ms"))
            return valid, message, elapsed
        except Exception as exc:
            results.append((name, "失败", type(exc).__name__ +
                            (f" / HTTP {exc.status_code}" if hasattr(exc, "status_code") else "")))
            return False, None, None

    try:
        try:
            models = await client.models.list()
            listed = any(m.id == s.llm_model for m in models.data)
            results.append(("模型列表", "通过" if listed else "未通过", "目标模型已列出" if listed else "未找到目标模型"))
        except Exception as exc:
            results.append(("模型列表", "失败", type(exc).__name__))
        await check("普通对话", "只回答你好。", lambda m: bool(m.content and m.content.strip()))
        tools = [{"type": "function", "function": {"name": name, "description": "回显参数",
                  "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                                 "required": ["text"], "additionalProperties": False}}}
                 for name in ("echo_first", "echo_second")]

        def valid_calls(message, minimum):
            calls = message.tool_calls or []
            return len(calls) >= minimum and all(c.function.name in ("echo_first", "echo_second")
                and isinstance(json.loads(c.function.arguments).get("text"), str) for c in calls)

        await check("单个 tool call", "调用 echo_first，text 为你好，不要直接回答。",
                    lambda m: valid_calls(m, 1), tools=tools[:1])
        supported, multiple, _ = await check("一次响应多个 tool calls",
            "请在同一个响应中分别调用 echo_first 和 echo_second，text 都为你好。", lambda m: valid_calls(m, 2), tools=tools)
        if supported:
            results.append(("并行调用数量", "记录", str(len(multiple.tool_calls))))
        schema = {"type": "object", "properties": {"answer": {"type": "string"}},
                  "required": ["answer"], "additionalProperties": False}
        def valid_json(m):
            return isinstance(json.loads(m.content or "").get("answer"), str)
        structured, _, _ = await check("response_format: json_schema", '返回 JSON，answer 为你好。', valid_json,
            response_format={"type": "json_schema", "json_schema": {"name": "probe", "strict": True, "schema": schema}})
        await check("response_format: json_object", '返回 JSON，answer 为你好。', valid_json,
                    response_format={"type": "json_object"})
        temperature, _, _ = await check("temperature=0", "只回答你好。", lambda m: bool(m.content), temperature=0)
        ok, thinking, _ = await check("思考内容", '计算 1+1，只输出 JSON：{"answer":"2"}。', lambda m: bool(m.content))
        if ok:
            content = thinking.content or ""
            try:
                json.loads(content)
                pure = True
            except ValueError:
                pure = False
            results.append(("content 推理混入检查", "记录", f"think 标签={'<think>' in content}；纯 JSON={pure}"))
        for index in range(5):
            ok, _, elapsed = await check(f"延迟样本 {index+1}", "只回答你好。", lambda m: bool(m.content))
            if ok:
                durations.append(elapsed)
        lines += ["| 实测项 | 结果 | 记录 |", "| --- | --- | --- |"]
        lines += [f"| {name} | {status} | {detail} |" for name, status, detail in results]
        lines += ["", f"STRUCTURED_OUTPUT={'on' if structured else 'off'}",
                  f"LLM_TEMPERATURE={'0' if temperature else '留空'}",
                  "配置建议仅写入报告，不自动覆盖本地环境变量。",
                  "本脚本记录 API 单次响应中的工具调用数量；多个工具调用可来自同一轮模型响应。",
                  "鉴权是否校验 Key：未做绕过鉴权测试，需由校方确认。"]
        if durations:
            lines.append(f"成功延迟样本 {len(durations)}/5；P50={statistics.median(durations)} ms；最大值={max(durations)} ms。")
        complete = all(status != "失败" for _, status, _ in results)
        lines.append("实测已执行；请结合各项结果判断兼容性。" if complete else "存在请求失败，未完成全部接口验证。")
        return 0 if complete else 1
    finally:
        text = "\n".join(lines) + "\n"
        for secret in (s.llm_api_key, s.llm_base_url):
            text = text.replace(secret, "[REDACTED]")
        report.write_text(text, encoding="utf-8")
        await client.close()
        print("实测记录已写入 docs/model_probe.md")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
