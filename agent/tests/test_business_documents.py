from guance_agent.business import PolicyRef, group_by_document
from guance_agent.schemas import Hit


def hit(planid, chunk_id, **changes):
    values = dict(
        planid=planid,
        chunk_id=chunk_id,
        title=f"标题-{planid}",
        area="甲市",
        fyp="第十四个五年规划",
        admin_level="市级",
        plantype_label="规划",
        section_idx=0,
        section_path="第一章",
        content="原文",
        char_start=0,
        char_end=2,
        score=1.0,
    )
    return Hit(**{**values, **changes})


def test_group_by_document_preserves_document_order_and_unique_chunks():
    hits = [
        hit("A", "a-1", title="甲市农业发展规划", area="甲市",
            fyp="第十四个五年规划", admin_level="市级", plantype_label="专项规划",
            section_idx=1, section_path="第一章"),
        hit("B", "b-1"),
        hit("A", "a-2", section_idx=2, section_path="第二章"),
        hit("B", "b-1"),
        hit("A", "a-1", section_idx=3, section_path="第三章"),
        hit("C", "c-1"),
    ]

    grouped = group_by_document(hits)

    assert [policy.planid for policy in grouped] == ["A", "B", "C"]
    assert [policy.chunk_ids for policy in grouped] == [["a-1", "a-2"], ["b-1"], ["c-1"]]
    assert grouped[0].model_dump() == {
        "planid": "A",
        "title": "甲市农业发展规划",
        "area": "甲市",
        "fyp": "第十四个五年规划",
        "admin_level": "市级",
        "plantype_label": "专项规划",
        "chunk_ids": ["a-1", "a-2"],
    }


def test_group_by_document_keeps_first_hit_metadata_including_none():
    first = hit("A", "a-1", title="首条标题", area=None, fyp=None,
                admin_level=None, plantype_label=None)
    later = hit("A", "a-2", title="冲突标题", area="乙市", fyp="十五五",
                admin_level="省级", plantype_label="专项")

    policy = group_by_document([first, later])[0]

    assert policy == PolicyRef(planid="A", title="首条标题", area=None, fyp=None,
                               admin_level=None, plantype_label=None,
                               chunk_ids=["a-1", "a-2"])


def test_group_by_document_does_not_mutate_input_and_returns_independent_lists():
    hits = [hit("A", "a-1"), hit("B", "b-1")]
    before = [item.model_dump() for item in hits]

    grouped = group_by_document(hits)
    grouped[0].chunk_ids.append("local-only")
    grouped_again = group_by_document(hits)

    assert [item.model_dump() for item in hits] == before
    assert grouped[1].chunk_ids == ["b-1"]
    assert grouped_again[0].chunk_ids == ["a-1"]
    assert grouped_again[1].chunk_ids == ["b-1"]


def test_group_by_document_empty_input():
    assert group_by_document([]) == []
