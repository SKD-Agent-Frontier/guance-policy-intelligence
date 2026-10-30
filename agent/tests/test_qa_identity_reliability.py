"""QA 政策身份编号与政策事实分离的回归测试（PR #9 任务一）。

纯单元测试：用固定元数据与固定原文模拟本轮真实检索返回，不依赖 RAG 或学校模型。
"""
from types import SimpleNamespace

import pytest

from guance_agent.schemas import Claim
from guance_agent.verify import verify_numbers

SOURCE = "推动养老服务人才队伍建设，完善培养、引进和激励措施。"


def _ref(planid, chunk_id, *, title="苏州市十四五民政事业发展规划", area="苏州市",
         fyp="第十四个五年规划", section_path="第四部分 主要任务"):
    return SimpleNamespace(
        chunk_id=chunk_id, planid=planid, title=title, area=area, fyp=fyp,
        section_path=section_path, document_id=f"doc-{planid}", generation_id="gen-1",
        observed_ranges=[(0, len(SOURCE))],
    )


def _case(*, answer, claims, refs, sources, question="提供政策依据", scope=()):
    by_chunk = {ref.chunk_id: ref for ref in refs}

    def get_source(chunk_id, *, document_id=None, generation_id=None):
        return SimpleNamespace(content=sources[chunk_id], chunk_char_start=0)

    ctx = SimpleNamespace(
        question=question, scope=tuple(scope),
        route=SimpleNamespace(planids=()),
        evidence=by_chunk,
        kb=SimpleNamespace(get_chunk_source=get_source),
        settings=SimpleNamespace(tool_timeout=5),
    )
    result = SimpleNamespace(claims=list(claims), answer=answer, evidence_refs=list(refs),
                             meta={}, warnings=[], evidence_status="verified")
    return result, ctx


@pytest.fixture(autouse=True)
def record_rebuild(monkeypatch):
    """记录答案重建被触发，避免依赖展示格式。"""
    calls = []
    monkeypatch.setattr("guance_agent.verify.rebuild_trusted_answer",
                        lambda result, ctx, reason: calls.append(reason))
    return calls


@pytest.mark.asyncio
async def test_listing_real_sources_uses_metadata_identity(record_rebuild):
    """QA 列举本轮真实检索文献：编号/篇名/地区/规划期来自元数据，不算未绑定数字。"""
    refs = [
        _ref("23132", "chunk-a", title="池州市十四五数字经济发展规划", area="池州市"),
        _ref("33137", "chunk-b", title="安徽省十四五数字政府建设规划", area="安徽省"),
        _ref("34891", "chunk-c", title="安庆市十四五数字经济发展规划", area="安庆市"),
    ]
    claims = [
        Claim(text="池州市数字经济相关重点是培育市场主体。", chunk_ids=["chunk-a"]),
        Claim(text="安徽省数字政府建设提出五位一体模式。", chunk_ids=["chunk-b"]),
    ]
    answer = ("本轮实际检索到的数字经济相关政策如下：\n"
              "1. 《池州市十四五数字经济发展规划》（planid=23132，池州市，第十四个五年规划）\n"
              "2. 《安徽省十四五数字政府建设规划》（planid=33137，安徽省，第十四个五年规划）\n"
              "3. 《安庆市十四五数字经济发展规划》（planid=34891，安庆市，第十四个五年规划）")
    result, ctx = _case(answer=answer, claims=claims, refs=refs,
                        sources={"chunk-a": SOURCE, "chunk-b": SOURCE, "chunk-c": SOURCE})
    problems = await verify_numbers(result, ctx)
    assert problems == 0, result.warnings
    assert len(result.claims) == 2
    assert record_rebuild == []


