"""题目管理面板（左侧停靠）：题库列表 + 题面 + 快捷操作。"""

from __future__ import annotations

import html
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDockWidget, QHBoxLayout, QLabel, QPushButton, QSplitter, QTextBrowser,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .problem_store import ProblemInfo, ProblemStore
from .theme import (
    button_danger_qss, button_green_qss, button_neutral_qss, group_qss,
    splitter_qss, tree_qss, ui_palette,
)


def _image_as_link(_self, tokens, idx, _options, _env) -> str:
    """markdown 图片 → 文字链接（与聊天面板同策略：不加载远程图片，避免泄露 IP）。

    注意：``MarkdownIt.add_render_rule`` 会把函数 ``__get__`` 绑成渲染器方法，
    因此第一个参数是占位的 ``self``（与 chat_widgets 的渲染规则一致）。
    """
    token = tokens[idx]
    src = str(token.attrGet("src") or "")
    alt = (token.content or "").strip() or "图片"
    if not src.lower().startswith(("http://", "https://")):
        return f"<span>[{html.escape(alt)}]</span>"
    return (
        f'<a href="{html.escape(src, quote=True)}">'
        f'[图片：{html.escape(alt)}]</a>'
    )


def render_markdown(text: str) -> str:
    """题面 Markdown → 安全 HTML（禁原始 HTML、图片转链接、linkify）。"""
    text = text or ""
    if not text.strip():
        return '<p style="color:#888;">（暂无题面，点「编辑题目」补充）</p>'
    try:
        from markdown_it import MarkdownIt

        md = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": True})
        md.add_render_rule("image", _image_as_link)
        return md.render(text)
    except Exception:
        return f"<pre>{html.escape(text)}</pre>"


