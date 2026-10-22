"""开源组件切块输入入口；复用同一向量化实现，保持两版配置及存储隔离。"""
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parent
sys.path.insert(0,str(PROJECT.parent/'向量化与存储工程'))
from vector_store.cli import main

if __name__=='__main__':
    main(PROJECT)
