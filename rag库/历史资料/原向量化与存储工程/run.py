"""原版切块输入的命令入口；所有实际逻辑由共享vector_store包实现。"""
from pathlib import Path
from vector_store.cli import main

if __name__=='__main__':
    main(Path(__file__).resolve().parent)
