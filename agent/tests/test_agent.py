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
        assert "研究目标和需要回答的维度" in prompt
        assert "围绕尚缺的维度选择检索词" in prompt
        assert "事实、分析与出处" in prompt
        assert "supporting_claim_indexes" in prompt
        assert "研究型问题必须提供 `report`" in prompt
        assert "综合分析至少依赖两条相关事实 Claim" in prompt
        assert "不要把分析伪装成原文事实" in prompt
        assert "不得对所有多维问题统一设定" in prompt
        assert "`insufficient` 设为 `true`" in prompt
        assert "必须与本轮工具返回的元数据完全一致" in prompt
        assert "在 `report.dimensions` 保留每个研究维度" in prompt
        assert "不要在生成后删除 Claim、引文或依赖" in prompt
        assert "chunk_ids" in prompt and "逐字片段" in prompt
        if structured:
            assert "只输出一个 JSON 对象" not in prompt
        else:
            assert "只输出一个 JSON 对象，不加其他文字" in prompt
            assert '"supporting_claim_indexes":[0]' in prompt
            assert '"kind":"fact"' in prompt and "kind 只能是 fact 或 analysis" in prompt
        digest = sha256(prompt.encode()).hexdigest()[:8]
        assert prompt_version(structured) == f"qa:{'schema' if structured else 'json'}@{digest}"
