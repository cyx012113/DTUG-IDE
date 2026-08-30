"""设置对话框：调整代码字体、字号、编辑器主题、界面主题与 C++ 工具链。

所有改动通过 QSettings 持久化（见 dtug_ide.theme），并实时 emit settings_applied
让主窗口重新应用主题（包括已打开的各个代码编辑器）与工具链配置。
"""

from __future__ import annotations

import shutil
from typing import List, Optional

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout,
)

from .theme import (
    dialog_qss, editor_themes, get_compiler_cmd, get_cpp_std, get_compile_flags,
    get_editor_font, get_editor_theme, get_link_flags, get_run_mode, get_ui_theme,
    set_compiler_cmd, set_compile_flags, set_cpp_std, set_editor_font, set_editor_theme,
    set_link_flags, set_run_mode, set_ui_theme, tab_qss, ui_palette, ui_themes,
)
from .toolchain import (
    bundled_gpp, copy_toolchain, detect_cpp_standards, discover_compilers, gpp_version,
)


class StdDetectWorker(QThread):
    """后台探测某编译器支持的 C++ 标准列表，避免界面卡顿。"""

    done = pyqtSignal(list)

    def __init__(self, gpp: str) -> None:
        super().__init__()
        self.gpp = gpp

    def run(self) -> None:
        try:
            standards = detect_cpp_standards(self.gpp)
        except Exception:
            standards = []
        self.done.emit(standards)


class ToolchainCopyWorker(QThread):
    """后台把一份 MinGW64 工具链复制到内置 resources 目录。"""

    finished = pyqtSignal(bool, str)

    def __init__(self, src_root: str, dst_root: str) -> None:
        super().__init__()
        self.src_root = src_root
        self.dst_root = dst_root

    def run(self) -> None:
        try:
            copy_toolchain(self.src_root, self.dst_root)
            self.finished.emit(True, "已捆绑内置工具链")
        except Exception as e:
            self.finished.emit(False, f"捆绑失败：{e}")