@pytest.mark.asyncio
async def test_planid_absent_from_body_is_identity_not_fact(record_rebuild):
    """政策编号不要求出现在引文正文里：只要与来源元数据一致就不是事实数字。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="《苏州市十四五民政事业发展规划》（planid=23132）提出人才队伍建设。",
                    chunk_ids=["chunk-a"])]
    result, ctx = _case(answer="23132 号政策提出人才队伍建设。", claims=claims, refs=refs,
                        sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) == 0, result.warnings
    assert len(result.claims) == 1
    assert record_rebuild == []


@pytest.mark.asyncio
async def test_fabricated_planid_is_still_rejected(record_rebuild):
    """模型编造不存在的政策编号：元数据里没有，仍必须拒绝。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="《苏州市十四五民政事业发展规划》（planid=99999）提出人才队伍建设。",
                    chunk_ids=["chunk-a"])]
    result, ctx = _case(answer="99999 号政策提出人才队伍建设。", claims=claims, refs=refs,
                        sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []
    assert result.evidence_status == "insufficient"


@pytest.mark.asyncio
async def test_fact_value_equal_to_planid_is_not_whitelisted(record_rebuild):
    """数字事实即使恰好等于某个 planid，也不能因身份白名单被当作可信。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="提供23132元补贴。", chunk_ids=["chunk-a"])]
    result, ctx = _case(answer="提供23132元补贴。", claims=claims, refs=refs,
                        sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []


@pytest.mark.asyncio
async def test_inline_enumeration_markers_are_not_facts(record_rebuild):
    """真实模型常把列表写成「：1）…；2）…」，句中标点后的序号也不能算数字事实。"""
    refs = [
        _ref("23132", "chunk-a", title="海西州十四五数字经济发展规划", area="海西州"),
        _ref("33137", "chunk-b", title="池州市十四五数字东至建设规划", area="东至县"),
        _ref("18563", "chunk-c", title="江西省十四五规划纲要", area="江西省"),
    ]
    answer = ("本轮检索到以下政策：1）《海西州十四五数字经济发展规划》"
              "（planid为23132）；2）《池州市十四五数字东至建设规划》（planid为33137）；"
              "3）《江西省十四五规划纲要》（planid为18563）。")
    claims = [Claim(text="检索到《江西省十四五规划纲要》，涉及数字经济。",
                    chunk_ids=["chunk-c"])]
    result, ctx = _case(answer=answer, claims=claims, refs=refs,
                        sources={"chunk-a": SOURCE, "chunk-b": SOURCE, "chunk-c": SOURCE})
    assert await verify_numbers(result, ctx) == 0, result.warnings
    assert len(result.claims) == 1


@pytest.mark.asyncio
async def test_list_markers_do_not_trigger_fact_verification(record_rebuild):
    """有序列表序号不是政策数字事实。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="提出人才队伍建设。", chunk_ids=["chunk-a"])]
    answer = "1. 提出人才队伍建设。\n2. 完善保障措施。\n3. 加强组织实施。"
    result, ctx = _case(answer=answer, claims=claims, refs=refs, sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) == 0, result.warnings
    assert len(result.claims) == 1