class ProblemDock(QDockWidget):
    """题库面板。所有「需要主窗口配合」的操作都发信号，由 MainWindow 接线。"""

    # 主窗口接线用
    new_requested = pyqtSignal()                  # 新建题目
    edit_requested = pyqtSignal(object)           # 编辑题目
    delete_requested = pyqtSignal(object)         # 删除题目
    load_samples_requested = pyqtSignal(object)   # ProblemInfo → 数据导入当前标签并评测
    save_solution_requested = pyqtSignal(object)  # ProblemInfo → 保存当前编辑器代码
    open_solution_requested = pyqtSignal(object)  # ProblemInfo → 打开 sol.cpp
    open_dir_requested = pyqtSignal(object)       # ProblemInfo → 资源管理器打开目录

    def __init__(self, store: ProblemStore, parent=None) -> None:
        super().__init__("题目", parent)
        self.store = store
        self._problems: List[ProblemInfo] = []
        self._current: Optional[ProblemInfo] = None
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        p = ui_palette()
        central = QWidget()
        central.setObjectName("problemRoot")
        central.setStyleSheet(self._root_qss())
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # 工具栏
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.new_btn = QToolButton()
        self.new_btn.setText("➕ 新建")
        self.new_btn.setToolTip("新建题目")
        self.edit_btn = QToolButton()
        self.edit_btn.setText("✏️ 编辑")
        self.del_btn = QToolButton()
        self.del_btn.setText("🗑 删除")
        self.del_btn.setStyleSheet(button_danger_qss(p))
        self.reload_btn = QToolButton()
        self.reload_btn.setText("🔄")
        self.reload_btn.setToolTip("刷新列表")
        for b in (self.new_btn, self.edit_btn, self.del_btn, self.reload_btn):
            b.setStyleSheet(button_neutral_qss(p))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            bar.addWidget(b)
        bar.addStretch(1)
        self.root_label = QLabel()
        self.root_label.setToolTip(str(self.store.root))
        self.root_label.setStyleSheet(f"color: {p['text2']}; font-size: 11px;")
        self.root_label.setText("题库: " + self._short_path(self.store.root))
        bar.addWidget(self.root_label)
        root.addLayout(bar)

        # 左右分栏：列表 + 详情
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(splitter_qss(p))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(tree_qss(p))
        self.tree.currentItemChanged.connect(self._on_select)
        splitter.addWidget(self.tree)

        detail = QWidget()
        dlay = QVBoxLayout(detail)
        dlay.setSpacing(6)
        dlay.setContentsMargins(4, 4, 4, 4)
        self.meta_label = QLabel("从左侧选择一道题目")
        self.meta_label.setWordWrap(True)
        self.meta_label.setStyleSheet(f"color: {p['text2']}; font-size: 12px;")
        dlay.addWidget(self.meta_label)
        self.desc_view = QTextBrowser()
        self.desc_view.setOpenExternalLinks(False)
        self.desc_view.setStyleSheet(
            f"background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']};"
            " border-radius: 6px; padding: 6px;"
        )
        self.desc_view.setMinimumWidth(280)
        self.desc_view.anchorClicked.connect(self._open_link)
        dlay.addWidget(self.desc_view, stretch=1)

        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.sample_btn = QPushButton("🧪 测样例")
        self.sample_btn.setStyleSheet(button_green_qss(p))
        self.save_sol_btn = QPushButton("💾 存为解答")
        self.open_sol_btn = QPushButton("📄 打开解答")
        self.open_dir_btn = QPushButton("📂 打开目录")
        for b in (self.sample_btn, self.save_sol_btn, self.open_sol_btn, self.open_dir_btn):
            b.setStyleSheet(button_neutral_qss(p) if b is not self.sample_btn else button_green_qss(p))
            b.setEnabled(False)
            acts.addWidget(b)
        dlay.addLayout(acts)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, stretch=1)

        self.setWidget(central)

        # 信号
        self.new_btn.clicked.connect(self._request_new)
        self.edit_btn.clicked.connect(self._request_edit)
        self.del_btn.clicked.connect(self._request_delete)
        self.reload_btn.clicked.connect(self.reload)
        self.sample_btn.clicked.connect(self._emit_samples)
        self.save_sol_btn.clicked.connect(lambda: self._emit(self.save_solution_requested))
        self.open_sol_btn.clicked.connect(lambda: self._emit(self.open_solution_requested))
        self.open_dir_btn.clicked.connect(lambda: self._emit(self.open_dir_requested))

    def _root_qss(self) -> str:
        p = ui_palette()
        return (
            f"QWidget#problemRoot {{ background: {p['window']}; }}\n"
            f"QToolButton {{ padding: 3px 8px; }}\n"
        )

    def apply_theme(self) -> None:
        """主题切换：只刷新样式，不重建控件树（教训见 MEMORY.md）。"""
        p = ui_palette()
        central = self.widget()
        if central is not None:
            central.setStyleSheet(self._root_qss())
        self.tree.setStyleSheet(tree_qss(p))
        self.del_btn.setStyleSheet(button_danger_qss(p))
        for b in (self.new_btn, self.edit_btn, self.reload_btn,
                  self.save_sol_btn, self.open_sol_btn, self.open_dir_btn):
            b.setStyleSheet(button_neutral_qss(p))
        self.sample_btn.setStyleSheet(button_green_qss(p))
        self.meta_label.setStyleSheet(f"color: {p['text2']}; font-size: 12px;")
        self.desc_view.setStyleSheet(
            f"background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']};"
            " border-radius: 6px; padding: 6px;"
        )
        self.root_label.setStyleSheet(f"color: {p['text2']}; font-size: 11px;")
        self._refresh_detail()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._problems = self.store.list_problems()
        self.tree.clear()
        for info in self._problems:
            item = QTreeWidgetItem([self._item_text(info)])
            item.setData(0, Qt.ItemDataRole.UserRole, info.pid)
            item.setToolTip(0, f"{info.title}\n来源：{info.source or '—'}")
            self.tree.addTopLevelItem(item)
        if self._problems:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:
            self._current = None
            self._refresh_detail()

    def _item_text(self, info: ProblemInfo) -> str:
        n = len(self.store.sample_cases(info)) if info.dir else 0
        tag = f" · {info.source}" if info.source else ""
        return f"{info.title}{tag} · {n} 样例"

    def _on_select(self, current, _previous) -> None:
        if current is None:
            self._current = None
        else:
            pid = current.data(0, Qt.ItemDataRole.UserRole)
            self._current = self.store.get(str(pid)) if pid else None
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        info = self._current
        has = info is not None
        for b in (self.sample_btn, self.save_sol_btn, self.open_sol_btn, self.open_dir_btn):
            b.setEnabled(has)
        if not has:
            self.meta_label.setText("从左侧选择一道题目")
            self.desc_view.setHtml(render_markdown(""))
            return
        n = len(self.store.sample_cases(info))
        has_sol = info.solution_path().is_file()
        self.meta_label.setText(
            f"<b>{html.escape(info.title)}</b>　来源：{html.escape(info.source or '—')}　"
            f"标签：{html.escape('、'.join(info.tags) or '—')}　"
            f"时限 {info.time_limit:g}s / 内存 {info.memory_limit:g}MB　"
            f"样例 {n} 组　解答：{'✅ 已保存' if has_sol else '未保存'}"
        )
        self.desc_view.setHtml(render_markdown(info.description))

    # ------------------------------------------------------------------
    # 操作转发
    # ------------------------------------------------------------------
    def _emit(self, signal) -> None:
        if self._current is not None:
            signal.emit(self._current)

    def _emit_samples(self) -> None:
        if self._current is not None:
            self.load_samples_requested.emit(self._current)

    def _request_new(self) -> None:
        self.new_requested.emit()

    def _request_edit(self) -> None:
        if self._current is not None:
            self.edit_requested.emit(self._current)

    def _request_delete(self) -> None:
        if self._current is not None:
            self.delete_requested.emit(self._current)

    def _open_link(self, url) -> None:
        if url.scheme().lower() in ("http", "https"):
            QDesktopServices.openUrl(url)

    @staticmethod
    def _short_path(path) -> str:
        s = str(path)
        return s if len(s) <= 42 else "…" + s[-42:]
