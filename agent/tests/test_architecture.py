"""固定规模、SDK 边界和依赖方向，防止超出基础 Agent 范围。"""
import ast

from guance_agent.config import ROOT


def test_source_size_and_dependencies():
    source = ROOT / "src/guance_agent"
    modules = "config schemas kb context tools agent runtime runner output main cli".split()
    order = {name: i for i, name in enumerate(modules)}
    counts = []
    for path in source.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        counts.append(len(text.splitlines()))
        assert counts[-1] <= 150, path.name
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] == "agents":
                    assert path.stem in {"tools", "agent", "runtime", "runner"}
                if node.level == 1 and path.stem in order and node.module in order:
                    assert order[node.module] < order[path.stem], path.name
    assert sum(counts) <= 900
