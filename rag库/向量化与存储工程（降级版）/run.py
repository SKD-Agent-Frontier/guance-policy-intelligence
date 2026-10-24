"""降级版命令入口；加载本目录的vector_store包，不导入原工程后端。"""
from pathlib import Path
import sys
from vector_store.cli import main

if __name__=='__main__':
    # 管道输出明确用UTF-8，避免Windows默认代码页让中文JSON和路径显示乱码。
    sys.stdout.reconfigure(encoding='utf-8');sys.stderr.reconfigure(encoding='utf-8')
    main(Path(__file__).resolve().parent)
