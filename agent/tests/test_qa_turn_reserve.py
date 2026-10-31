import json
from dataclasses import replace

from agents import Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai import APITimeoutError
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from guance_agent.agent import build_agent
from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.runner import execute
from guance_agent.runtime import Runtime


class ToolAwareModel(Model):
    def __init__(self, multiple_searches=False):
        self.calls = 0
        self.multiple_searches = multiple_searches
        self.visible_tools = []
        self.instructions = []

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema,
                           handoffs, tracing, **kwargs):
        names = {getattr(item, "name", None) for item in tools}
        self.visible_tools.append(names)
        self.instructions.append(system_instructions)
        if self.calls == 0 and self.multiple_searches and "kb_search" in names:
            output = [
                ResponseFunctionToolCall(
                    type="function_call", name="kb_search", call_id="search_a",
                    arguments=json.dumps(
                        {"query": "农业", "area": "甲", "fyp": "十四五", "top_k": 8}),
                ),
                ResponseFunctionToolCall(
                    type="function_call", name="kb_search", call_id="search_b",
                    arguments=json.dumps(
                        {"query": "农业发展", "area": "甲", "fyp": "十四五", "top_k": 8}),
                ),
            ]
        elif self.calls == 0 and "kb_search" in names:
            output = [ResponseFunctionToolCall(
                type="function_call", name="kb_search", call_id="search",
                arguments=json.dumps({"query": "农业", "area": "甲", "fyp": "十四五", "top_k": 8}),
            )]
        elif self.calls == 1 and "kb_read" in names:
            output = [ResponseFunctionToolCall(
                type="function_call", name="kb_read", call_id="read",
                arguments=json.dumps({"planid": "TEST-001", "section_idx": 0}),
            )]
        else:
            content = json.dumps({"answer": "证据不足", "claims": [], "insufficient": True})
            output = [ResponseOutputMessage(
                id=f"message_{self.calls}", type="message", role="assistant", status="completed",
                content=[ResponseOutputText(type="output_text", text=content, annotations=[])],
            )]
        self.calls += 1
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=f"response_{self.calls}")

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover


async def execute_task(settings, task, model, agent=None):
    ctx = RunContext(settings, get_kb(settings), task.run_id, task.session_id)
    rt = Runtime(settings, task)
    rt.received()
    rt.prepare()
    try:
        return await execute(agent or build_agent(settings, model), task, ctx, rt), ctx, rt
    finally:
        rt.close()


async def test_qa_keeps_multistep_retrieval_and_reserves_last_turn(env):
    settings, task = env
    settings = replace(settings, max_turns=3)
    model = ToolAwareModel()
    outcome, ctx, rt = await execute_task(settings, task, model)

    assert outcome.error_code is None
    assert model.calls == rt.hooks.turns == 3
    assert "kb_search" in model.visible_tools[0]
    assert "kb_read" in model.visible_tools[1]
    assert model.visible_tools[2] == set()
    assert len(ctx.tool_calls) == 2 and ctx.evidence.ids()
    assert rt.meta["qa_final_turn_reserved"] is True
    assert "证据不足" in model.instructions[2]
    assert "JSON 格式" in model.instructions[2]


async def test_two_tool_calls_in_penultimate_turn_dispatch_before_final_turn(env):
    settings, task = env
    settings = replace(settings, max_turns=2)
    model = ToolAwareModel(multiple_searches=True)
    outcome, ctx, rt = await execute_task(settings, task, model)

    assert outcome.error_code is None
    assert model.calls == rt.hooks.turns == 2
    assert {"kb_search", "kb_read"} <= model.visible_tools[0]
    assert model.visible_tools[1] == set()
    assert [call["name"] for call in ctx.tool_calls] == ["kb_search", "kb_search"]
    assert ctx.evidence.ids()
    assert rt.meta["qa_final_turn_reserved"] is True


async def test_reservation_counter_is_local_to_each_execute(env):
    settings, task = env
    settings = replace(settings, max_turns=3)
    model = ToolAwareModel()
    shared_agent = build_agent(settings, model)
    original_tools = list(shared_agent.tools)

    for run_number in (1, 2):
        if run_number == 2:
            model.calls = 0
            model.visible_tools.clear()
            model.instructions.clear()
        run_task = task.model_copy(update={"run_id": f"run_{run_number}",
                                           "session_id": f"session_{run_number}"})
        ctx = RunContext(settings, get_kb(settings), run_task.run_id, run_task.session_id)
        rt = Runtime(settings, run_task)
        rt.received()
        rt.prepare()
        try:
            result = await execute(shared_agent, run_task, ctx, rt)
        finally:
            rt.close()
        assert result.error_code is None and model.calls == 3
        assert model.visible_tools[2] == set()
        assert rt.meta["qa_final_turn_reserved"] is True

    assert shared_agent.tools == original_tools
    assert all(tool.is_enabled is True for tool in original_tools)


async def test_existing_async_tool_enable_predicate_is_preserved(env):
    settings, task = env
    settings = replace(settings, max_turns=3)
    model = ToolAwareModel()
    shared_agent = build_agent(settings, model)
    predicate_calls = []

    async def disabled_by_original_policy(run_context, tool_agent):
        predicate_calls.append(tool_agent)
        return False

    shared_agent.tools[0] = replace(shared_agent.tools[0], is_enabled=disabled_by_original_policy)
    outcome, _, rt = await execute_task(settings, task, model, agent=shared_agent)

    assert outcome.error_code is None and model.calls == 1
    assert predicate_calls
    assert "kb_search" not in model.visible_tools[0]
    assert "qa_final_turn_reserved" not in rt.meta


async def test_max_turns_one_and_non_qa_keep_original_tool_visibility(env):
    settings, task = env
    one_turn = replace(settings, max_turns=1)
    model = ToolAwareModel()
    outcome, _, rt = await execute_task(one_turn, task, model)
    assert outcome.error_code == "MAX_TURNS" and model.calls == 1
    assert "kb_search" in model.visible_tools[0]
    assert "qa_final_turn_reserved" not in rt.meta

    model = ToolAwareModel()
    extract_task = task.model_copy(update={"task_type": "extract"})
    outcome, _, rt = await execute_task(replace(settings, max_turns=3), extract_task, model)
    assert outcome.error_code is None and model.calls == 3
    assert all({"kb_search", "kb_read"} <= names for names in model.visible_tools)
    assert "qa_final_turn_reserved" not in rt.meta


async def test_single_model_request_timeout_has_its_own_error_code(env):
    from openai._exceptions import httpx2

    error = APITimeoutError(request=httpx2.Request("POST", "https://example.invalid"))
    outcome, _, _ = await execute_task(env[0], env[1], _ErrorModel(error))
    assert outcome.error_code == "MODEL_TIMEOUT"


class _ErrorModel(Model):
    def __init__(self, error):
        self.error = error

    async def get_response(self, *args, **kwargs):
        raise self.error

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover
