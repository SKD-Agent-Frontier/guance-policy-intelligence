"""固定 SDK 边界与依赖方向，防止基础 Agent 越过架构分层。"""
import ast

from guance_agent.config import ROOT


def test_source_structure_and_dependencies():
    # 不再限制行数：行数不是复杂度的可靠代理，且会诱导为凑数字而牺牲可读性。
    # 保留 SDK 导入边界与单向依赖两项结构性约束。
    source = ROOT / "src/guance_agent"
    modules = ("config schemas kb kb_sample context tools agent client sessions "
               "runtime runner output orchestrator main cli").split()
    order = {name: i for i, name in enumerate(modules)}
    for path in source.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] == "agents":
                    assert path.stem in {"tools", "agent", "runtime", "runner"}
        # 依赖方向只约束模块级导入；函数内的延迟导入正是用来打破环的。
        for node in tree.body:
            if not (isinstance(node, ast.ImportFrom) and node.level == 1 and node.module):
                continue
            if path.stem in order and node.module in order:
                assert order[node.module] < order[path.stem], path.name