class SettingsDialog(QDialog):
    """设置对话框；应用即生效并持久化。"""

    settings_applied = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置 - DTUG IDE")
        self.setMinimumWidth(460)
        self._detect_worker: Optional[StdDetectWorker] = None
        self._copy_worker: Optional[ToolchainCopyWorker] = None
        self._build_ui()
        self._load_current()
        self._apply_self_style()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(14)
        root.setContentsMargins(18, 18, 18, 18)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("settingsTabs")
        root.addWidget(self.tabs)

        # ---- 标签页 1：外观（界面主题）----
        appearance = QWidget()
        ap_layout = QVBoxLayout(appearance)
        ap_layout.setContentsMargins(8, 12, 8, 8)
        ap_group = QGroupBox("界面主题")
        ap_form = QFormLayout(ap_group)
        ap_form.setSpacing(10)
        self.ui_theme_combo = QComboBox()
        self.ui_theme_combo.addItems(ui_themes())
        # 切换界面主题时联动编辑器配色主题（详见 _on_ui_theme_changed）
        self.ui_theme_combo.currentTextChanged.connect(self._on_ui_theme_changed)
        ap_form.addRow("界面主题", self.ui_theme_combo)
        ap_layout.addWidget(ap_group)
        ap_layout.addStretch(1)
        self.tabs.addTab(appearance, "外观")

        # ---- 标签页 2：代码编辑器 ----
        editor = QWidget()
        ed_layout = QVBoxLayout(editor)
        ed_layout.setContentsMargins(8, 12, 8, 8)
        ed_group = QGroupBox("代码编辑器")
        ed_form = QFormLayout(ed_group)
        ed_form.setSpacing(10)

        self.font_combo = QComboBox()
        self.font_combo.addItems(self._monospace_fonts())
        self.font_combo.setEditable(False)
        self.font_combo.currentTextChanged.connect(self._on_any_change)
        ed_form.addRow("字体", self.font_combo)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(8, 40)
        self.size_spin.valueChanged.connect(self._on_any_change)
        ed_form.addRow("字号", self.size_spin)

        self.editor_theme_combo = QComboBox()
        self.editor_theme_combo.addItems(editor_themes())
        self.editor_theme_combo.currentTextChanged.connect(self._on_any_change)
        ed_form.addRow("配色主题", self.editor_theme_combo)
        ed_layout.addWidget(ed_group)
        hint = QLabel("提示：Ctrl + 滚轮可在编辑器中临时缩放字号。")
        hint.setObjectName("hint")
        ed_layout.addWidget(hint)
        ed_layout.addStretch(1)
        self.tabs.addTab(editor, "编辑器")

        # ---- 标签页 3：编译器（C++ 工具链，独立于外观/编辑器）----
        build = QWidget()
        b_layout = QVBoxLayout(build)
        b_layout.setContentsMargins(8, 12, 8, 8)
        b_group = QGroupBox("C++ 工具链")
        b_form = QFormLayout(b_group)
        b_form.setSpacing(10)

        # 编译器选择（可手动编辑路径，或「浏览」/「扫描环境变量」）
        self.compiler_combo = QComboBox()
        self.compiler_combo.setEditable(True)
        self.compiler_combo.addItems(discover_compilers())
        self.compiler_combo.currentTextChanged.connect(self._on_compiler_changed)
        b_form.addRow("编译器", self.compiler_combo)

        compiler_row = QHBoxLayout()
        self.browse_btn = QPushButton("浏览…")
        self.browse_btn.clicked.connect(self._browse_compiler)
        self.scan_btn = QPushButton("扫描环境变量")
        self.scan_btn.clicked.connect(self._scan_env_compilers)
        self.redetect_btn = QPushButton("重新检测版本")
        self.redetect_btn.clicked.connect(self._refresh_standards)
        compiler_row.addWidget(self.browse_btn)
        compiler_row.addWidget(self.scan_btn)
        compiler_row.addWidget(self.redetect_btn)
        b_form.addRow("编译器操作", compiler_row)

        self.compiler_ver_label = QLabel("")
        self.compiler_ver_label.setObjectName("hint")
        b_form.addRow("版本", self.compiler_ver_label)

        # C++ 标准
        self.cpp_std_combo = QComboBox()
        self.cpp_std_combo.currentTextChanged.connect(self._on_any_change)
        b_form.addRow("C++ 标准", self.cpp_std_combo)

        # 运行方式
        self.run_mode_combo = QComboBox()
        self.run_mode_combo.addItems(["外部黑窗（Dev-C++ 风格）", "内置捕获"])
        self.run_mode_combo.currentTextChanged.connect(self._on_any_change)
        b_form.addRow("运行方式", self.run_mode_combo)

        # 编译指令 / 连接指令
        self.compile_edit = QLineEdit()
        self.compile_edit.setPlaceholderText("-static-libgcc -O2")
        self.compile_edit.textChanged.connect(self._on_any_change)
        b_form.addRow("编译指令", self.compile_edit)
        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText("-static -lm")
        self.link_edit.textChanged.connect(self._on_any_change)
        b_form.addRow("连接器指令", self.link_edit)

        # 捆绑内置工具链
        self.bundle_btn = QPushButton("捆绑内置工具链（复制所选编译器到程序目录）")
        self.bundle_btn.clicked.connect(self._bundle_toolchain)
        b_form.addRow(self.bundle_btn)
        b_layout.addWidget(b_group)
        b_layout.addStretch(1)
        self.tabs.addTab(build, "编译器")

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _monospace_fonts(self) -> list[str]:
        """收集系统等宽字体（含首选回退字体），去重并排序。"""
        families = QFontDatabase.families()
        fixed: list[str] = []
        for fam in families:
            if QFontDatabase.isFixedPitch(fam):
                fixed.append(fam)
        preferred = ["Consolas", "JetBrains Mono", "Fira Code", "Cascadia Code", "Source Code Pro", "Menlo"]
        order: list[str] = []
        for f in preferred + fixed:
            if f not in order:
                order.append(f)
        return order

    def _load_current(self) -> None:
        # 加载时屏蔽信号：避免 setCurrentText 触发自动联动（界面↔编辑器主题）
        # 或持久化，覆盖用户已保存的偏好。
        self.ui_theme_combo.blockSignals(True)
        self.font_combo.blockSignals(True)
        self.editor_theme_combo.blockSignals(True)
        self.compiler_combo.blockSignals(True)
        self.run_mode_combo.blockSignals(True)
        self.size_spin.blockSignals(True)
        self.compile_edit.blockSignals(True)
        self.link_edit.blockSignals(True)
        try:
            self.ui_theme_combo.setCurrentText(get_ui_theme())
            family, size = get_editor_font()
            idx = self.font_combo.findText(family)
            if idx >= 0:
                self.font_combo.setCurrentIndex(idx)
            else:
                self.font_combo.setCurrentText(family)
            self.size_spin.setValue(size)
            self.editor_theme_combo.setCurrentText(get_editor_theme())
            # 工具链
            self.compiler_combo.setCurrentText(get_compiler_cmd())
            mode = get_run_mode()
            self.run_mode_combo.setCurrentIndex(0 if mode == "external" else 1)
            self.compile_edit.setText(get_compile_flags())
            self.link_edit.setText(get_link_flags())
        finally:
            self.ui_theme_combo.blockSignals(False)
            self.font_combo.blockSignals(False)
            self.editor_theme_combo.blockSignals(False)
            self.compiler_combo.blockSignals(False)
            self.run_mode_combo.blockSignals(False)
            self.size_spin.blockSignals(False)
            self.compile_edit.blockSignals(False)
            self.link_edit.blockSignals(False)
        # 编译器版本 / 标准探测（直接调用，不依赖信号）
        self._on_compiler_changed(get_compiler_cmd())

    def _on_compiler_changed(self, text: str) -> None:
        gpp = (text or "").strip()
        self.compiler_ver_label.setText(gpp_version(gpp) if gpp else "")
        self._refresh_standards()

    def _refresh_standards(self) -> None:
        gpp = (self.compiler_combo.currentText() or "").strip()
        if not gpp:
            return
        if self._detect_worker is not None and self._detect_worker.isRunning():
            self._detect_worker.quit()
        self._detect_worker = StdDetectWorker(gpp)
        self._detect_worker.done.connect(self._on_standards_detected)
        self._detect_worker.start()

    def _on_standards_detected(self, standards: List[str]) -> None:
        current = get_cpp_std()
        self.cpp_std_combo.blockSignals(True)
        self.cpp_std_combo.clear()
        if standards:
            self.cpp_std_combo.addItems(standards)
            if current in standards:
                self.cpp_std_combo.setCurrentText(current)
            else:
                # 默认回落到 c++14（若存在），否则取最高支持版本
                self.cpp_std_combo.setCurrentText(
                    "c++14" if "c++14" in standards else standards[-1]
                )
        else:
            self.cpp_std_combo.addItems(["c++14", "c++17", "c++20", "c++23"])
            if current:
                self.cpp_std_combo.setCurrentText(current)
        self.cpp_std_combo.blockSignals(False)

    def _browse_compiler(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 g++.exe", "", "编译器 (*.exe);;所有文件 (*)"
        )
        if path:
            self.compiler_combo.setCurrentText(path)
            self._on_compiler_changed(path)

    def _bundle_toolchain(self) -> None:
        gpp = (self.compiler_combo.currentText() or "").strip()
        if not gpp or not shutil.which(gpp) and not __import__("os").path.exists(gpp):
            return
        # 推断 mingw64 根目录：bin/g++.exe -> 上一级 bin 的上一级
        import os
        gpp_path = os.path.abspath(gpp)
        root = os.path.dirname(os.path.dirname(gpp_path))  # .../mingw64/bin/g++.exe -> .../mingw64
        # 目标：源码/开发目录下的 dtug_ide/resources/mingw64
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # dtug_ide
        dst = os.path.join(here, "resources", "mingw64")
        self.bundle_btn.setEnabled(False)
        self.bundle_btn.setText("正在复制…（可能需数十秒）")
        self._copy_worker = ToolchainCopyWorker(root, dst)
        self._copy_worker.finished.connect(self._on_bundle_finished)
        self._copy_worker.start()

    def _on_bundle_finished(self, ok: bool, msg: str) -> None:
        self.bundle_btn.setEnabled(True)
        self.bundle_btn.setText("捆绑内置工具链（复制所选编译器到程序目录）")
        self.compiler_ver_label.setText(msg)
        if ok:
            # 复制完成后，内置编译器应排到候选首位
            self.compiler_combo.blockSignals(True)
            self.compiler_combo.clear()
            self.compiler_combo.addItems(discover_compilers())
            self.compiler_combo.blockSignals(False)

    def _on_any_change(self, _=None) -> None:
        # 实时应用并持久化（预览）
        set_ui_theme(self.ui_theme_combo.currentText())
        set_editor_theme(self.editor_theme_combo.currentText())
        set_editor_font(self.font_combo.currentText(), self.size_spin.value())
        set_compiler_cmd(self.compiler_combo.currentText().strip())
        set_cpp_std(self.cpp_std_combo.currentText())
        set_run_mode("external" if self.run_mode_combo.currentIndex() == 0 else "capture")
        set_compile_flags(self.compile_edit.text())
        set_link_flags(self.link_edit.text())
        self._apply_self_style()
        self.settings_applied.emit()

    def _on_accept(self) -> None:
        set_ui_theme(self.ui_theme_combo.currentText())
        set_editor_theme(self.editor_theme_combo.currentText())
        set_editor_font(self.font_combo.currentText(), self.size_spin.value())
        set_compiler_cmd(self.compiler_combo.currentText().strip())
        set_cpp_std(self.cpp_std_combo.currentText())
        set_run_mode("external" if self.run_mode_combo.currentIndex() == 0 else "capture")
        set_compile_flags(self.compile_edit.text())
        set_link_flags(self.link_edit.text())
        self.settings_applied.emit()
        self.accept()

    def _on_ui_theme_changed(self, ui_name: str) -> None:
        """切换界面主题时，若当前编辑器配色主题与界面明暗不一致，自动同步到对应模式。

        例如切到「亮色」界面时，编辑器配色主题名会从「暗色 · Dracula」变为
        「亮色 · GitHub」（解决「切换成纯白主题后，编辑器主题名称仍未更改」）。
        仅当用户主动改界面主题时触发；加载已保存设置时因屏蔽信号不会触发。
        """
        ui_light = (ui_name == "亮色")
        cur_ed = self.editor_theme_combo.currentText()
        ed_light = cur_ed.startswith("亮色")
        if ui_light != ed_light:
            target = "亮色 · GitHub" if ui_light else "暗色 · Dracula"
            items = [self.editor_theme_combo.itemText(i) for i in range(self.editor_theme_combo.count())]
            if target in items:
                self.editor_theme_combo.blockSignals(True)
                self.editor_theme_combo.setCurrentText(target)
                self.editor_theme_combo.blockSignals(False)
        self._on_any_change()

    def _scan_env_compilers(self) -> None:
        """重新扫描系统（PATH 环境变量 + 标准安装位置），刷新编译器下拉列表。

        不再依赖硬编码路径：只要编译器在 PATH 中，或位于 PATH 目录同级
        mingw64/ucrt64/clang64 等环境，即可被发现；否则可用「浏览…」手动导入。
        """
        compilers = discover_compilers()
        current = self.compiler_combo.currentText().strip()
        self.compiler_combo.blockSignals(True)
        self.compiler_combo.clear()
        self.compiler_combo.addItems(compilers)
        idx = self.compiler_combo.findText(current)
        if idx >= 0:
            self.compiler_combo.setCurrentIndex(idx)
        elif current:
            self.compiler_combo.setCurrentText(current)
        self.compiler_combo.blockSignals(False)
        self.compiler_ver_label.setText(f"已扫描到 {len(compilers)} 个编译器")
        self._on_compiler_changed(self.compiler_combo.currentText())

    def _apply_self_style(self) -> None:
        p = ui_palette()
        self.setStyleSheet(dialog_qss(p))
        self.tabs.setStyleSheet(tab_qss(p))
        for w in self.findChildren(QLabel, "hint"):
            w.setStyleSheet(f"color: {p['text3']}; font-size: 12px;")
