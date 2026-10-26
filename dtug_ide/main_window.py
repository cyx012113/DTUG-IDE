"""Main window for DTUG IDE."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QSettings, QSize, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QShortcut
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
from .client_sig import default_signer, token_sid
from .compiler import Compiler, CompileResult
from .editor import CodeEditor
from .formatter import format_cpp
from .interactive_run import InteractiveRunDialog
from .data_generator import DataGenDialog
from .judge import JudgeControl, JudgeEngine, JudgeResult, TestCase, TestResult
from .login_dialog import LoginDialog
from .problem_dialog import ProblemEditDialog
from .problem_panel import ProblemDock
from .problem_store import ProblemInfo, ProblemStore, default_root
from .settings_dialog import SettingsDialog
from .stress import StressDialog
from .theme import (
    button_danger_qss, button_green_qss, button_neutral_qss,
    console_qss, dialog_qss, get_compile_flags, get_compiler_cmd, get_cpp_std,
    get_link_flags, get_run_mode, group_qss, main_window_global_qss,
    plain_edit_qss, splitter_qss, statusbar_qss, tab_qss, toolbar_qss,
    tree_qss, ui_palette,
)
from .utils import (
    clean_temp_dir, ensure_dir, find_app_icon, find_compiler_candidates,
    is_valid_cpp_filename, read_file_text, safe_remove, write_file_text,
)


class JudgeWorker(QThread):
    """Background worker for judging."""
    finished = pyqtSignal(object)
    progress = pyqtSignal(int, int)  # current, total
    result_ready = pyqtSignal(object, int)  # TestResult, index（每完成一个用例即发出）

    def __init__(self, engine: JudgeEngine, code: str, tests: List[TestCase], time_limit: float, memory_limit: float, ignore_case: bool = False, control: Optional[JudgeControl] = None) -> None:
        super().__init__()
        self.engine = engine
        self.code = code
        self.tests = tests
        self.time_limit = time_limit
        self.memory_limit = memory_limit
        self.ignore_case = ignore_case
        self.control = control or JudgeControl()

    def run(self) -> None:
        def _on_result(res: "TestResult", idx: int) -> None:
            self.result_ready.emit(res, idx)

        result = self.engine.judge(
            self.code, self.tests, self.time_limit, self.memory_limit,
            on_result=_on_result, control=self.control, ignore_case=self.ignore_case,
        )
        self.finished.emit(result)

    # ---- 评测控制：终止 / 暂停 / 继续 ----
    def request_stop(self) -> None:
        """请求停止：置标志并立即终止正在运行的子进程。"""
        self.control.stop = True
        try:
            self.engine.compiler.kill_active()
        except Exception:
            pass

    def request_pause(self) -> None:
        """请求暂停：当前用例跑完后挂起（不杀进程）。"""
        self.control.pause = True

    def request_resume(self) -> None:
        """请求继续：解除暂停。"""
        self.control.pause = False


class CompileRunWorker(QThread):
    """Background worker for compile & run."""
    compile_done = pyqtSignal(object)
    run_done = pyqtSignal(object)

    def __init__(self, compiler: Compiler, code: str, input_data: str = "", run_in_term: bool = False, compile_only: bool = False) -> None:
        super().__init__()
        self.compiler = compiler
        self.code = code
        self.input_data = input_data
        self.run_in_term = run_in_term
        self.compile_only = compile_only

    def run(self) -> None:
        comp = self.compiler.compile(self.code)
        self.compile_done.emit(comp)
        if self.compile_only:
            return
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
        self._apply_compiler_options()
        self.judge_engine = JudgeEngine(self.compiler)

        self.tabs: List[FileTab] = []
        self.current_tab = -1

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._connect_events()
        self._setup_shortcuts()
        self._new_file()
        self._restore_settings()
        self._init_auth()
        self._init_problems()

    def _build_ui(self) -> None:
        self._ui = ui_palette()
        # 界面字体统一由 QApplication 设置（不在 QSS 里写 QWidget font-family，
        # 否则会覆盖代码编辑器/控制台的等宽字体，导致「改字体无效」）。
        # 字体族必须带 emoji 回退（Segoe UI Emoji / Noto Color Emoji 等）：
        # 若只声明单一族，Qt 富文本引擎（消息气泡 QLabel 走 QTextDocument）渲染
        # 表情时会分配错误的水平步进宽度，emoji 被压成「很窄的一小条」。
        app_font = QFont()
        app_font.setFamilies([
            "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei",
            "Segoe UI Emoji", "Noto Color Emoji", "Apple Color Emoji",
        ])
        app_font.setPointSize(10)
        QApplication.setFont(app_font)
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
        self.tab_widget.setStyleSheet(tab_qss(self._ui))
        left.addWidget(self.tab_widget, stretch=3)

        # Console
        console_group = QGroupBox("控制台")
        console_group.setStyleSheet(group_qss(self._ui))
        console_layout = QVBoxLayout(console_group)
        console_layout.setContentsMargins(4, 8, 4, 4)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)
        font = QFont("Consolas", 10)
        self.console.setFont(font)
        self.console.setStyleSheet(console_qss(self._ui))
        console_layout.addWidget(self.console)
        left.addWidget(console_group, stretch=1)

        left_widget = QWidget()
        left_widget.setLayout(left)

        # Right: test cases / judge panel
        right = QVBoxLayout()
        right.setSpacing(8)

        # Judge controls
        judge_group = QGroupBox("评测控制台")
        judge_group.setStyleSheet(group_qss(self._ui))
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

        # 比较选项 + 评测控制（暂停 / 继续 / 停止）
        opt_layout = QHBoxLayout()
        self.ignore_case_check = QCheckBox("忽略大小写")
        self.ignore_case_check.setToolTip("比较输出时忽略大小写；「去行末空格、去末尾空行」始终生效。")
        opt_layout.addWidget(self.ignore_case_check)
        opt_layout.addStretch()
        judge_layout.addLayout(opt_layout)

        ctrl_layout = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ 暂停")
        self.pause_btn.setStyleSheet(button_neutral_qss(self._ui))
        self.pause_btn.setEnabled(False)
        self.resume_btn = QPushButton("▶ 继续")
        self.resume_btn.setStyleSheet(button_neutral_qss(self._ui))
        self.resume_btn.setEnabled(False)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setStyleSheet(button_danger_qss(self._ui))
        self.stop_btn.setEnabled(False)
        ctrl_layout.addWidget(self.pause_btn)
        ctrl_layout.addWidget(self.resume_btn)
        ctrl_layout.addWidget(self.stop_btn)
        judge_layout.addLayout(ctrl_layout)

        btn_layout = QHBoxLayout()
        self.import_btn = QPushButton("📁 导入文件夹")
        self.import_btn.setStyleSheet(button_neutral_qss(self._ui))
        self.judge_btn = QPushButton("▶ 开始评测")
        self.judge_btn.setStyleSheet(button_green_qss(self._ui))
        self.clear_tests_btn = QPushButton("🗑️ 清空")
        self.clear_tests_btn.setStyleSheet(button_danger_qss(self._ui))
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
        self.test_list.setStyleSheet(tree_qss(self._ui))
        self.test_list.setRootIsDecorated(False)
        judge_layout.addWidget(self.test_list)

        self.judge_summary = QLabel("等待评测")
        self.judge_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.judge_summary.setStyleSheet(self._judge_summary_style("#94a9c9"))
        judge_layout.addWidget(self.judge_summary)

        right.addWidget(judge_group)

        # Quick test editor
        quick_group = QGroupBox("快速样例")
        quick_group.setStyleSheet(group_qss(self._ui))
        quick_layout = QVBoxLayout(quick_group)
        quick_layout.setContentsMargins(8, 8, 8, 8)

        quick_layout.addWidget(QLabel("输入:"))
        self.quick_input = QPlainTextEdit()
        self.quick_input.setMaximumBlockCount(100)
        self.quick_input.setStyleSheet(plain_edit_qss(self._ui))
        self.quick_input.setPlaceholderText("stdin")
        quick_layout.addWidget(self.quick_input)

        quick_layout.addWidget(QLabel("期望输出:"))
        self.quick_expected = QPlainTextEdit()
        self.quick_expected.setMaximumBlockCount(100)
        self.quick_expected.setStyleSheet(plain_edit_qss(self._ui))
        self.quick_expected.setPlaceholderText("expected (optional)")
        quick_layout.addWidget(self.quick_expected)

        quick_btn_layout = QHBoxLayout()
        self.add_quick_btn = QPushButton("➕ 添加样例")
        self.run_quick_btn = QPushButton("▶ 运行当前")
        self.run_quick_btn.setStyleSheet(button_green_qss(self._ui))
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
        splitter.setStyleSheet(splitter_qss(self._ui))
        main_layout.addWidget(splitter)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet(statusbar_qss(self._ui))
        self.status_label = QLabel("就绪")
        self.status_cursor = QLabel("行 1, 列 1")
        self.status_file = QLabel("未命名.cpp")
        self.status_compiler = QLabel("g++")
        self.status_bar.addWidget(self.status_file)
        self.status_bar.addPermanentWidget(self.status_cursor)
        self.status_bar.addPermanentWidget(self.status_compiler)
        self.status_bar.addPermanentWidget(self.status_label)
        self.user_status_label = QLabel("未登录")
        self.user_status_label.setStyleSheet(f"color: {self._ui['text3']};")
        self.status_bar.addPermanentWidget(self.user_status_label)

        self.setStyleSheet(main_window_global_qss(self._ui))

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
        self.compile_act = QAction("编译 (F9)", self)
        run_menu.addAction(self.compile_act)
        self.run_act = QAction("运行 (F10)", self)
        run_menu.addAction(self.run_act)
        self.compile_run_act = QAction("编译并运行 (F11)", self)
        self.compile_run_act.setShortcut(QKeySequence("Ctrl+Return"))
        run_menu.addAction(self.compile_run_act)
        self.compile_run_term_act = QAction("编译运行 (独立终端)", self)
        run_menu.addAction(self.compile_run_term_act)
        run_menu.addSeparator()
        self.judge_act = QAction("开始评测 (&J)", self)
        self.judge_act.setShortcut(QKeySequence("Shift+Return"))
        run_menu.addAction(self.judge_act)
        self.interactive_act = QAction("交互运行 (&I)", self)
        self.interactive_act.setShortcut(QKeySequence("Ctrl+Shift+R"))
        run_menu.addAction(self.interactive_act)
        self.datagen_act = QAction("数据生成 (&G)", self)
        run_menu.addAction(self.datagen_act)
        self.stress_act = QAction("对拍 (&S)", self)
        run_menu.addAction(self.stress_act)

        edit_menu = menubar.addMenu("编辑 (&E)")
        self.default_code_act = QAction("默认代码模板 (&D)", self)
        edit_menu.addAction(self.default_code_act)
        self.format_act = QAction("格式化代码 (Ctrl+Shift+A)", self)
        edit_menu.addAction(self.format_act)

        problem_menu = menubar.addMenu("题目 (&P)")
        self.problem_new_act = QAction("新建题目 (&N)", self)
        problem_menu.addAction(self.problem_new_act)
        self.problem_reload_act = QAction("刷新题库 (&R)", self)
        problem_menu.addAction(self.problem_reload_act)
        self.problem_root_act = QAction("题库目录… (&D)", self)
        problem_menu.addAction(self.problem_root_act)

        settings_menu = menubar.addMenu("设置 (&T)")
        self.pref_act = QAction("外观与编辑器… (&P)", self)
        settings_menu.addAction(self.pref_act)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setStyleSheet(toolbar_qss(self._ui))
        self.addToolBar(toolbar)

        self.compiler_combo = QComboBox()
        self.compiler_combo.addItems(find_compiler_candidates())
        toolbar.addWidget(QLabel("编译器:"))
        toolbar.addWidget(self.compiler_combo)
        toolbar.addSeparator()

        self.run_tool_btn = QPushButton("▶ 编译运行")
        self.run_tool_btn.setStyleSheet(button_green_qss(self._ui))
        toolbar.addWidget(self.run_tool_btn)

        self.format_tool_btn = QPushButton("🎨 格式化")
        self.format_tool_btn.setStyleSheet(button_neutral_qss(self._ui))
        toolbar.addWidget(self.format_tool_btn)

        self.datagen_tool_btn = QPushButton("🎲 数据生成")
        self.datagen_tool_btn.setStyleSheet(button_neutral_qss(self._ui))
        toolbar.addWidget(self.datagen_tool_btn)

        self.judge_tool_btn = QPushButton("📊 评测")
        self.judge_tool_btn.setStyleSheet(button_neutral_qss(self._ui))
        toolbar.addWidget(self.judge_tool_btn)

        # 右侧弹性占位，将登录按钮推到最右
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.auth_btn = QPushButton("🔐 登录")
        self.auth_btn.setStyleSheet(button_neutral_qss(self._ui))
        toolbar.addWidget(self.auth_btn)

        self.user_menu = QMenu(self)
        self.rename_act = QAction("修改用户名…", self)
        self.user_menu.addAction(self.rename_act)
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
        self.compile_act.triggered.connect(self._compile_only)
        self.run_act.triggered.connect(self._run_only)
        self.judge_act.triggered.connect(self._start_judge)
        self.interactive_act.triggered.connect(self._interactive_run)
        self.datagen_act.triggered.connect(self._open_data_gen)
        self.stress_act.triggered.connect(self._open_stress)
        self.default_code_act.triggered.connect(self._edit_default_code)
        self.format_act.triggered.connect(self._format_current)
        self.problem_new_act.triggered.connect(self._problem_new)
        self.problem_reload_act.triggered.connect(self._problem_reload)
        self.problem_root_act.triggered.connect(self._problem_choose_root)
        self.pref_act.triggered.connect(self._open_settings)
        self.rename_act.triggered.connect(self._change_username)
        self.logout_act.triggered.connect(self._logout)
        self.run_tool_btn.clicked.connect(self._compile_run)
        self.format_tool_btn.clicked.connect(self._format_current)
        self.datagen_tool_btn.clicked.connect(self._open_data_gen)
        self.judge_tool_btn.clicked.connect(self._start_judge)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)
        self.tab_widget.currentChanged.connect(self._tab_changed)
        self.compiler_combo.currentTextChanged.connect(self._compiler_changed)
        self.import_btn.clicked.connect(self._import_test_folder)
        self.clear_tests_btn.clicked.connect(self._clear_tests)
        self.judge_btn.clicked.connect(self._start_judge)
        # 评测控制（仅连接一次，避免每次评测重复绑定）
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        self.resume_btn.clicked.connect(self._on_resume_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.add_quick_btn.clicked.connect(self._add_quick_test)
        self.run_quick_btn.clicked.connect(self._run_quick_test)
        self.auth_btn.clicked.connect(self._open_login)
        # 令牌滑动续签：每 30 分钟调一次 /me，剩余寿命不足 12h 时服务端会下发
        # 新 token（响应里带回），_refresh_token 负责写回本地 —— 否则 24h 后必掉线。
        # 定时器在 _start_chat（已登录）时启动、_stop_chat 时停止。
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30 * 60 * 1000)
        self._refresh_timer.timeout.connect(self._refresh_token)

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
        return '#include <bits/stdc++.h>\nusing namespace std;\nsigned main() {\n    return 0;\n}'

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
        # 未显式指定时，按「运行方式」决定：external=外部黑窗（默认），capture=内置捕获
        if in_terminal is False and self.run_mode == "external":
            in_terminal = True
        self.console.clear()
        self._log("正在编译...", "info")
        self.run_tool_btn.setEnabled(False)
        self.worker = CompileRunWorker(self.compiler, code, run_in_term=in_terminal)
        self.worker.compile_done.connect(self._on_compile_done)
        self.worker.run_done.connect(self._on_run_done)
        self.worker.finished.connect(lambda: self.run_tool_btn.setEnabled(True))
        self.worker.start()

    def _compile_only(self) -> None:
        """仅编译（F9）：构建但不运行，便于检查编译错误。"""
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
        self.worker = CompileRunWorker(self.compiler, code, compile_only=True)
        self.worker.compile_done.connect(self._on_compile_done)
        self.worker.finished.connect(lambda: self.run_tool_btn.setEnabled(True))
        self.worker.start()

    def _run_only(self) -> None:
        """运行（F10）：若有最近编译产物则直接运行黑窗；否则先编译再运行。"""
        editor = self._get_current_editor()
        if editor is None:
            return
        code = editor.toPlainText()
        if not code.strip():
            QMessageBox.warning(self, "警告", "代码为空")
            return
        last = getattr(self, "_last_exe", None)
        if last and os.path.exists(last):
            self._log("运行已编译的程序...", "info")
            self.compiler.run_in_terminal(last)
        else:
            self._compile_run()

    def _setup_shortcuts(self) -> None:
        """注册 Dev-C++ 风格快捷键：F9 编译 / F10 运行 / F11 编译并运行 / Ctrl+Shift+A 格式化。"""
        def add(key: str, slot) -> None:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(slot)

        add("F9", self._compile_only)
        add("F10", self._run_only)
        add("F11", self._compile_run)
        add("Ctrl+Shift+A", self._format_current)

    def _on_compile_done(self, result: CompileResult) -> None:
        if result.success:
            self._last_exe = result.exe_path
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
        self.import_btn.setEnabled(False)
        self.clear_tests_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.test_list.clear()
        for tc in tab.tests:
            item = QTreeWidgetItem([tc.name, "⏳", "-", "-"])
            self.test_list.addTopLevelItem(item)
        self.judge_summary.setText("评测中...")
        self._judge_total = len(tab.tests)
        # 每次评测使用独立的控制对象（避免上次残留的 stop/pause 状态）
        control = JudgeControl()
        self.worker = JudgeWorker(
            self.judge_engine, code, tab.tests,
            self.time_limit_spin.value(), self.memory_limit_spin.value(),
            ignore_case=self.ignore_case_check.isChecked(), control=control,
        )
        self.worker.result_ready.connect(self._on_case_done)
        self.worker.finished.connect(self._on_judge_done)
        self.worker.finished.connect(self._reset_judge_buttons)
        self.worker.start()

    def _on_pause_clicked(self) -> None:
        if getattr(self, "worker", None) and self.worker.isRunning():
            self.worker.request_pause()
            self.pause_btn.setEnabled(False)
            self.resume_btn.setEnabled(True)

    def _on_resume_clicked(self) -> None:
        if getattr(self, "worker", None) and self.worker.isRunning():
            self.worker.request_resume()
            self.resume_btn.setEnabled(False)
            self.pause_btn.setEnabled(True)

    def _on_stop_clicked(self) -> None:
        if getattr(self, "worker", None) and self.worker.isRunning():
            self.worker.request_stop()
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.resume_btn.setEnabled(False)

    def _reset_judge_buttons(self) -> None:
        """评测结束后复位按钮状态。"""
        self.judge_btn.setEnabled(True)
        self.judge_tool_btn.setEnabled(True)
        self.import_btn.setEnabled(True)
        self.clear_tests_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)

    def _on_judge_done(self, result: JudgeResult) -> None:
        if result.overall_status == "compile_error":
            self._log("编译错误 (CE)", "error")
            self._log(result.compile_result.output or "", "error")
            self.judge_summary.setText("编译错误 (CE)")
            self.judge_summary.setStyleSheet(self._judge_summary_style("#f87171"))
            for i in range(self.test_list.topLevelItemCount()):
                item = self.test_list.topLevelItem(i)
                if item is not None:
                    item.setText(1, "CE")
            return

        ac = 0
        for i, res in enumerate(result.results):
            if res.status == "AC":
                ac += 1
            item = self.test_list.topLevelItem(i)
            if item is not None:
                self._paint_result_item(item, res)

        # 因「停止」而遗漏的用例标为「已停止」
        if result.stopped:
            for i in range(len(result.results), self.test_list.topLevelItemCount()):
                item = self.test_list.topLevelItem(i)
                if item is not None:
                    item.setText(1, "已停止")
                    item.setForeground(1, QBrush(QColor("#94a9c9")))

        total_run = len(result.results)
        rate = ac / total_run * 100 if total_run else 0
        if result.stopped:
            self.judge_summary.setText(f"已停止（AC {ac}/{total_run}，共 {self._judge_total}）")
            self.judge_summary.setStyleSheet(self._judge_summary_style("#94a9c9", bold=True))
            self._log(f"评测已停止：完成 {total_run}/{self._judge_total}，AC {ac}", "warning")
        else:
            self.judge_summary.setText(f"AC {ac}/{total_run} ({rate:.1f}%)")
            if ac == total_run:
                self.judge_summary.setStyleSheet(self._judge_summary_style("#34d399", bold=True))
                self._log(f"全部通过! AC {ac}/{total_run}", "success")
            else:
                self.judge_summary.setStyleSheet(self._judge_summary_style("#fb923c", bold=True))
                self._log(f"通过 {ac}/{total_run}", "warning")

    def _paint_result_item(self, item: "QTreeWidgetItem", result: "TestResult") -> None:
        """把一个用例结果刷到表格行（状态 / 用时 / 内存 / 配色 / 悬浮提示）。"""
        item.setText(1, result.status)
        item.setText(2, f"{result.time:.3f}s")
        item.setText(3, f"{result.memory_mb:.1f}MB")
        color = {
            "AC": "#34d399", "WA": "#f87171", "TLE": "#fb923c",
            "MLE": "#f472b6", "RE": "#f87171",
        }.get(result.status, "#e8edf5")
        item.setForeground(1, QBrush(QColor(color)))
        item.setToolTip(0, f"输入:\n{result.expected[:500]}\n\n输出:\n{result.stdout[:500]}\n\n错误:\n{result.stderr[:500]}")

    def _on_case_done(self, result: "TestResult", index: int) -> None:
        """每个用例跑完即增量刷新对应行，实现评测结果『依次显示』。"""
        if not (0 <= index < self.test_list.topLevelItemCount()):
            return
        item = self.test_list.topLevelItem(index)
        if item is not None:
            self._paint_result_item(item, result)
        total = getattr(self, "_judge_total", 0) or (index + 1)
        self.judge_summary.setText(f"评测中... ({index + 1}/{total})")

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
        dlg.setStyleSheet(dialog_qss(self._ui))
        layout = QVBoxLayout(dlg)
        edit = QPlainTextEdit()
        edit.setPlainText(self.settings.value("default_code", self._builtin_default(), type=str))
        edit.setStyleSheet(plain_edit_qss(self._ui))
        layout.addWidget(edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(lambda: (self.settings.setValue("default_code", edit.toPlainText()), dlg.accept()))
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        dlg.exec()

    def _format_current(self) -> None:
        """对当前编辑器代码执行格式化（保留光标位置）。"""
        editor = self._get_current_editor()
        if editor is None:
            return
        code = editor.toPlainText()
        if not code.strip():
            return
        formatted = format_cpp(code)
        if formatted == code:
            self._log("已是最优格式", "info")
            return
        # 记录光标绝对偏移，格式化后尽量还原
        cur = editor.textCursor()
        pos = cur.position()
        pos = min(pos, len(formatted))
        editor.blockSignals(True)
        editor.setPlainText(formatted)
        editor.blockSignals(False)
        new_cur = editor.textCursor()
        new_cur.setPosition(pos)
        editor.setTextCursor(new_cur)
        tab = self._current_tab()
        if tab:
            tab.content = formatted
        self._log("已格式化代码", "success")

    def _interactive_run(self) -> None:
        """编译后用管道交互式运行，支持运行时输入。"""
        tab = self._current_tab()
        editor = self._get_current_editor()
        if not tab or not editor:
            return
        code = editor.toPlainText()
        if not code.strip():
            QMessageBox.warning(self, "警告", "代码为空")
            return
        self._log("正在编译（交互运行）…", "info")
        result = self.compiler.compile(code)
        if not result.success:
            self._log("编译失败", "error")
            if result.output:
                self._log(result.output, "error")
            return
        self._log("编译成功，启动交互运行…", "success")
        dlg = InteractiveRunDialog(result.exe_path, self, bin_dir=self.compiler.bin_dir())
        dlg.show()

    def _open_data_gen(self) -> None:
        """打开数据生成面板（合并自 Data-Generator 的核心能力）。"""
        tab = self._current_tab()
        if tab is None:
            return
        dlg = DataGenDialog(self.compiler, self)
        dlg.imported.connect(lambda cases: self._on_data_imported(cases, tab))
        dlg.show()

    def _on_data_imported(self, cases: list, tab) -> None:
        for name, inp, exp in cases:
            tab.tests.append(TestCase(name=name, input=inp, expected=exp))
        self._render_tests()
        self._log(f"数据生成：已导入 {len(cases)} 组测试用例", "success")

    def _open_stress(self) -> None:
        """打开对拍器：当前编辑器代码自动填入「待测程序」."""
        editor = self._get_current_editor()
        code = editor.toPlainText() if editor is not None else ""
        dlg = StressDialog(self.compiler, code, self)
        dlg.show()

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

    # ---------- 设置：外观与编辑器主题 ----------
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        dlg.setStyleSheet(dialog_qss(ui_palette()))
        dlg.settings_applied.connect(self._apply_theme)
        dlg.exec()

    def _apply_compiler_options(self) -> None:
        """从 QSettings 读取编译器路径、标准与编译/连接指令并应用到 compiler 实例。"""
        self.compiler.set_compiler(get_compiler_cmd())
        self.compiler.set_std(get_cpp_std())
        self.compiler.set_compile_flags(get_compile_flags())
        self.compiler.set_link_flags(get_link_flags())
        self.run_mode = get_run_mode()  # external=外部黑窗（默认）；capture=内置捕获
        if not hasattr(self, "_last_exe"):
            self._last_exe = None

    def _judge_summary_style(self, color: str, bold: bool = False) -> str:
        """评测结果条样式：背景用主题 selection 色（暗/亮主题都协调），不再写死深色。"""
        return (
            f"color:{color};padding:8px;background:{self._ui['selection']};"
            f"border-radius:6px;{'font-weight:bold;' if bold else ''}"
        )

    def _apply_theme(self) -> None:
        """重新读取主题并应用到主窗口与所有已打开的代码编辑器（设置变更时调用）。"""
        self._ui = ui_palette()
        self.setStyleSheet(main_window_global_qss(self._ui))
        self.tab_widget.setStyleSheet(tab_qss(self._ui))
        self._apply_compiler_options()
        cmd = get_compiler_cmd()
        if self.compiler_combo.currentText() != cmd:
            self.compiler_combo.blockSignals(True)
            self.compiler_combo.setCurrentText(cmd)
            self.compiler_combo.blockSignals(False)
            self.status_compiler.setText(cmd)
        for grp in self.findChildren(QGroupBox):
            grp.setStyleSheet(group_qss(self._ui))
        self.console.setStyleSheet(console_qss(self._ui))
        self.quick_input.setStyleSheet(plain_edit_qss(self._ui))
        self.quick_expected.setStyleSheet(plain_edit_qss(self._ui))
        self.test_list.setStyleSheet(tree_qss(self._ui))
        self.import_btn.setStyleSheet(button_neutral_qss(self._ui))
        self.judge_btn.setStyleSheet(button_green_qss(self._ui))
        self.clear_tests_btn.setStyleSheet(button_danger_qss(self._ui))
        self.run_quick_btn.setStyleSheet(button_green_qss(self._ui))
        self.run_tool_btn.setStyleSheet(button_green_qss(self._ui))
        self.judge_tool_btn.setStyleSheet(button_neutral_qss(self._ui))
        self.auth_btn.setStyleSheet(button_neutral_qss(self._ui))
        # 以下按钮在构建时上了暗色样式表，主题切换时必须重新套用，
        # 否则会残留旧主题的黑色背景（「有些按钮仍是黑色的」）。
        self.pause_btn.setStyleSheet(button_neutral_qss(self._ui))
        self.resume_btn.setStyleSheet(button_neutral_qss(self._ui))
        self.stop_btn.setStyleSheet(button_danger_qss(self._ui))
        self.format_tool_btn.setStyleSheet(button_neutral_qss(self._ui))
        self.datagen_tool_btn.setStyleSheet(button_neutral_qss(self._ui))
        # 聊天面板（自建样式表，需主动刷新）
        if hasattr(self, "chat_dock") and self.chat_dock is not None:
            self.chat_dock.apply_theme()
        # 题目面板（同样自建样式表）
        if hasattr(self, "problem_dock") and self.problem_dock is not None:
            self.problem_dock.apply_theme()
        self.status_bar.setStyleSheet(statusbar_qss(self._ui))
        self.user_status_label.setStyleSheet(f"color: {self._ui['text3']};")
        tb = self.findChild(QToolBar)
        if tb is not None:
            tb.setStyleSheet(toolbar_qss(self._ui))
        for sp in self.findChildren(QSplitter):
            sp.setStyleSheet(splitter_qss(self._ui))
        # 代码编辑器主题（字体 + 配色）
        for i in range(self.tab_widget.count()):
            w = self.tab_widget.widget(i)
            if isinstance(w, CodeEditor):
                w.apply_theme()

    # ---------- 账号 / 登录（对接主页 auth-worker） ----------
    def _init_auth(self) -> None:
        self.auth_client = AuthClient()
        self.token = ""
        self.current_user = None
        # 聊天（对接 chat-worker.cyx012113.top，复用同一份登录 token）
        from .chat_client import ChatClient
        from .chat_panel import ChatDock
        self.chat_client = ChatClient()
        # 令牌失效（401）：清理凭证并引导重新登录（见 _on_chat_auth_expired）
        self.chat_client.auth_expired.connect(self._on_chat_auth_expired)
        self.chat_dock = ChatDock(self.chat_client, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.chat_dock)
        self.chat_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.chat_dock.setVisible(True)
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

    def _store_token(self, token: str) -> None:
        """把（可能被滑动续签刷新过的）token 写回本地凭证。"""
        if not token:
            return
        if token != self.token:
            self.token = token
        # 与登录时一致：仅当用户勾了「记住我」才持久化；没记住就只更新内存，
        # 重启后仍需重新登录（不擅自落盘）。
        if self.settings.value("auth_token", ""):
            self.settings.setValue("auth_token", token)

    def _on_token_validated(self, ok: bool, data) -> None:
        self.auth_btn.setEnabled(True)
        if ok and isinstance(data, dict):
            self.current_user = data
            # /me 滑动续签（剩余寿命不足 12h）会下发新 token，必须写回，否则 24h 后掉线
            fresh = data.get("token")
            if isinstance(fresh, str) and fresh:
                self._store_token(fresh)
            self._update_user_ui()
            self._start_chat()
            self._log(f"已恢复登录：{data.get('username', '')}", "success")
        else:
            self.token = ""
            self.current_user = None
            self.settings.remove("auth_token")
            self._stop_chat()
            self._update_user_ui()

    def _refresh_token(self) -> None:
        """周期性滑动续签：调 /me 捕获服务端下发的新 token（见 _on_token_validated）。"""
        if not self.token or not self.current_user:
            return
        self._validate_worker = TokenValidateWorker(self.auth_client, self.token)
        self._validate_worker.done.connect(self._on_token_validated)
        self._validate_worker.finished.connect(self._validate_worker.deleteLater)
        self._validate_worker.start()

    def _on_chat_auth_expired(self) -> None:
        """聊天轮询收到 401：令牌失效（过期 / 改密后 tv 递增 / 被吊销）。
        清理本地凭证、停掉轮询与续签定时器，提示重新登录。"""
        if not self.token:
            return
        self._stop_chat()
        self.token = ""
        self.current_user = None
        self.settings.remove("auth_token")
        self._update_user_ui()
        self._log("⚠️ 登录已失效（令牌过期或被吊销），请重新登录", "error")

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
        self._start_chat()
        self._log(f"已登录：{user.get('username', '')}", "success")

    def _change_username(self) -> None:
        """修改用户名（2026-10 主页新机制）：PUT /update-profile，服务端在用户名
        真变化时重签新 token —— 必须替换本地凭证并刷新聊天会话（房间名含用户名）。"""
        if not self.token or not self.current_user:
            return
        old_name = str(self.current_user.get("username") or "")
        dlg = QDialog(self)
        dlg.setWindowTitle("修改用户名")
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet(dialog_qss(ui_palette()))
        root = QVBoxLayout(dlg)
        root.setSpacing(10)
        root.setContentsMargins(18, 18, 18, 18)
        hint = QLabel(
            "3～50 位，仅限字母、数字、下划线、连字符。修改成功后需要重新登录聊天会话。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {ui_palette()['text2']}; font-size: 12px;")
        root.addWidget(hint)
        edit = QLineEdit(dlg)
        edit.setText(old_name)
        edit.setPlaceholderText("新用户名")
        root.addWidget(edit)
        err = QLabel("")
        err.setWordWrap(True)
        err.setStyleSheet("color: #f87171; font-size: 12px;")
        root.addWidget(err)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg,
        )
        root.addWidget(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = edit.text().strip()
        if not new_name or new_name == old_name:
            return

        class _RenameWorker(QThread):
            done = pyqtSignal(bool, object)

            def __init__(self, client: AuthClient, token: str, username: str) -> None:
                super().__init__()
                self.client = client
                self.token = token
                self.username = username

            def run(self) -> None:
                ok, data = self.client.update_profile(self.token, username=self.username)
                self.done.emit(ok, data)

        self.auth_btn.setEnabled(False)
        self.user_status_label.setText("正在修改用户名...")
        self._rename_worker = _RenameWorker(self.auth_client, self.token, new_name)
        self._rename_worker.done.connect(self._on_rename_done)
        self._rename_worker.finished.connect(self._rename_worker.deleteLater)
        self._rename_worker.start()

    def _on_rename_done(self, ok: bool, data) -> None:
        self.auth_btn.setEnabled(True)
        if not ok or not isinstance(data, dict):
            msg = str((data or {}).get("error", "修改失败") if isinstance(data, dict) else data)
            self._log(f"❌ 修改用户名失败：{msg}", "error")
            self._update_user_ui()
            return
        # 改名成功：服务端重签的新 token 必须写回（旧 token 的 username 快照已过期）
        fresh = data.get("token")
        if isinstance(fresh, str) and fresh:
            self._store_token(fresh)
        new_name = str(data.get("username") or self.current_user.get("username") or "")
        self.current_user = dict(self.current_user or {})
        self.current_user["username"] = new_name
        self._update_user_ui()
        # 私聊房间名 dm:<a>~<b> 含用户名：换凭证重连，避免旧房间名对不上
        self._stop_chat()
        self._start_chat()
        self._log(f"✅ 用户名已修改为：{new_name}", "success")

    def _logout(self) -> None:
        self._stop_chat()
        self.token = ""
        self.current_user = None
        self.settings.remove("auth_token")
        self.auth_btn.setMenu(None)
        self._update_user_ui()
        self._log("已退出登录", "info")

    # ---------- 题目管理（本地题库） ----------
    def _problem_root(self) -> Path:
        saved = self.settings.value("problems_root", "")
        return Path(saved) if saved else default_root()

    def _init_problems(self) -> None:
        self.problem_store = ProblemStore(self._problem_root())
        self._rebuild_problem_dock()

    def _rebuild_problem_dock(self) -> None:
        """（重）建题目停靠面板并接线；题库目录变化时整体重建。"""
        old = getattr(self, "problem_dock", None)
        if old is not None:
            try:
                self.removeDockWidget(old)
                old.deleteLater()
            except Exception:
                pass
        dock = ProblemDock(self.problem_store, self)
        dock.new_requested.connect(self._problem_new)
        dock.edit_requested.connect(self._problem_edit)
        dock.delete_requested.connect(self._problem_delete)
        dock.load_samples_requested.connect(self._problem_load_samples)
        dock.save_solution_requested.connect(self._problem_save_solution)
        dock.open_solution_requested.connect(self._problem_open_solution)
        dock.open_dir_requested.connect(self._problem_open_dir)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.problem_dock = dock

    def _problem_new(self) -> None:
        dlg = ProblemEditDialog(self, edit=None)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        info = self.problem_store.create(
            v["title"],
            source=v["source"],
            tags=v["tags"],
            time_limit=v["time_limit"],
            memory_limit=v["memory_limit"],
            description=v["description"],
        )
        self.problem_dock.reload()
        self._log(f"已新建题目：{info.title}（{info.pid}）", "success")

    def _problem_edit(self, info: ProblemInfo) -> None:
        dlg = ProblemEditDialog(self, edit=info)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        info.title = v["title"] or info.title
        info.source = v["source"]
        info.tags = v["tags"]
        info.time_limit = v["time_limit"]
        info.memory_limit = v["memory_limit"]
        info.description = v["description"]
        self.problem_store.update(info)
        self.problem_dock.reload()
        self._log(f"已更新题目：{info.title}", "success")

    def _problem_delete(self, info: ProblemInfo) -> None:
        ret = QMessageBox.question(
            self, "删除题目",
            f"确定删除「{info.title}」？\n该题目的数据与解答将一并删除，不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.problem_store.delete(info.pid)
        self.problem_dock.reload()
        self._log(f"已删除题目：{info.title}", "info")

    def _problem_reload(self) -> None:
        self.problem_dock.reload()
        self._log("题库已刷新", "info")

    def _problem_choose_root(self) -> None:
        cur = str(self._problem_root())
        path = QFileDialog.getExistingDirectory(self, "选择题库目录", cur)
        if not path:
            return
        self.settings.setValue("problems_root", path)
        self.problem_store = ProblemStore(Path(path))
        self._rebuild_problem_dock()
        self._log(f"题库目录已切换：{path}", "success")

    def _problem_load_samples(self, info: ProblemInfo) -> None:
        """把题目 data/ 下的样例加载为当前标签的测试用例，并直接开跑评测。"""
        tab = self._current_tab()
        if not tab:
            QMessageBox.information(self, "提示", "请先打开一个代码标签页")
            return
        cases = self.problem_store.sample_cases(info)
        if not cases:
            QMessageBox.information(
                self, "提示", f"「{info.title}」还没有配对完整的样例数据（data/*.in + *.out）"
            )
            return
        tab.tests.extend(cases)
        self._render_tests()
        self._log(f"已加载「{info.title}」{len(cases)} 组样例，开始评测", "info")
        self._start_judge()

    def _problem_save_solution(self, info: ProblemInfo) -> None:
        editor = self._get_current_editor()
        if not editor:
            QMessageBox.information(self, "提示", "请先打开解答代码")
            return
        code = editor.toPlainText()
        if not code.strip():
            QMessageBox.information(self, "提示", "当前代码为空")
            return
        path = self.problem_store.save_solution(info, code)
        self.problem_dock.reload()
        self._log(f"已保存解答：{path}", "success")

    def _problem_open_solution(self, info: ProblemInfo) -> None:
        path = info.solution_path()
        if not path.is_file():
            QMessageBox.information(self, "提示", "该题目还没有保存过解答")
            return
        content = read_file_text(path)
        tab = FileTab(name=path.name, path=str(path), content=content)
        self.tabs.append(tab)
        editor = CodeEditor()
        editor.setPlainText(content)
        editor.textChanged.connect(self._on_text_changed)
        editor.cursorPositionChanged.connect(self._update_cursor_status)
        self.tab_widget.addTab(editor, path.name)
        self.tab_widget.setCurrentIndex(self.tab_widget.count() - 1)
        self._update_status()

    def _problem_open_dir(self, info: ProblemInfo) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(info.dir or self.problem_store.root)))

    # ---------- 聊天（对接 chat-worker） ----------
    def _start_chat(self) -> None:
        if not self.token or not self.current_user:
            return
        self.chat_client.set_credentials(
            self.token,
            self.current_user.get("username", ""),
            self.current_user.get("is_op", False),
        )
        self.chat_client.start()
        self.chat_dock.set_logged_in(
            True,
            self.current_user.get("username", ""),
            self.current_user.get("is_op", False),
        )
        self.chat_dock.setVisible(True)
        # 登录态稳定后启动令牌滑动续签定时器（30 分钟一次）
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.start()

    def _stop_chat(self) -> None:
        if hasattr(self, "chat_client"):
            self.chat_client.stop()
            self.chat_client.clear_credentials()
        if hasattr(self, "chat_dock"):
            self.chat_dock.set_logged_in(False)
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()

    def _update_user_ui(self) -> None:
        if self.current_user:
            name = self.current_user.get("username", "用户")
            tag = self.current_user.get("tag") or ("[管理员]" if self.current_user.get("is_op") else "")
            # 客户端绑定签名状态（2026-10 新机制）：令牌 sid 与本机私钥匹配时，
            # 所有请求都带签名头 —— 令牌被偷也无法在别的机器上冒用。
            sig_mark = ""
            try:
                sid = token_sid(getattr(self, "token", "") or "")
                if sid and sid == default_signer().bound_sid():
                    sig_mark = " 🔒签名保护"
            except Exception:
                pass
            self.auth_btn.setText(f"👤 {name}")
            self.auth_btn.setMenu(self.user_menu)
            self.user_status_label.setText(f"已登录：{name} {tag}{sig_mark}".rstrip())
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
