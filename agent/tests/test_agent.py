from dataclasses import replace

from guance_agent.agent import build_agent, prompt_version
from guance_agent.config import Settings
from guance_agent.schemas import AgentOutput


def test_agent_modes_tools_and_version():
    s = Settings()
    agent = build_agent(s, "unused-in-offline-configuration-test")
    assert agent.name == "管策智研"
    assert [t.name for t in agent.tools] == ["kb_search", "kb_read"]
    assert agent.output_type is None and "JSON 对象" in agent.instructions
    assert agent.model_settings.temperature is None
    assert prompt_version() == prompt_version() and len(prompt_version().split("@")[1]) == 8
    structured = build_agent(replace(s, structured_output=True, llm_temperature=0), agent.model)
    assert structured.output_type is AgentOutput
    assert structured.model_settings.temperature == 0
    assert not structured.handoffs and not structured.input_guardrails
