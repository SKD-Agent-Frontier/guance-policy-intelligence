"""比较功能的公共导出。

这里只提供模块类；实际注册由 business/__init__.py 显式完成，不在导入时创建
模型连接、运行库或 Runtime。两个类共用取证、事实矩阵与结果约束。
"""
from .module import PolicyComparisonModule, SchemeComparisonModule

__all__ = ["PolicyComparisonModule", "SchemeComparisonModule"]
