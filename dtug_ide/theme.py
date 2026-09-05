"""主题与字体统一管理。

提供：
  * UI 主题（界面暗色 / 亮色）调色板；
  * 编辑器主题（语法配色 + 背景）调色板；
  * 通过 QSettings 持久化当前 UI 主题、编辑器主题、字体族与字号；
  * 由调色板生成各控件的 QSS，供主窗口 / 对话框统一调用，做到「主题可调」。

所有颜色集中在 UI_THEMES / EDITOR_THEMES 两个字典里，新增主题只需追加条目。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from PyQt6.QtCore import QSettings

_ORG = "DTUG"
_APP = "IDE"

# ---------------------------------------------------------------------------
# UI（界面）主题
# ---------------------------------------------------------------------------
UI_THEMES: Dict[str, Dict[str, str]] = {
    "暗色": {
        "window": "#0b0e17",
        "panel": "#111827",
        "editor_bg": "#0d1117",
        "border": "#2a3a5a",
        "text": "#e8edf5",
        "text2": "#94a9c9",
        "text3": "#5a7a9a",
        "accent": "#4f9cf7",
        "input_bg": "#0d1117",
        "success": "#34d399",
        "error": "#f87171",
        "warning": "#fb923c",
        "selection": "rgba(79,156,247,0.30)",
        "tab": "#111827",
        "tab_sel": "#1a2332",
        "menubar": "#111827",
        "grad_g1": "#34d399",
        "grad_g2": "#22d3bf",
        "grad_h1": "#6366f1",
        "grad_h2": "#8b5cf6",
        "line_hl": "#1a2332",
    },
    "亮色": {
        "window": "#eef1f5",
        "panel": "#ffffff",
        "editor_bg": "#ffffff",
        "border": "#cbd5e1",
        "text": "#1f2937",
        "text2": "#475569",
        "text3": "#94a3b8",
        "accent": "#2563eb",
        "input_bg": "#ffffff",
        "success": "#059669",
        "error": "#dc2626",
        "warning": "#ea580c",
        "selection": "rgba(37,99,235,0.18)",
        "tab": "#e5e7eb",
        "tab_sel": "#ffffff",
        "menubar": "#ffffff",
        "grad_g1": "#10b981",
        "grad_g2": "#0ea5e9",
        "grad_h1": "#6366f1",
        "grad_h2": "#8b5cf6",
        "line_hl": "#f1f5f9",
    },
}

# ---------------------------------------------------------------------------
# 编辑器（代码）主题
# ---------------------------------------------------------------------------
EDITOR_THEMES: Dict[str, Dict[str, str]] = {
    "暗色 · Dracula": {
        "bg": "#0d1117",
        "fg": "#e8edf5",
        "line_hl": "#1a2332",
        "line_no_bg": "#0d1117",
        "line_no_fg": "#5a7a9a",
        "selection": "rgba(79,156,247,0.30)",
        "keyword": "#f92672",
        "type": "#66d9ef",
        "number": "#ae81ff",
        "string": "#e6db74",
        "comment": "#75715e",
        "preproc": "#f92672",
        "function": "#a6e22e",
    },
    "暗色 · Monokai": {
        "bg": "#272822",
        "fg": "#f8f8f2",
        "line_hl": "#3e3d32",
        "line_no_bg": "#272822",
        "line_no_fg": "#75715e",
        "selection": "rgba(73,72,62,0.60)",
        "keyword": "#f92672",
        "type": "#66d9ef",
        "number": "#ae81ff",
        "string": "#e6db74",
        "comment": "#75715e",
        "preproc": "#f92672",
        "function": "#a6e22e",
    },
    "暗色 · One Dark": {
        "bg": "#282c34",
        "fg": "#abb2bf",
        "line_hl": "#2c313a",
        "line_no_bg": "#282c34",
        "line_no_fg": "#5c6370",
        "selection": "rgba(86,118,153,0.30)",
        "keyword": "#c678dd",
        "type": "#61afef",
        "number": "#d19a66",
        "string": "#98c379",
        "comment": "#5c6370",
        "preproc": "#c678dd",
        "function": "#61afef",
    },
    "亮色 · GitHub": {
        "bg": "#ffffff",
        "fg": "#24292e",
        "line_hl": "#f6f8fa",
        "line_no_bg": "#ffffff",
        "line_no_fg": "#6a737d",
        "selection": "rgba(3,102,214,0.15)",
        "keyword": "#d73a49",
        "type": "#6f42c1",
        "number": "#005cc5",
        "string": "#032f62",
        "comment": "#6a737d",
        "preproc": "#d73a49",
        "function": "#6f42c1",
    },
}

# 默认字体族优先级（系统中存在其一即采用），最终由编辑器在运行时按 QFontDatabase 决定
DEFAULT_FONT_FAMILY = "Consolas"
DEFAULT_FONT_SIZE = 13

_MONO_FALLBACKS = ["Consolas", "JetBrains Mono", "Fira Code", "Cascadia Code", "Source Code Pro", "Menlo"]


# ---------------------------------------------------------------------------
# 持久化读写
# ---------------------------------------------------------------------------
def _settings() -> QSettings:
    return QSettings(_ORG, _APP)


def ui_themes() -> List[str]:
    return list(UI_THEMES.keys())


def editor_themes() -> List[str]:
    return list(EDITOR_THEMES.keys())


def get_ui_theme() -> str:
    name = _settings().value("ui_theme", "暗色", type=str)
    return name if name in UI_THEMES else "暗色"


def get_editor_theme() -> str:
    name = _settings().value("editor_theme", "暗色 · Dracula", type=str)
    return name if name in EDITOR_THEMES else "暗色 · Dracula"


def get_editor_font() -> Tuple[str, int]:
    family = _settings().value("editor_font_family", DEFAULT_FONT_FAMILY, type=str)
    size = _settings().value("editor_font_size", DEFAULT_FONT_SIZE, type=int)
    if not isinstance(size, int) or size < 8 or size > 40:
        size = DEFAULT_FONT_SIZE
    return family, int(size)


def set_ui_theme(name: str) -> None:
    if name in UI_THEMES:
        _settings().setValue("ui_theme", name)


def set_editor_theme(name: str) -> None:
    if name in EDITOR_THEMES:
        _settings().setValue("editor_theme", name)


def set_editor_font(family: str, size: int) -> None:
    _settings().setValue("editor_font_family", family)
    _settings().setValue("editor_font_size", int(size))


# ---------------------------------------------------------------------------
# 编译 / 连接指令（Dev-C++ 风格，持久化到同一 QSettings）
# ---------------------------------------------------------------------------
def get_cpp_std() -> str:
    from .compiler import DEFAULT_STD
    return _settings().value("cpp_std", DEFAULT_STD, type=str)


def set_cpp_std(std: str) -> None:
    _settings().setValue("cpp_std", std)


def get_compile_flags() -> str:
    from .compiler import DEFAULT_COMPILE_FLAGS
    return _settings().value("compile_flags", DEFAULT_COMPILE_FLAGS, type=str)


def set_compile_flags(text: str) -> None:
    _settings().setValue("compile_flags", text or "")


def get_link_flags() -> str:
    from .compiler import DEFAULT_LINK_FLAGS
    return _settings().value("link_flags", DEFAULT_LINK_FLAGS, type=str)


def set_link_flags(text: str) -> None:
    _settings().setValue("link_flags", text or "")


# ---------------------------------------------------------------------------
# 工具链（编译器路径 / 运行方式），Dev-C++ 风格
# ---------------------------------------------------------------------------
def get_compiler_cmd() -> str:
    from .toolchain import discover_compilers
    default = discover_compilers()[0]
    return _settings().value("compiler_cmd", default, type=str)


def set_compiler_cmd(cmd: str) -> None:
    _settings().setValue("compiler_cmd", cmd or "")


def get_run_mode() -> str:
    """运行方式：external=外部黑窗（Dev-C++ 风格，默认）；capture=内置捕获。"""
    return _settings().value("run_mode", "external", type=str)


def set_run_mode(mode: str) -> None:
    _settings().setValue("run_mode", mode or "external")


def ui_palette() -> Dict[str, str]:
    return dict(UI_THEMES[get_ui_theme()])


def editor_palette() -> Dict[str, str]:
    return dict(EDITOR_THEMES[get_editor_theme()])


# ---------------------------------------------------------------------------
# QSS 构造（由调色板生成，保证「主题可调」）
# ---------------------------------------------------------------------------
def main_window_global_qss(p: Dict[str, str]) -> str:
    """主窗口全局样式（背景、菜单、通用输入/按钮）。

    注意：不要在此处用 ``QWidget { font-family: ... }`` 指定字体——
    QSS 的 font-family 优先级高于控件的 setFont()，会锁死代码编辑器/控制台等
    需要等宽字体的控件，导致「改字体无效」。界面字体统一由 QApplication.setFont 设置。
    """
    return f"""
        QMainWindow {{ background: {p['window']}; }}
        QMenuBar {{ background: {p['menubar']}; color: {p['text']}; }}
        QMenuBar::item:selected {{ background: {p['tab_sel']}; }}
        QMenu {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; }}
        QMenu::item:selected {{ background: {p['tab_sel']}; }}
        QLabel {{ color: {p['text']}; }}
        QLineEdit {{ background: {p['input_bg']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 4px; padding: 4px; }}
        QSpinBox, QDoubleSpinBox {{ background: {p['input_bg']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 4px; padding: 4px; }}
        QPushButton {{ background: {p['tab']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 6px 12px; }}
        QPushButton:hover {{ border-color: {p['accent']}; }}
        QComboBox {{ background: {p['tab']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 4px; padding: 4px; }}
        QComboBox QAbstractItemView {{ background: {p['panel']}; color: {p['text']}; selection-background-color: {p['tab_sel']}; }}
    """


def group_qss(p: Dict[str, str]) -> str:
    return (
        f"QGroupBox {{ color: {p['text2']}; border: 1px solid {p['border']}; "
        f"border-radius: 8px; margin-top: 8px; }}"
        f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}"
    )


def tab_qss(p: Dict[str, str]) -> str:
    return f"""
        QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: 8px; background: {p['editor_bg']}; }}
        QTabBar {{ background: {p['panel']}; }}
        QTabBar::tab {{
            background: {p['tab']}; color: {p['text2']};
            padding: 6px 14px; border-top-left-radius: 6px; border-top-right-radius: 6px;
            border: 1px solid {p['border']}; border-bottom: none; margin-bottom: -1px;
        }}
        QTabBar::tab:selected {{ background: {p['tab_sel']}; color: {p['text']}; }}
        QTabBar::tab:!selected:hover {{ background: {p['tab_sel']}; }}
    """


def console_qss(p: Dict[str, str]) -> str:
    return f"QPlainTextEdit {{ background: {p['editor_bg']}; color: {p['text']}; border: none; }}"


def tree_qss(p: Dict[str, str]) -> str:
    return f"""
        QTreeWidget {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 6px; }}
        QTreeWidget::item {{ padding: 4px; border-bottom: 1px solid {p['border']}; }}
        QTreeWidget::item:selected {{ background: {p['selection']}; }}
        QHeaderView::section {{ background: {p['tab_sel']}; color: {p['text2']}; padding: 4px; border: none; }}
    """


def chat_qss(p: Dict[str, str]) -> str:
    """聊天面板样式：消息列表 / 会话列表 / 输入框背景随主题，避免暗色下白板。"""
    return f"""
        QWidget#chatRoot {{ background: {p['panel']}; }}
        QListWidget {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 6px; }}
        QListWidget::item {{ padding: 4px; }}
        QListWidget::item:selected {{ background: {p['selection']}; }}
        QLineEdit {{ background: {p['input_bg']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 4px; padding: 4px; }}
    """


def button_green_qss(p: Dict[str, str]) -> str:
    return (
        f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        f"stop:0 {p['grad_g1']},stop:1 {p['grad_g2']}); color: {p['window']}; "
        f"border-radius: 6px; padding: 6px 16px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        f"stop:0 {p['grad_g1']},stop:1 {p['grad_g2']}); }}"
    )


def button_home_qss(p: Dict[str, str]) -> str:
    return (
        f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        f"stop:0 {p['grad_h1']},stop:1 {p['grad_h2']}); color: #ffffff; "
        f"border-radius: 6px; padding: 6px 14px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        f"stop:0 {p['grad_h1']},stop:1 {p['grad_h2']}); }}"
    )


def button_danger_qss(p: Dict[str, str]) -> str:
    return (
        f"QPushButton {{ background: {p['tab']}; color: {p['text']}; "
        f"border: 1px solid {p['border']}; border-radius: 6px; padding: 6px 12px; }}"
        f"QPushButton:hover {{ border-color: {p['error']}; }}"
    )


def button_neutral_qss(p: Dict[str, str]) -> str:
    return (
        f"QPushButton {{ background: {p['tab']}; color: {p['text']}; "
        f"border: 1px solid {p['border']}; border-radius: 6px; padding: 6px 12px; }}"
        f"QPushButton:hover {{ border-color: {p['accent']}; }}"
    )


def plain_edit_qss(p: Dict[str, str]) -> str:
    return (
        f"QPlainTextEdit {{ background: {p['editor_bg']}; color: {p['text']}; "
        f"border: 1px solid {p['border']}; border-radius: 4px; padding: 4px; }}"
    )


def toolbar_qss(p: Dict[str, str]) -> str:
    return f"QToolBar {{ background: {p['menubar']}; border-bottom: 1px solid {p['border']}; spacing: 6px; padding: 4px; }}"


def statusbar_qss(p: Dict[str, str]) -> str:
    return f"QStatusBar {{ background: {p['menubar']}; color: {p['text3']}; border-top: 1px solid {p['border']}; }}"


def splitter_qss(p: Dict[str, str]) -> str:
    return f"QSplitter::handle {{ background: {p['border']}; }}"


def dialog_qss(p: Dict[str, str]) -> str:
    """通用对话框样式（登录框、设置框等）。"""
    return f"""
        QDialog {{ background: {p['window']}; }}
        QLabel {{ color: {p['text']}; }}
        QLineEdit {{ background: {p['input_bg']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 8px; }}
        QComboBox {{ background: {p['input_bg']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 6px; }}
        QComboBox QAbstractItemView {{ background: {p['panel']}; color: {p['text']}; selection-background-color: {p['tab_sel']}; }}
        QSpinBox {{ background: {p['input_bg']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 6px; }}
        QPushButton {{ background: {p['tab']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 8px 14px; }}
        QPushButton:hover {{ border-color: {p['accent']}; }}
        QPushButton#primary {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {p['grad_g1']},stop:1 {p['grad_g2']}); color: {p['window']}; font-weight: bold; }}
        QPushButton#home {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {p['grad_h1']},stop:1 {p['grad_h2']}); color: #ffffff; font-weight: bold; }}
        QCheckBox {{ color: {p['text']}; }}
        QFrame#sep {{ background: {p['border']}; }}
        QGroupBox {{ color: {p['text2']}; border: 1px solid {p['border']}; border-radius: 8px; margin-top: 8px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
    """


def monospace_fallbacks() -> List[str]:
    return list(_MONO_FALLBACKS)
