"""Entry point for DTUG IDE."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QFontDatabase
from PyQt6.QtWidgets import QApplication

from .main_window import MainWindow
from .utils import find_app_icon


def _write_crash(msg: str) -> None:
    try:
        p = Path(sys.executable).resolve().parent / "DTUG_IDE_crash.log"
        with p.open("w", encoding="utf-8", errors="replace") as fh:
            fh.write(msg)
    except Exception:
        pass


def _ensure_qt_plugin_path() -> None:
    """冻结后显式把打包的 Qt 插件目录加入搜索路径，避免平台插件找不到。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        # onedir: plugins 位于 <exe_dir>/_internal/PyQt6/Qt6/plugins
        candidates = [
            base / "_internal" / "PyQt6" / "Qt6" / "plugins",
            base / "PyQt6" / "Qt6" / "plugins",
        ]
        for p in candidates:
            if p.is_dir():
                QApplication.addLibraryPath(str(p))
                os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(p / "platforms"))
                break


def _ensure_webengine_paths() -> None:
    """冻结后设置 QtWebEngine 的 resources / locales 路径（内嵌登录页用）。

    PyQt6-WebEngine 为懒导入，PyInstaller 的 qtwebengine 运行时 hook 可能未自动
    注入，这里根据 PyQt6 包的实际位置把 resources / locales 目录指给 QtWebEngine。
    兼容 PyInstaller 6.x 的 _internal/ 目录布局（冻结后 PyQt6 位于 <exe>/_internal/PyQt6）。
    """
    if getattr(sys, "frozen", False):
        try:
            import PyQt6

            qt6 = Path(PyQt6.__file__).resolve().parent / "Qt6"
        except Exception:
            qt6 = Path(sys.executable).resolve().parent / "_internal" / "PyQt6" / "Qt6"
        res = qt6 / "resources"
        loc = qt6 / "translations" / "qtwebengine_locales"
        if res.is_dir():
            os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(res)
        if loc.is_dir():
            os.environ["QTWEBENGINE_LOCALES_PATH"] = str(loc)


def main() -> int:
    # Enable High DPI scaling (Qt6 默认开启，属性在 PyQt6 中已移除，故做存在性判断)
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    _ensure_qt_plugin_path()
    _ensure_webengine_paths()

    try:
        app = QApplication(sys.argv)
    except Exception:
        # 最常见原因：Qt Windows 平台插件(qwindows.dll)加载/初始化失败
        msg = (
            "FAILED to create QApplication.\n"
            "最常见原因：Qt Windows 平台插件(qwindows.dll)未能加载/初始化，\n"
            "通常是缺少系统依赖(如 MSVC 运行库 / d3dcompiler / OpenGL)或杀毒软件拦截。\n\n"
            + "".join(traceback.format_exc())
        )
        _write_crash(msg)
        raise

    app.setApplicationName("DTUG IDE")
    app.setOrganizationName("DTUG")

    # Set application/taskbar icon (new icon)
    icon_path = find_app_icon()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    # Load JetBrains Mono if available (requires font file path; skip for now)
    pass

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
