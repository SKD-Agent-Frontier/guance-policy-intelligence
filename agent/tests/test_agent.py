from dataclasses import replace
from hashlib import sha256

from guance_agent.agent import build_agent, effective_prompt, prompt_version
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


def test_prompt_version_covers_effective_prompt():
    # 两种输出模式下实际下发的提示词不同，版本号必须随之不同
    assert effective_prompt(False) != effective_prompt(True)
    assert prompt_version(False) != prompt_version(True)
    for structured in (False, True):
        head, digest = prompt_version(structured).split("@")
        assert head.startswith("qa:") and len(digest) == 8
    # 不同任务类型必须是不同版本，否则日志里分不清用的是哪套提示词
    assert prompt_version(False, "qa") != prompt_version(False, "extract")
    assert prompt_version(False, "extract").startswith("extract:")


def test_build_agent_uses_effective_prompt():
    for structured in (False, True):
        s = replace(Settings(), structured_output=structured)
        assert build_agent(s, "unused").instructions == effective_prompt(structured)


def test_qa_output_budget_guidance_is_in_effective_prompt():
    for structured in (False, True):
        prompt = effective_prompt(structured)
        assert "既定输出预算" in prompt
        assert "3–5 项最相关且有本轮证据支持的措施" in prompt
        assert "保留其中的条件、单位和适用范围" in prompt
        assert "明确说明已覆盖的范围及未覆盖的部分" in prompt
        assert "不要在生成后删减 claims 或引文" in prompt
        assert "chunk_ids" in prompt and "逐字引文" in prompt
        if structured:
            assert "只输出一个 JSON 对象" not in prompt
        else:
            assert "只输出一个 JSON 对象，不加其他文字" in prompt
        digest = sha256(prompt.encode()).hexdigest()[:8]
        assert prompt_version(structured) == f"qa:{'schema' if structured else 'json'}@{digest}"
