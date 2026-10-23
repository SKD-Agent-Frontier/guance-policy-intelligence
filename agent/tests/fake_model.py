"""脚本化假模型，仅用于离线测试，不代表真实模型能力。"""
import asyncio
import copy
import json

from agents import Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)


def tool(name, **arguments):
    return ResponseFunctionToolCall(type="function_call", name=name,
                                    call_id="call_" + name, arguments=json.dumps(arguments))


def answer(text="你好", claims=None, insufficient=False):
    return json.dumps(dict(answer=text, claims=claims or [], insufficient=insufficient), ensure_ascii=False)


class FakeModel(Model):
    def __init__(self, script, delay=0):
        self.script, self.delay, self.inputs, self.calls = script, delay, [], 0

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema,
                           handoffs, tracing, **kwargs):
        self.inputs.append(copy.deepcopy(input))
        self.calls += 1
        await asyncio.sleep(self.delay)
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        if isinstance(step, str):
            output = [ResponseOutputMessage(id=f"msg_{self.calls}", type="message", role="assistant",
                        status="completed", content=[ResponseOutputText(type="output_text", text=step, annotations=[])])]
        else:
            output = [item.model_copy(update={"call_id": f"call_{self.calls}_{i}"})
                      for i, item in enumerate(step)]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=f"fake_{self.calls}")

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError("offline fake does not stream")
        yield  # pragma: no cover
