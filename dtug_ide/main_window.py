"""Main window for DTUG IDE."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QSettings, QSize, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSplitter,
    QStatusBar, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser,
    QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    QSpinBox, QDoubleSpinBox, QCheckBox, QGridLayout, QSizePolicy,
    QAbstractItemView,
)

from .auth import AuthClient
from .compiler import Compiler, CompileResult
from .editor import CodeEditor
from .judge import JudgeEngine, JudgeResult, TestCase, TestResult
from .login_dialog import LoginDialog
from .utils import (
    clean_temp_dir, ensure_dir, find_app_icon, find_compiler_candidates,
    is_valid_cpp_filename, read_file_text, safe_remove, write_file_text,
)


class JudgeWorker(QThread):
    """Background worker for judging."""
    finished = pyqtSignal(object)
    progress = pyqtSignal(int, int)  # current, total

    def __init__(self, engine: JudgeEngine, code: str, tests: List[TestCase], time_limit: float, memory_limit: float) -> None:
        super().__init__()
        self.engine = engine
        self.code = code
        self.tests = tests
        self.time_limit = time_limit
        self.memory_limit = memory_limit

    def run(self) -> None:
        result = self.engine.judge(self.code, self.tests, self.time_limit, self.memory_limit)
        self.finished.emit(result)


class CompileRunWorker(QThread):
    """Background worker for compile & run."""
    compile_done = pyqtSignal(object)
    run_done = pyqtSignal(object)

    def __init__(self, compiler: Compiler, code: str, input_data: str = "", run_in_term: bool = False) -> None:
        super().__init__()
        self.compiler = compiler
        self.code = code
        self.input_data = input_data
        self.run_in_term = run_in_term

    def run(self) -> None:
        comp = self.compiler.compile(self.code)
        self.compile_done.emit(comp)
        if comp.success and comp.exe_path and not self.run_in_term:
            run = self.compiler.run_sandboxed(comp.exe_path, self.input_data, time_limit=10.0, memory_limit_mb=512.0)
            self.compiler.cleanup(comp.exe_path)
            self.run_done.emit(run)
        elif comp.success and comp.exe_path and self.run_in_term:
            self.compiler.run_in_terminal(comp.exe_path)


class TokenValidateWorker(QThread):
    """后台校验已保存的 token 是否有效（调用 /me）。"""

    done = pyqtSignal(bool, object)

    def __init__(self, client: "AuthClient", token: str) -> None:
        super().__init__()
        self.client = client
        self.token = token

    def run(self) -> None:
        ok, data = self.client.get_me(self.token)
        self.done.emit(ok, data)


class FileTab:
    def __init__(self, name: str = "未命名.cpp", path: Optional[str] = None, content: str = "") -> None:
        self.name = name
        self.path = path
        self.content = content
        self.modified = False
        self.tests: List[TestCase] = []


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DTUG IDE")
        icon_path = find_app_icon()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))
        self.setMinimumSize(1200, 800)

        self.settings = QSettings("DTUG", "IDE")
        self.temp_dir = Path(tempfile.gettempdir()) / "dtug_ide"
        ensure_dir(self.temp_dir)
        clean_temp_dir(self.temp_dir)

        self.compiler = Compiler(self.temp_dir)
        self.judge_engine = JudgeEngine(self.compiler)

        self.tabs: List[FileTab] = []
        self.current_tab = -1

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._connect_events()
        self._new_file()
        self._restore_settings()
        self._init_auth()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Left: editor + console
        left = QVBoxLayout()
        left.setSpacing(8)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #2a3a5a; border-radius: 8px; background: #0d1117; }
            QTabBar::tab { background: #111827; color: #94a9c9; padding: 6px 14px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #1a2332; color: #e8edf5; }
            QTabBar::tab:hover { background: #1a2332; }
        """)
        left.addWidget(self.tab_widget, stretch=3)

        # Console
        console_group = QGroupBox("控制台")
        console_group.setStyleSheet("QGroupBox { color: #94a9c9; border: 1px solid #2a3a5a; border-radius: 8px; margin-top: 8px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }")
        console_layout = QVBoxLayout(console_group)
        console_layout.setContentsMargins(4, 8, 4, 4)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)
        font = QFont("Consolas", 10)
        self.console.setFont(font)
        self.console.setStyleSheet("QPlainTextEdit { background: #0d1117; color: #e8edf5; border: none; }")
        console_layout.addWidget(self.console)
        left.addWidget(console_group, stretch=1)

        left_widget = QWidget()
        left_widget.setLayout(left)

        # Right: test cases / judge panel
        right = QVBoxLayout()
        right.setSpacing(8)

        # Judge controls
        judge_group = QGroupBox("评测控制台")
        judge_group.setStyleSheet("QGroupBox { color: #94a9c9; border: 1px solid #2a3a5a; border-radius: 8px; margin-top: 8px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }")
        judge_layout = QVBoxLayout(judge_group)
        judge_layout.setContentsMargins(8, 8, 8, 8)

        limits_layout = QHBoxLayout()
        limits_layout.addWidget(QLabel("时间限制 (s):"))
        self.time_limit_spin = QDoubleSpinBox()
        self.time_limit_spin.setRange(0.1, 60.0)
        self.time_limit_spin.setValue(1.0)
        self.time_limit_spin.setSingleStep(0.1)
        limits_layout.addWidget(self.time_limit_spin)
        limits_layout.addWidget(QLabel("内存限制 (MB):"))
        self.memory_limit_spin = QSpinBox()
        self.memory_limit_spin.setRange(1, 4096)
        self.memory_limit_spin.setValue(256)
        limits_layout.addWidget(self.memory_limit_spin)
        limits_layout.addStretch()
        judge_layout.addLayout(limits_layout)

        btn_layout = QHBoxLayout()
        self.import_btn = QPushButton("📁 导入文件夹")
        self.import_btn.setStyleSheet("QPushButton { background: #1a2332; color: #e8edf5; border: 1px solid #2a3a5a; border-radius: 6px; padding: 6px 12px; } QPushButton:hover { border-color: #4f9cf7; }")
        self.judge_btn = QPushButton("▶ 开始评测")
        self.judge_btn.setStyleSheet("QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #34d399,stop:1 #22d3ee); color: #0b0e17; border-radius: 6px; padding: 6px 16px; font-weight: bold; } QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #4ade80,stop:1 #2dd4bf); }")
        self.clear_tests_btn = QPushButton("🗑️ 清空")
        self.clear_tests_btn.setStyleSheet("QPushButton { background: #1a2332; color: #e8edf5; border: 1px solid #2a3a5a; border-radius: 6px; padding: 6px 12px; } QPushButton:hover { border-color: #f87171; }")
        btn_layout.addWidget(self.import_btn)
        btn_layout.addWidget(self.judge_btn)
        btn_layout.addWidget(self.clear_tests_btn)
        judge_layout.addLayout(btn_layout)

        # Test list
        self.test_list = QTreeWidget()
        self.test_list.setHeaderLabels(["样例", "状态", "时间", "内存"])
        self.test_list.setColumnWidth(0, 120)
        self.test_list.setColumnWidth(1, 50)
        self.test_list.setColumnWidth(2, 60)
        self.test_list.setColumnWidth(3, 60)
        self.test_list.setStyleSheet("""
            QTreeWidget { background: #111827; color: #e8edf5; border: 1px solid #2a3a5a; border-radius: 6px; }
            QTreeWidget::item { padding: 4px; border-bottom: 1px solid rgba(255,255,255,0.05); }
            QTreeWidget::item:selected { background: rgba(79,156,247,0.15); }
            QHeaderView::section { background: #1a2332; color: #94a9c9; padding: 4px; border: none; }
        """)
        self.test_list.setRootIsDecorated(False)
        judge_layout.addWidget(self.test_list)

        self.judge_summary = QLabel("等待评测")
        self.judge_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.judge_summary.setStyleSheet("color: #94a9c9; padding: 8px; background: rgba(0,0,0,0.2); border-radius: 6px;")
        judge_layout.addWidget(self.judge_summary)

        right.addWidget(judge_group)

        # Quick test editor
        quick_group = QGroupBox("快速样例")
        quick_group.setStyleSheet("QGroupBox { color: #94a9c9; border: 1px solid #2a3a5a; border-radius: 8px; margin-top: 8px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }")
        quick_layout = QVBoxLayout(quick_group)
        quick_layout.setContentsMargins(8, 8, 8, 8)

        quick_layout.addWidget(QLabel("输入:"))
        self.quick_input = QPlainTextEdit()
        self.quick_input.setMaximumBlockCount(100)
        self.quick_input.setStyleSheet("QPlainTextEdit { background: #0d1117; color: #e8edf5; border: 1px solid #2a3a5a; border-radius: 4px; }")
        self.quick_input.setPlaceholderText("stdin")
        quick_layout.addWidget(self.quick_input)

        quick_layout.addWidget(QLabel("期望输出:"))
        self.quick_expected = QPlainTextEdit()
        self.quick_expected.setMaximumBlockCount(100)
        self.quick_expected.setStyleSheet("QPlainTextEdit { background: #0d1117; color: #e8edf5; border: 1px solid #2a3a5a; border-radius: 4px; }")
        self.quick_expected.setPlaceholderText("expected (optional)")
        quick_layout.addWidget(self.quick_expected)

        quick_btn_layout = QHBoxLayout()
        self.add_quick_btn = QPushButton("➕ 添加样例")
        self.run_quick_btn = QPushButton("▶ 运行当前")
        self.run_quick_btn.setStyleSheet("QPushButton { background: #34d399; color: #0b0e17; border-radius: 6px; padding: 4px 12px; font-weight: bold; }")
        quick_btn_layout.addWidget(self.add_quick_btn)
        quick_btn_layout.addWidget(self.run_quick_btn)
        quick_layout.addLayout(quick_btn_layout)

        right.addWidget(quick_group)

        right_widget = QWidget()
        right_widget.setLayout(right)
        right_widget.setMaximumWidth(420)
        right_widget.setMinimumWidth(320)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setStyleSheet("QSplitter::handle { background: #2a3a5a; }")
        main_layout.addWidget(splitter)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet("QStatusBar { background: #111827; color: #5a7a9a; border-top: 1px solid #2a3a5a; }")
        self.status_label = QLabel("就绪")
        self.status_cursor = QLabel("行 1, 列 1")
        self.status_file = QLabel("未命名.cpp")
        self.status_compiler = QLabel("g++")
        self.status_bar.addWidget(self.status_file)
        self.status_bar.addPermanentWidget(self.status_cursor)
        self.status_bar.addPermanentWidget(self.status_compiler)
        self.status_bar.addPermanentWidget(self.status_label)
        self.user_status_label = QLabel("未登录")
        self.user_status_label.setStyleSheet("color: #5a7a9a;")
        self.status_bar.addPermanentWidget(self.user_status_label)

        self.setStyleSheet("""
            QMainWindow { background: #0b0e17; }
            QWidget { font-family: 'Segoe UI', system-ui, sans-serif; }
            QMenuBar { background: #111827; color: #e8edf5; }
            QMenuBar::item:selected { background: #1a2332; }
            QMenu { background: #111827; color: #e8edf5; border: 1px solid #2a3a5a; }
            QMenu::item:selected { background: #1a2332; }
            QLabel { color: #e8edf5; }
            QLineEdit { background: #0d1117; color: #e8edf5; border: 1px solid #2a3a5a; border-radius: 4px; padding: 4px; }
            QSpinBox, QDoubleSpinBox { background: #0d1117; color: #e8edf5; border: 1px solid #2a3a5a; border-radius: 4px; padding: 4px; }
            QPushButton { background: #1a2332; color: #e8edf5; border: 1px solid #2a3a5a; border-radius: 6px; padding: 6px 12px; }
            QPushButton:hover { border-color: #4f9cf7; }
        """)

    def _build_menu(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件 (&F)")
        self.new_act = QAction("新建 (&N)", self)
        self.new_act.setShortcut(QKeySequence("Ctrl+N"))
        file_menu.addAction(self.new_act)
        self.open_act = QAction("打开 (&O)", self)
        self.open_act.setShortcut(QKeySequence("Ctrl+O"))
        file_menu.addAction(self.open_act)
        self.save_act = QAction("保存 (&S)", self)
        self.save_act.setShortcut(QKeySequence("Ctrl+S"))
        file_menu.addAction(self.save_act)
        self.save_as_act = QAction("另存为 (&A)", self)
        self.save_as_act.setShortcut(QKeySequence("Ctrl+Shift+S"))
        file_menu.addAction(self.save_as_act)
        file_menu.addSeparator()
        self.exit_act = QAction("退出 (&X)", self)
        file_menu.addAction(self.exit_act)

        run_menu = menubar.addMenu("运行 (&R)")
        self.compile_run_act = QAction("编译运行 (&R)", self)
        self.compile_run_act.setShortcut(QKeySequence("Ctrl+Return"))
        run_menu.addAction(self.compile_run_act)
        self.compile_run_term_act = QAction("编译运行 (独立终端)", self)
        run_menu.addAction(self.compile_run_term_act)
        run_menu.addSeparator()
        self.judge_act = QAction("开始评测 (&J)", self)
        self.judge_act.setShortcut(QKeySequence("Shift+Return"))
        run_menu.addAction(self.judge_act)

        edit_menu = menubar.addMenu("编辑 (&E)")
        self.default_code_act = QAction("默认代码模板 (&D)", self)
        edit_menu.addAction(self.default_code_act)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setStyleSheet("QToolBar { background: #111827; border-bottom: 1px solid #2a3a5a; spacing: 6px; padding: 4px; }")
        self.addToolBar(toolbar)

        self.compiler_combo = QComboBox()
        self.compiler_combo.addItems(find_compiler_candidates())
        self.compiler_combo.setStyleSheet("QComboBox { background: #1a2332; color: #e8edf5; border: 1px solid #2a3a5a; border-radius: 4px; padding: 4px; }")
        toolbar.addWidget(QLabel("编译器:"))
        toolbar.addWidget(self.compiler_combo)
        toolbar.addSeparator()

        self.run_tool_btn = QPushButton("▶ 编译运行")
        self.run_tool_btn.setStyleSheet("QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #34d399,stop:1 #22d3ee); color: #0b0e17; border-radius: 6px; padding: 6px 14px; font-weight: bold; }")
        toolbar.addWidget(self.run_tool_btn)

        self.judge_tool_btn = QPushButton("📊 评测")
        toolbar.addWidget(self.judge_tool_btn)

        # 右侧弹性占位，将登录按钮推到最右
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.auth_btn = QPushButton("🔐 登录")
        self.auth_btn.setStyleSheet(
            "QPushButton { background: #1a2332; color: #e8edf5; border: 1px solid #2a3a5a;"
            " border-radius: 6px; padding: 6px 14px; } QPushButton:hover { border-color: #4f9cf7; }"
        )
        toolbar.addWidget(self.auth_btn)

        self.user_menu = QMenu(self)
        self.logout_act = QAction("退出登录", self)
        self.user_menu.addAction(self.logout_act)

    def _connect_events(self) -> None:
        self.new_act.triggered.connect(self._new_file)
        self.open_act.triggered.connect(self._open_file)
        self.save_act.triggered.connect(self._save_file)
        self.save_as_act.triggered.connect(self._save_as_file)
        self.exit_act.triggered.connect(self.close)
        self.compile_run_act.triggered.connect(self._compile_run)
        self.compile_run_term_act.triggered.connect(lambda: self._compile_run(in_terminal=True))
        self.judge_act.triggered.connect(self._start_judge)
        self.default_code_act.triggered.connect(self._edit_default_code)
        self.run_tool_btn.clicked.connect(self._compile_run)
        self.judge_tool_btn.clicked.connect(self._start_judge)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)
        self.tab_widget.currentChanged.connect(self._tab_changed)
        self.compiler_combo.currentTextChanged.connect(self._compiler_changed)
        self.import_btn.clicked.connect(self._import_test_folder)
        self.clear_tests_btn.clicked.connect(self._clear_tests)
        self.judge_btn.clicked.connect(self._start_judge)
        self.add_quick_btn.clicked.connect(self._add_quick_test)
        self.run_quick_btn.clicked.connect(self._run_quick_test)
        self.auth_btn.clicked.connect(self._open_login)
        self.logout_act.triggered.connect(self._logout)

    def _get_current_editor(self) -> Optional[CodeEditor]:
        w = self.tab_widget.currentWidget()
        return w if isinstance(w, CodeEditor) else None

    def _current_tab(self) -> Optional[FileTab]:
        idx = self.tab_widget.currentIndex()
        if 0 <= idx < len(self.tabs):
            return self.tabs[idx]
        return None

    def _new_file(self) -> None:
        count = sum(1 for t in self.tabs if t.name.startswith("未命名"))
        name = f"未命名-{count+1}.cpp" if count > 0 else "未命名.cpp"
        code = self.settings.value("default_code", self._builtin_default(), type=str)
        tab = FileTab(name=name, content=code)
        self.tabs.append(tab)
        editor = CodeEditor()
        editor.setPlainText(code)
        editor.textChanged.connect(self._on_text_changed)
        editor.cursorPositionChanged.connect(self._update_cursor_status)
        idx = self.tab_widget.addTab(editor, name)
        self.tab_widget.setCurrentIndex(idx)
        self.current_tab = idx
        self._update_status()

    def _builtin_default(self) -> str:
        return '#include <iostream>\n\nint main() {\n    std::cout << "Hello, 世界!" << std::endl;\n    return 0;\n}'

    def _on_text_changed(self) -> None:
        tab = self._current_tab()
        editor = self._get_current_editor()
        if tab and editor:
            tab.modified = True
            tab.content = editor.toPlainText()
            idx = self.tab_widget.currentIndex()
            self.tab_widget.setTabText(idx, tab.name + " *")
            self._update_status()

    def _update_cursor_status(self) -> None:
        editor = self._get_current_editor()
        if editor:
            cursor = editor.textCursor()
            self.status_cursor.setText(f"行 {cursor.blockNumber()+1}, 列 {cursor.columnNumber()+1}")
            self.status_label.setText(f"{len(editor.toPlainText())} 字符 | {editor.blockCount()} 行")

    def _update_status(self) -> None:
        tab = self._current_tab()
        if tab:
            self.status_file.setText(tab.name + (" *" if tab.modified else ""))
            self.status_compiler.setText(self.compiler_combo.currentText())
            self._render_tests()

    def _tab_changed(self, idx: int) -> None:
        self.current_tab = idx
        self._update_status()
        self._update_cursor_status()

    def _close_tab(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.tabs):
            return
        tab = self.tabs[idx]
        if tab.modified:
            r = QMessageBox.question(self, "未保存", f"{tab.name} 已修改，是否保存？", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Save:
                self.tab_widget.setCurrentIndex(idx)
                if not self._save_file():
                    return
            elif r == QMessageBox.StandardButton.Cancel:
                return
        self.tabs.pop(idx)
        self.tab_widget.removeTab(idx)
        if not self.tabs:
            self._new_file()

    def _open_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "打开文件", "", "C++ 文件 (*.cpp *.cxx *.cc *.c *.h *.hpp);;所有文件 (*)")
        for path in paths:
            content = read_file_text(path)
            name = Path(path).name
            tab = FileTab(name=name, path=path, content=content)
            self.tabs.append(tab)
            editor = CodeEditor()
            editor.setPlainText(content)
            editor.textChanged.connect(self._on_text_changed)
            editor.cursorPositionChanged.connect(self._update_cursor_status)
            self.tab_widget.addTab(editor, name)
            self.tab_widget.setCurrentIndex(self.tab_widget.count() - 1)
        self._update_status()

    def _save_file(self) -> bool:
        tab = self._current_tab()
        editor = self._get_current_editor()
        if not tab or not editor:
            return False
        if tab.path:
            write_file_text(tab.path, editor.toPlainText())
            tab.modified = False
            tab.content = editor.toPlainText()
            idx = self.tab_widget.currentIndex()
            self.tab_widget.setTabText(idx, tab.name)
            self._log(f"已保存 {tab.path}", "info")
            return True
        else:
            return self._save_as_file()

    def _save_as_file(self) -> bool:
        tab = self._current_tab()
        editor = self._get_current_editor()
        if not tab or not editor:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "另存为", tab.name, "C++ 文件 (*.cpp *.cxx *.cc *.c *.h *.hpp)")
        if path:
            write_file_text(path, editor.toPlainText())
            tab.path = path
            tab.name = Path(path).name
            tab.modified = False
            tab.content = editor.toPlainText()
            idx = self.tab_widget.currentIndex()
            self.tab_widget.setTabText(idx, tab.name)
            self._log(f"已保存 {path}", "info")
            return True
        return False

    def _compiler_changed(self, text: str) -> None:
        self.compiler.set_compiler(text)
        self.status_compiler.setText(text)

    def _compile_run(self, in_terminal: bool = False) -> None:
        tab = self._current_tab()
        editor = self._get_current_editor()
        if not tab or not editor:
            return
        code = editor.toPlainText()
        if not code.strip():
            QMessageBox.warning(self, "警告", "代码为空")
            return
        self.console.clear()
        self._log("正在编译...", "info")
        self.run_tool_btn.setEnabled(False)
        self.worker = CompileRunWorker(self.compiler, code, run_in_term=in_terminal)
        self.worker.compile_done.connect(self._on_compile_done)
        self.worker.run_done.connect(self._on_run_done)
        self.worker.finished.connect(lambda: self.run_tool_btn.setEnabled(True))
        self.worker.start()

    def _on_compile_done(self, result: CompileResult) -> None:
        if result.success:
            self._log("编译成功", "success")
            if result.output.strip():
                self._log("编译信息:\n" + result.output.strip(), "info")
        else:
            self._log("编译失败", "error")
            if result.output:
                self._log(result.output, "error")
            if result.error:
                self._log(result.error, "error")

    def _on_run_done(self, result) -> None:
        if hasattr(result, 'stdout'):
            if result.stdout:
                self._log("输出:\n" + result.stdout, "success")
            if result.stderr:
                self._log("错误输出:\n" + result.stderr, "warning")
            self._log(f"运行时间: {result.elapsed:.3f}s | 内存: {result.memory_mb:.1f}MB | 返回码: {result.returncode}", "info")

    def _start_judge(self) -> None:
        tab = self._current_tab()
        editor = self._get_current_editor()
        if not tab or not editor:
            return
        code = editor.toPlainText()
        if not code.strip():
            QMessageBox.warning(self, "警告", "代码为空")
            return
        if not tab.tests:
            QMessageBox.information(self, "提示", "当前没有测试用例，请先添加或导入")
            return
        self.console.clear()
        self._log("开始评测...", "info")
        self.judge_btn.setEnabled(False)
        self.judge_tool_btn.setEnabled(False)
        self.test_list.clear()
        for tc in tab.tests:
            item = QTreeWidgetItem([tc.name, "⏳", "-", "-"])
            self.test_list.addTopLevelItem(item)
        self.judge_summary.setText("评测中...")
        self.worker = JudgeWorker(self.judge_engine, code, tab.tests, self.time_limit_spin.value(), self.memory_limit_spin.value())
        self.worker.finished.connect(self._on_judge_done)
        self.worker.finished.connect(lambda: self.judge_btn.setEnabled(True))
        self.worker.finished.connect(lambda: self.judge_tool_btn.setEnabled(True))
        self.worker.start()

    def _on_judge_done(self, result: JudgeResult) -> None:
        if result.overall_status == "compile_error":
            self._log("编译错误 (CE)", "error")
            self._log(result.compile_result.output or "", "error")
            self.judge_summary.setText("编译错误 (CE)")
            self.judge_summary.setStyleSheet("color: #f87171; padding: 8px; background: rgba(0,0,0,0.2); border-radius: 6px;")
            for i in range(self.test_list.topLevelItemCount()):
                item = self.test_list.topLevelItem(i)
                item.setText(1, "CE")
            return

        ac = 0
        self.test_list.clear()
        for res in result.results:
            color = ""
            if res.status == "AC":
                ac += 1
                color = "color: #34d399;"
            elif res.status == "WA":
                color = "color: #f87171;"
            elif res.status == "TLE":
                color = "color: #fb923c;"
            elif res.status == "MLE":
                color = "color: #f472b6;"
            elif res.status == "RE":
                color = "color: #f87171;"
            item = QTreeWidgetItem([
                res.name,
                res.status,
                f"{res.time:.3f}s",
                f"{res.memory_mb:.1f}MB",
            ])
            item.setToolTip(0, f"输入:\n{res.expected[:500]}\n\n输出:\n{res.stdout[:500]}\n\n错误:\n{res.stderr[:500]}")
            from PyQt6.QtGui import QBrush
            item.setForeground(1, QBrush(QColor({"AC": "#34d399", "WA": "#f87171", "TLE": "#fb923c", "MLE": "#f472b6", "RE": "#f87171"}.get(res.status, "#e8edf5"))))
            self.test_list.addTopLevelItem(item)

        total = len(result.results)
        rate = ac / total * 100 if total else 0
        self.judge_summary.setText(f"AC {ac}/{total} ({rate:.1f}%)")
        if ac == total:
            self.judge_summary.setStyleSheet("color: #34d399; padding: 8px; background: rgba(0,0,0,0.2); border-radius: 6px; font-weight: bold;")
            self._log(f"全部通过! AC {ac}/{total}", "success")
        else:
            self.judge_summary.setStyleSheet("color: #fb923c; padding: 8px; background: rgba(0,0,0,0.2); border-radius: 6px; font-weight: bold;")
            self._log(f"通过 {ac}/{total}", "warning")

    def _import_test_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "导入测试文件夹")
        if not folder:
            return
        p = Path(folder)
        files = list(p.iterdir())
        file_map: dict[str, dict] = {}
        for f in files:
            if not f.is_file():
                continue
            name = f.name
            if "." not in name:
                continue
            # Be careful with files starting with dot
            if name.startswith("."):
                # e.g. .in -> base might be tricky; skip hidden files
                continue
            ext = name.rsplit(".", 1)[1].lower()
            base = name.rsplit(".", 1)[0]
            if ext == "in":
                file_map.setdefault(base, {})["input"] = read_file_text(f)
            elif ext in ("ans", "out"):
                file_map.setdefault(base, {})["expected"] = read_file_text(f)

        # Sort by natural order
        def natural_key(s: str):
            import re
            return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]

        imported = []
        for base in sorted(file_map.keys(), key=natural_key):
            data = file_map[base]
            if "input" in data and "expected" in data:
                imported.append(TestCase(name=base, input=data["input"], expected=data["expected"]))

        tab = self._current_tab()
        if tab:
            tab.tests.extend(imported)
            self._render_tests()
            self._log(f"导入 {len(imported)} 个样例", "info")

    def _clear_tests(self) -> None:
        tab = self._current_tab()
        if tab:
            tab.tests.clear()
            self._render_tests()

    def _add_quick_test(self) -> None:
        tab = self._current_tab()
        if not tab:
            return
        name = f"quick{len(tab.tests)+1}"
        tc = TestCase(name=name, input=self.quick_input.toPlainText(), expected=self.quick_expected.toPlainText())
        tab.tests.append(tc)
        self._render_tests()
        self.quick_input.clear()
        self.quick_expected.clear()

    def _run_quick_test(self) -> None:
        tab = self._current_tab()
        editor = self._get_current_editor()
        if not tab or not editor:
            return
        code = editor.toPlainText()
        if not code.strip():
            return
        inp = self.quick_input.toPlainText()
        self.console.clear()
        self._log("编译运行 (快速样例)...", "info")
        self.run_tool_btn.setEnabled(False)
        self.worker = CompileRunWorker(self.compiler, code, inp)
        self.worker.compile_done.connect(self._on_compile_done)
        self.worker.run_done.connect(self._on_run_done)
        self.worker.finished.connect(lambda: self.run_tool_btn.setEnabled(True))
        self.worker.start()

    def _render_tests(self) -> None:
        self.test_list.clear()
        tab = self._current_tab()
        if not tab:
            return
        for tc in tab.tests:
            item = QTreeWidgetItem([tc.name, "⏳", "-", "-"])
            self.test_list.addTopLevelItem(item)

    def _edit_default_code(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("默认代码模板")
        dlg.setMinimumSize(600, 400)
        layout = QVBoxLayout(dlg)
        edit = QPlainTextEdit()
        edit.setPlainText(self.settings.value("default_code", self._builtin_default(), type=str))
        edit.setStyleSheet("QPlainTextEdit { background: #0d1117; color: #e8edf5; border: 1px solid #2a3a5a; border-radius: 4px; }")
        layout.addWidget(edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(lambda: (self.settings.setValue("default_code", edit.toPlainText()), dlg.accept()))
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        dlg.exec()

    def _log(self, text: str, level: str = "info") -> None:
        import html as html_module
        colors = {"info": "#94a9c9", "success": "#34d399", "error": "#f87171", "warning": "#fb923c"}
        color = colors.get(level, "#e8edf5")
        safe = html_module.escape(text).replace("\n", "<br>")
        self.console.appendHtml(f'<span style="color:{color};">{safe}</span>')

    def _restore_settings(self) -> None:
        sz = self.settings.value("window_size", None)
        pos = self.settings.value("window_pos", None)
        if sz is not None and isinstance(sz, QSize):
            self.resize(sz)
        if pos is not None:
            self.move(pos)
        compiler = self.settings.value("compiler", "")
        if compiler:
            idx = self.compiler_combo.findText(compiler)
            if idx >= 0:
                self.compiler_combo.setCurrentIndex(idx)

    # ---------- 账号 / 登录（对接主页 auth-worker） ----------
    def _init_auth(self) -> None:
        self.auth_client = AuthClient()
        self.token = ""
        self.current_user = None
        self._update_user_ui()
        saved = self.settings.value("auth_token", "")
        if saved:
            self.token = str(saved)
            self._validate_token()

    def _validate_token(self) -> None:
        self.auth_btn.setEnabled(False)
        self.user_status_label.setText("验证登录中...")
        self._validate_worker = TokenValidateWorker(self.auth_client, self.token)
        self._validate_worker.done.connect(self._on_token_validated)
        self._validate_worker.finished.connect(self._validate_worker.deleteLater)
        self._validate_worker.start()

    def _on_token_validated(self, ok: bool, data) -> None:
        self.auth_btn.setEnabled(True)
        if ok and isinstance(data, dict):
            self.current_user = data
            self._update_user_ui()
            self._log(f"已恢复登录：{data.get('username', '')}", "success")
        else:
            self.token = ""
            self.current_user = None
            self.settings.remove("auth_token")
            self._update_user_ui()

    def _open_login(self) -> None:
        if self.token and self.current_user:
            return  # 已登录，点击无效（退出走菜单）
        dlg = LoginDialog(self.auth_client, self)
        dlg.login_success.connect(self._on_login_success)
        dlg.exec()

    def _on_login_success(self, token: str, user: dict, remember: bool) -> None:
        self.token = token
        self.current_user = user
        if remember:
            self.settings.setValue("auth_token", token)
        else:
            self.settings.remove("auth_token")
        self._update_user_ui()
        self._log(f"已登录：{user.get('username', '')}", "success")

    def _logout(self) -> None:
        self.token = ""
        self.current_user = None
        self.settings.remove("auth_token")
        self.auth_btn.setMenu(None)
        self._update_user_ui()
        self._log("已退出登录", "info")

    def _update_user_ui(self) -> None:
        if self.current_user:
            name = self.current_user.get("username", "用户")
            tag = self.current_user.get("tag") or ("[管理员]" if self.current_user.get("is_op") else "")
            self.auth_btn.setText(f"👤 {name}")
            self.auth_btn.setMenu(self.user_menu)
            self.user_status_label.setText(f"已登录：{name} {tag}".rstrip())
        else:
            self.auth_btn.setText("🔐 登录")
            self.auth_btn.setMenu(None)
            self.user_status_label.setText("未登录")

    def closeEvent(self, event) -> None:
        self.settings.setValue("window_size", self.size())
        self.settings.setValue("window_pos", self.pos())
        self.settings.setValue("compiler", self.compiler_combo.currentText())
        clean_temp_dir(self.temp_dir)
        event.accept()
