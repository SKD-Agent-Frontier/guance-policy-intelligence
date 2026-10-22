"""Windows 容错运行入口：只为本工程原子替换增加有界重试，不改变切分。

文件监视器/杀毒扫描可能短时持有 checkpoint.json，导致 os.replace 报
WinError 5。这里仍调用同一个 os.replace，不改权限、不删目标、不降级为
非原子覆盖。持续拒绝访问时照常失败，不能把失败隐藏为成功。
"""
import os
from pathlib import Path
import time

DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 0.8, 0.8, 0.8)


def replace_with_retry(original, source, target, *, project, sleeper=time.sleep):
    """只重试本工程内 PermissionError，且必须保持原始异常类型与目标路径。"""
    target_path=Path(target).resolve()
    if not target_path.is_relative_to(Path(project).resolve()):
        return original(source,target)
    for attempt in range(len(DELAYS)+1):
        try:
            return original(source,target)
        except PermissionError:
            if attempt==len(DELAYS):
                raise
            sleeper(DELAYS[attempt])


if __name__=="__main__":
    # 仅当前 CLI 进程生效；切分模块文件与其已冻结指纹保持原样。
    from chunk import main
    project=Path(__file__).resolve().parent
    original=os.replace
    os.replace=lambda source,target:replace_with_retry(original,source,target,project=project)
    try:
        raise SystemExit(main())
    finally:
        os.replace=original
