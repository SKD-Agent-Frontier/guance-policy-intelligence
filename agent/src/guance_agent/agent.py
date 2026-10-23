"""单 Agent 配置和版本化通用提示词。"""
from hashlib import sha256
from pathlib import Path

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel

from .config import Settings
from .context import RunContext
from .schemas import AgentOutput
from .tools import kb_read, kb_search

PROMPT_FILE = Path(__file__).with_name("prompts") / "system_v1.md"


def prompt_version() -> str:
    return "system_v1@" + sha256(PROMPT_FILE.read_bytes()).hexdigest()[:8]


def chat_model(s: Settings, client):
    return OpenAIChatCompletionsModel(model=s.llm_model, openai_client=client)


def build_agent(s: Settings, model) -> Agent[RunContext]:
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    if not s.structured_output:
        prompt += '\n只输出一个 JSON 对象，不加其他文字。结构示例：' + AgentOutput(
            answer="回答正文", claims=[], insufficient=False).model_dump_json()
        prompt += '\n政策结论 claims 的每项格式：{"text":"结论","chunk_ids":["本轮证据ID"],"quotes":["逐字引文"]}'
    parameters = {} if s.llm_temperature is None else {"temperature": s.llm_temperature}
    return Agent(name="管策智研", instructions=prompt, model=model,
                 tools=[kb_search, kb_read], model_settings=ModelSettings(**parameters),
                 output_type=AgentOutput if s.structured_output else None)
