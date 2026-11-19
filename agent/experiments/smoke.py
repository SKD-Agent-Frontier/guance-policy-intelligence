"""Offline smoke: validates the frozen mock RAG and experiment question suite, no LLM/API."""
import json
from pathlib import Path

from guance_agent.config import Settings
from guance_agent.kb import get_kb

here = Path(__file__).resolve().parent
cases = json.loads((here / "questions.json").read_text(encoding="utf-8"))
assert len(cases) == 5 and len({x["id"] for x in cases}) == 5, "frozen case list invalid"

kb = get_kb(Settings(kb_backend="mock"))
hits = kb.search("甲市十四五农业农村规划", None, None, 20)
assert any(hit.planid == "TEST-001" for hit in hits), "Mock RAG did not return TEST-001"

passage = kb.read("TEST-001", None, 6000, chunk_id="TEST-001__s01__p00")
assert "完善农业灌溉设施" in passage.content, "original chunk read failed"
assert passage.planid == "TEST-001", "source planid changed"

print(json.dumps({"offline_smoke":"passed","cases":len(cases),
                  "source":"mock-only","model_calls":0}))
