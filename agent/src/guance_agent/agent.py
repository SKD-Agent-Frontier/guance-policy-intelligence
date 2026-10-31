"""单 Agent 配置和版本化通用提示词。"""
from hashlib import sha256
from pathlib import Path

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel

from .client import thinking_off
from .config import Settings
from .context import RunContext
from .schemas import AgentOutput
from .tools import kb_read, kb_search

PROMPT_DIR = Path(__file__).with_name("prompts")
PROMPT_FILES = {"qa": "system_v1.md", "extract": "extract_v1.md"}
PROMPT_FILE = PROMPT_DIR / "system_v1.md"

def effective_prompt(structured: bool, task_type: str = "qa") -> str:
    """返回真正下发给模型的功能提示词；结构化模式下不追加 JSON 约束。"""
    prompt = (PROMPT_DIR / PROMPT_FILES.get(task_type, PROMPT_FILES["qa"])).read_text(encoding="utf-8")
    if not structured:
        prompt += '\n只输出一个 JSON 对象，不加其他文字。结构示例：' + AgentOutput(
            answer="回答正文", claims=[], insufficient=False).model_dump_json()
        prompt += '\n政策结论 claims 的每项格式：{"text":"结论","chunk_ids":["本轮证据ID"],"quotes":["逐字引文"]}'
        prompt += '\n可选 analyses 的每项格式：{"kind":"synthesis","text":"归纳判断","scope_note":"仅适用于已读政策条款","supporting_claim_indexes":[0,1]}。仅用于确实要求分析的任务。'
    return prompt


def prompt_version(structured: bool = False, task_type: str = "qa") -> str:
    """对实际生效的提示词取哈希；任务类型或输出模式不同，版本号必然不同。"""
    return f"{task_type}:{'schema' if structured else 'json'}@" + sha256(
        effective_prompt(structured, task_type).encode()).hexdigest()[:8]

def chat_model(s: Settings, client):
    return OpenAIChatCompletionsModel(model=s.llm_model, openai_client=client)

def build_agent(s: Settings, model, task_type: str = "qa") -> Agent[RunContext]:
    prompt = effective_prompt(s.structured_output, task_type)
    parameters = {"max_tokens": s.llm_max_output_tokens, "extra_body": thinking_off(s)}
    if s.llm_temperature is not None:
        parameters["temperature"] = s.llm_temperature
    return Agent(name="管策智研", instructions=prompt, model=model,
                 tools=[kb_search, kb_read], model_settings=ModelSettings(**parameters),
                 output_type=AgentOutput if s.structured_output else None)
