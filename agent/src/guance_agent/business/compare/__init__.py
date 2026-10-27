"""两个已实现的比较业务模块；共享取证和矩阵，不自行创建运行底座。"""
from .module import PolicyComparisonModule, SchemeComparisonModule

__all__ = ["PolicyComparisonModule", "SchemeComparisonModule"]
