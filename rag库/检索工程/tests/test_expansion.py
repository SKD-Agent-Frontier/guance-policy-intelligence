from contextlib import closing

import pytest

from guance_rag import RagKnowledgeBase, RuntimeOptions
from guance_rag.expansion import ExpansionRegistry, load_release


def test_release_rollback_retains_previously_visible_shards(tmp_path):
    base = tmp_path / "base"
    root = tmp_path / "expansion"
    first, second = "md_" + "a" * 40, "md_" + "b" * 40
    with closing(ExpansionRegistry(base, "b1", root)) as registry:
        assert load_release(base, "b1", root) is None
        registry.ready(first, {"documents": 1})
        with pytest.raises(ValueError, match="unverified"):
            registry.activate([second])
        revision, generation = registry.activate([first])
        assert revision == 2 and generation != "b1"
        assert registry.activate([first]) == (revision, generation)
        registry.ready(second, {"documents": 2})
        registry.activate([first, second])
        registry.activate([])
    release = load_release(base, "b1", root)
    assert release.revision == 4 and release.generation == "b1"
    assert release.active == ()
    assert release.history == {first: root / first, second: root / second}
    with pytest.raises(ValueError, match="does not match"):
        load_release(base, "other-base", root)


def test_startup_budget_tracks_active_bm25_shards(tmp_path):
    base = tmp_path / "base"
    root = tmp_path / "expansion"
    options = {"store_project": base, "expansion_root": root, "mode": "bm25"}
    assert RuntimeOptions(**options).startup_timeout == 180.0
    with closing(ExpansionRegistry(base, "base1", root)) as registry:
        shards = [f"md_{i:040x}" for i in range(43)]
        for shard in shards:
            registry.ready(shard, {"documents": 1})
        registry.activate(shards)
        assert RuntimeOptions(**options).startup_timeout == 524.0
        assert RuntimeOptions(**options, startup_timeout=50.0).startup_timeout == 50.0
        assert RuntimeOptions(**(options | {"mode": "hybrid"})).startup_timeout == 180.0
        registry.activate([])
    assert RuntimeOptions(**options).startup_timeout == 180.0


def test_structured_query_filters_are_applied_before_bm25_top_k(options):
    with RagKnowledgeBase(options) as kb:
        hits = kb.search("养老服务", fyp="十四五", area="苏州", admin_level="市级",
                         plantype="专项规划", top_k=1)
        assert hits and hits[0].planid == "1001"
        assert kb.search("养老服务", admin_level="province") == []
        assert kb.search("养老服务", plan_stage="审议") == []
        assert kb.find_by_title("测试规划a", admin_level="prefecture", plantype="专项规划").matched == 1
        assert kb.find_by_title("测试规划a", admin_level="province").matched == 0
