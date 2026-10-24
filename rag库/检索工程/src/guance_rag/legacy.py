"""按独立包名复用本地存储组件，避免与原版vector_store包名冲突。"""
import hashlib
import importlib
import importlib.util
from pathlib import Path
import sys


def component(project: Path, module: str):
    package_path = project / "vector_store"
    name = "_guance_local_" + hashlib.sha256(str(package_path).encode()).hexdigest()[:12]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, package_path / "__init__.py", submodule_search_locations=[str(package_path)])
        if spec is None or spec.loader is None:
            raise RuntimeError("找不到本地存储组件")
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    return importlib.import_module(name + "." + module)