@pytest.mark.asyncio
async def test_single_stray_auxiliary_number_keeps_valid_claims(record_rebuild):
    """单个未经绑定的辅助数字只影响答案展示，不得无差别删除全部有效 Claim。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="提出人才队伍建设。", chunk_ids=["chunk-a"])]
    answer = "本轮共覆盖 7 个检索词；提出人才队伍建设。"
    result, ctx = _case(answer=answer, claims=claims, refs=refs, sources={"chunk-a": SOURCE})
    problems = await verify_numbers(result, ctx)
    assert problems > 0
    assert len(result.claims) == 1
    assert result.evidence_status == "insufficient"
    assert record_rebuild and record_rebuild[-1] == "number_verification"


@pytest.mark.asyncio
async def test_metadata_identity_does_not_hide_unrelated_fact(record_rebuild):
    """身份字段被排除后，答案里其它无依据数字仍要触发降级。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="提出人才队伍建设。", chunk_ids=["chunk-a"])]
    answer = "23132 号政策提出人才队伍建设，并投入 8 亿元。"
    result, ctx = _case(answer=answer, claims=claims, refs=refs, sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert len(result.claims) == 1
    assert record_rebuild


@pytest.mark.asyncio
async def test_user_scope_identity_still_allowed(record_rebuild):
    """用户 targets 指定的编号属于输入身份，不要求出现在正文中。"""
    claims = [Claim(text="提出人才队伍建设。", chunk_ids=["chunk-x"])]
    ctx = SimpleNamespace(
        question="提取人才措施", scope=("20699", "37982"),
        route=SimpleNamespace(planids=()),
        evidence={"chunk-x": SimpleNamespace(
            chunk_id="chunk-x", planid="20699", title="苏州市规划", area="苏州市",
            fyp="第十四个五年规划", section_path="第一章", document_id="doc",
            generation_id="gen-1", observed_ranges=[(0, len(SOURCE))])},
        kb=SimpleNamespace(get_chunk_source=lambda chunk_id, **kwargs: SimpleNamespace(
            content=SOURCE, chunk_char_start=0)),
        settings=SimpleNamespace(tool_timeout=5),
    )
    result = SimpleNamespace(claims=claims, answer="20699、37982 号政策的措施如下。",
                             evidence_refs=[], meta={}, warnings=[], evidence_status="verified")
    assert await verify_numbers(result, ctx) == 0
    assert len(result.claims) == 1


@pytest.mark.asyncio
async def test_identity_marker_forms_are_not_facts(record_rebuild):
    """常见身份写法（编号为 / planid为 / N号政策）都不进入事实数字核验。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="编号为23132的政策提出人才队伍建设。", chunk_ids=["chunk-a"])]
    result, ctx = _case(answer="planid为23132的政策提出人才队伍建设。",
                       claims=claims, refs=refs, sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) == 0, result.warnings
    assert len(result.claims) == 1


@pytest.mark.asyncio
async def test_unknown_identity_marker_variant_rejected(record_rebuild):
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="编号为99999的政策提出人才队伍建设。", chunk_ids=["chunk-a"])]
    result, ctx = _case(answer="编号为99999的政策提出人才队伍建设。",
                       claims=claims, refs=refs, sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []


@pytest.mark.asyncio
async def test_measure_context_number_equal_to_planid_is_fact(record_rebuild):
    """带计量动词但没有单位的数字仍是事实：不得因为等于 planid 被身份核验放行。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="补贴标准23132。", chunk_ids=["chunk-a"])]
    result, ctx = _case(answer="补贴标准23132。", claims=claims,
                       refs=refs, sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unit_text", ["年处理能力23132吨", "惠及23132户居民", "覆盖23132家"]
                         )
