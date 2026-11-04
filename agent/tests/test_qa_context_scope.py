from guance_agent.qa_context import resolve_followup

PRIOR = {
    "question": "沧州市十一五规划主要产业部署",
    "documents": [{"planid": "620", "title": "沧州市十一五规划",
                   "area": "沧州市", "fyp": "第十一个五年规划"}],
    "verified_facts": [{"text": "沧州产业事实", "chunk_ids": ["c1"],
                         "quotes": ["沧州产业事实"]}],
    "evidence_refs": [{"chunk_id": "c1", "planid": "620"}],
    "quote_verification": {"approved": 1, "unverified": 0},
}


def test_comparison_scope_extension_keeps_old_identity_and_requests_both_sides():
    resolution = resolve_followup("请结合石家庄市十一五规划比较产业重点", [PRIOR])

    assert resolution["status"] == "extended"
    assert resolution["inherited_planids"] == ["620"]
    assert set(resolution["requested_constraints"]["areas"]) == {"沧州市", "石家庄市"}
    assert resolution["documents"][0]["planid"] == "620"
    assert "旧来源不能替代新范围证据" in resolution["context_note"]


def test_unqualified_other_county_is_not_treated_as_a_named_region():
    resolution = resolve_followup("与其他区县比较产业发展", [PRIOR])

    assert resolution["status"] == "continued"
    assert resolution["inherited_planids"] == ["620"]
    assert resolution["requested_constraints"] is None


def test_comparison_between_subjects_keeps_the_same_document_scope():
    resolution = resolve_followup("比较农业和工业的重点部署", [PRIOR])

    assert resolution["status"] == "continued"
    assert resolution["inherited_planids"] == ["620"]


def test_explicit_scope_replacement_drops_old_document_identity():
    resolution = resolve_followup("改为只看石家庄市的产业规划", [PRIOR])

    assert resolution["status"] == "scope_changed"
    assert resolution["inherited_planids"] == []
    assert resolution["requested_constraints"]["areas"] == ["石家庄市"]


def test_new_named_region_without_link_replaces_the_retrieval_scope():
    resolution = resolve_followup("石家庄市十一五规划的产业部署", [PRIOR])

    assert resolution["status"] == "scope_changed"
    assert resolution["inherited_planids"] == []
    assert resolution["requested_constraints"]["areas"] == ["石家庄市"]


def test_followup_researches_saved_user_scope_without_inheriting_unverified_sources():
    prior = {
        "question": "衡水市第十三个五年规划的经济发展部署有哪些？",
        "documents": [],
        "verified_facts": [],
        "user_scope": {"areas": ["衡水市"], "periods": ["第十三个五年规划"]},
    }

    resolution = resolve_followup("你上面提到的第①项具体量化目标是什么？", [prior])

    assert resolution["status"] == "fresh_research"
    assert resolution["inherited_planids"] == []
    assert resolution["requested_constraints"] == {
        "areas": ["衡水市"], "periods": ["第十三个五年规划"], "titles": []}
    assert "重新检索" in resolution["reason"]


def test_followup_without_verified_docs_or_saved_scope_fails_closed():
    resolution = resolve_followup(
        "你上面提到的第①项具体量化目标是什么？",
        [{"question": "政策研究", "documents": [], "verified_facts": []}],
    )

    assert resolution["status"] == "ambiguous"
    assert resolution["inherited_planids"] == []


def test_pending_scope_is_replaced_before_referential_recovery():
    prior = {
        "question": "衡水市十三五规划产业部署",
        "documents": [],
        "user_scope": {"areas": ["衡水市"], "periods": ["第十三个五年规划"]},
    }
    route = type("Route", (), {
        "areas": ("改为只看密云县",),
        "canonical_areas": ("密云县",),
        "fyps": ("第十一个五年规划",),
    })()

    resolution = resolve_followup(
        "改为只看密云县十一五规划，你上面提到的第①项是什么？", [prior], route)

    assert resolution["status"] == "scope_changed"
    assert resolution["inherited_planids"] == []
    assert resolution["requested_constraints"]["areas"] == ["密云县"]
    assert resolution["requested_constraints"]["periods"] == ["第十一个五年规划"]


def test_verified_but_foreign_document_cannot_override_saved_user_scope():
    prior = {
        "question": "衡水市十三五规划经济发展",
        "documents": [{"planid": "foreign", "title": "萝北县十二五规划",
                       "area": "萝北县", "fyp": "第十二个五年规划"}],
        "user_scope": {"areas": ["衡水市"], "periods": ["第十三个五年规划"]},
    }

    resolution = resolve_followup("你上面提到的第①项量化目标是什么？", [prior])

    assert resolution["status"] == "fresh_research"
    assert resolution["inherited_planids"] == []
    assert resolution["requested_constraints"]["areas"] == ["衡水市"]
    assert resolution["requested_constraints"]["periods"] == ["第十三个五年规划"]


def test_foreign_identity_mentioned_in_title_is_not_matching_source():
    prior = {
        "question": "衡水市十三五规划产业发展",
        "documents": [{"planid": "foreign", "title": "衡水市与萝北县产业发展比较研究",
                       "area": "萝北县", "fyp": "第十三个五年规划"}],
        "user_scope": {"areas": ["衡水市"], "periods": ["第十三个五年规划"]},
    }

    resolution = resolve_followup("你上面提到的产业发展具体有哪些？", [prior])

    assert resolution["status"] == "fresh_research"
    assert resolution["inherited_planids"] == []
    assert resolution["requested_constraints"]["areas"] == ["衡水市"]


def test_unresolved_replacement_does_not_fall_back_to_pending_old_scope():
    prior = {
        "question": "衡水市十三五规划产业部署",
        "documents": [],
        "user_scope": {"areas": ["衡水市"], "periods": ["第十三个五年规划"]},
    }

    resolution = resolve_followup("改为只看新对象，你上面提到的第①项是什么？", [prior])

    assert resolution["status"] == "ambiguous"
    assert resolution["requested_constraints"] is None
    assert "不会继续使用旧研究范围" in resolution["clarification"]
