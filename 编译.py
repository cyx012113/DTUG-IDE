"""DTUG IDE 兼容启动器。

可直接双击运行，也可通过 ``python 编译.py`` 启动。
PyInstaller 也以本文件作为冻结入口。

若程序启动失败（例如 Qt 平台插件加载失败），异常会被写入
与 exe 同目录的 ``DTUG_IDE_crash.log``，方便排查。
"""

import sys
import traceback
from pathlib import Path

CRASH_LOG = Path(sys.executable).resolve().parent / "DTUG_IDE_crash.log"


def _write_crash(msg: str) -> None:
    try:
        with CRASH_LOG.open("w", encoding="utf-8", errors="replace") as fh:
            fh.write(msg)
    except Exception:
        pass


def _excepthook(et, ev, tb) -> None:
    _write_crash("UNCAUGHT EXCEPTION:\n" + "".join(traceback.format_exception(et, ev, tb)))


# 捕获事件循环中抛出的未处理异常（如 Qt 平台插件初始化失败）
sys.excepthook = _excepthook

try:
    from dtug_ide import main
except Exception:
    _write_crash("IMPORT ERROR:\n" + traceback.format_exc())
    raise

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        _write_crash("RUNTIME ERROR:\n" + traceback.format_exc())
        raise