async def test_fact_number_with_other_units_is_not_whitelisted(record_rebuild, unit_text):
    """事实数字等于 planid、单位不在预置清单里时也必须拒绝，不能靠身份掩码放行。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text=unit_text + "。", chunk_ids=["chunk-a"])]
    result, ctx = _case(answer=unit_text + "。", claims=claims,
                       refs=refs, sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []


@pytest.mark.asyncio
async def test_answer_fabricated_tonnage_keeps_claims_but_downgrades(record_rebuild):
    """答案里编造的“23132 吨”不能被身份掩码吞掉：降级并重建答案，Claim 保留。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="提出人才队伍建设。", chunk_ids=["chunk-a"])]
    answer = "该政策年处理能力23132吨，推动人才队伍建设。"
    result, ctx = _case(answer=answer, claims=claims, refs=refs,
                       sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert len(result.claims) == 1
    assert result.evidence_status == "insufficient"
    assert record_rebuild


@pytest.mark.asyncio
async def test_claim_may_cite_another_real_source_identity(record_rebuild):
    """结论提到本轮另一篇真实来源的编号/篇名时，身份核对通过（元数据全局核对）。"""
    first = _ref("23132", "chunk-a", title="甲市规划", area="甲市")
    second = _ref("222", "chunk-b", title="乙市规划", area="乙市")
    claims = [Claim(text="《乙市规划》（planid=222）与《甲市规划》方向一致。",
                    chunk_ids=["chunk-a"])]
    result, ctx = _case(answer="《乙市规划》（planid=222）与《甲市规划》方向一致。",
                       claims=claims, refs=[first, second],
                       sources={"chunk-a": SOURCE, "chunk-b": SOURCE})
    assert await verify_numbers(result, ctx) == 0, result.warnings
    assert len(result.claims) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_text", [
    "年产量：23132（吨）。",
    "惠及23132（户）居民。",
    "计划培养23132名。",
])
async def test_parenthesized_unit_is_still_a_fact(record_rebuild, claim_text):
    """括号包住的单位也不能让 planid 同值数字变成身份。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text=claim_text, chunk_ids=["chunk-a"])]
    result, ctx = _case(answer=claim_text, claims=claims, refs=refs,
                       sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []


@pytest.mark.asyncio
async def test_bare_planid_after_words_is_conservatively_treated_as_fact(record_rebuild):
    """事实优先：没有显式身份写法的裸编号一律按待核验事实处理，宁可降级不误信。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="政策23132提出人才队伍建设。", chunk_ids=["chunk-a"])]
    result, ctx = _case(answer="政策23132提出人才队伍建设。", claims=claims,
                       refs=refs, sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []


@pytest.mark.asyncio
async def test_parenthesized_identity_is_allowed(record_rebuild):
    """括号列举位置的身份编号（（23132，苏州市））仍按身份放行。"""
    refs = [_ref("23132", "chunk-a")]
    text = "《苏州市十四五民政事业发展规划》（23132，苏州市）提出人才队伍建设。"
    claims = [Claim(text=text, chunk_ids=["chunk-a"])]
    result, ctx = _case(answer=text, claims=claims, refs=refs,
                       sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) == 0, result.warnings
    assert len(result.claims) == 1


@pytest.mark.asyncio
async def test_measure_after_identity_word_is_still_fact(record_rebuild):
    """「计划：23132（吨）」不能借身份词变成身份。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="计划：23132（吨）。", chunk_ids=["chunk-a"])]
    result, ctx = _case(answer="计划：23132（吨）。", claims=claims, refs=refs,
                       sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []


@pytest.mark.asyncio
async def test_answer_fabricated_parenthesized_fact_downgrades(record_rebuild):
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="提出人才队伍建设。", chunk_ids=["chunk-a"])]
    answer = "该政策年产量：23132（吨），推动人才队伍建设。"
    result, ctx = _case(answer=answer, claims=claims, refs=refs,
                       sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert len(result.claims) == 1
    assert result.evidence_status == "insufficient"
    assert record_rebuild


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_text", [
    "编号为９９９９９的政策提出人才队伍建设。",
    "《未知政策文件名称》提出人才队伍建设。",
    "〈未知政策文件名称〉提出人才队伍建设。",
    "《假政策》提出人才队伍建设。",
])
async def test_unknown_identity_variants_are_rejected(record_rebuild, claim_text):
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text=claim_text, chunk_ids=["chunk-a"])]
    result, ctx = _case(answer=claim_text, claims=claims, refs=refs,
                       sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []


@pytest.mark.asyncio
async def test_fabricated_title_is_rejected(record_rebuild):
    """模型编造本轮来源元数据中不存在的政策篇名：身份核验失败。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="《不存在的某某市数字经济促进条例》提出人才队伍建设。",
                    chunk_ids=["chunk-a"])]
    result, ctx = _case(answer="《不存在的某某市数字经济促进条例》提出人才队伍建设。",
                        claims=claims, refs=refs, sources={"chunk-a": SOURCE})
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []
    assert result.evidence_status == "insufficient"


@pytest.mark.asyncio
async def test_answer_with_unknown_title_downgrades_without_dropping_claims(record_rebuild):
    """答案里出现编造篇名：重建答案并降级，但保留逐条可核验的结论。"""
    refs = [_ref("23132", "chunk-a")]
    claims = [Claim(text="提出人才队伍建设。", chunk_ids=["chunk-a"])]
    answer = ("1. 《苏州市十四五民政事业发展规划》（planid=23132）提出人才队伍建设；\n"
              "2. 《凭空编造的政策文件》也提出类似要求。")
    result, ctx = _case(answer=answer, claims=claims, refs=refs, sources={"chunk-a": SOURCE})
    problems = await verify_numbers(result, ctx)
    assert problems > 0
    assert len(result.claims) == 1
    assert result.evidence_status == "insufficient"
    assert record_rebuild
