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
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from .theme import (
    dialog_qss, editor_themes, get_compiler_cmd, get_cpp_std, get_compile_flags,
    get_editor_font, get_editor_theme, get_link_flags, get_run_mode, get_ui_theme,
    set_compiler_cmd, set_compile_flags, set_cpp_std, set_editor_font, set_editor_theme,
    set_link_flags, set_run_mode, set_ui_theme, ui_palette, ui_themes,
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

        # 外观（界面主题）
        appearance = QGroupBox("外观")
        ap_form = QFormLayout(appearance)
        ap_form.setSpacing(10)
        self.ui_theme_combo = QComboBox()
        self.ui_theme_combo.addItems(ui_themes())
        self.ui_theme_combo.currentTextChanged.connect(self._on_any_change)
        ap_form.addRow("界面主题", self.ui_theme_combo)
        root.addWidget(appearance)

        # 编辑器
        editor = QGroupBox("代码编辑器")
        ed_form = QFormLayout(editor)
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
        root.addWidget(editor)

        hint = QLabel("提示：Ctrl + 滚轮可在编辑器中临时缩放字号。")
        hint.setObjectName("hint")
        root.addWidget(hint)

        # 工具链（Dev-C++ 风格：编译器 / 标准 / 运行方式 / 编译指令）
        build = QGroupBox("C++ 工具链")
        b_form = QFormLayout(build)
        b_form.setSpacing(10)

        # 编译器选择
        self.compiler_combo = QComboBox()
        self.compiler_combo.setEditable(True)
        self.compiler_combo.addItems(discover_compilers())
        self.compiler_combo.currentTextChanged.connect(self._on_compiler_changed)
        b_form.addRow("编译器", self.compiler_combo)

        compiler_row = QHBoxLayout()
        self.browse_btn = QPushButton("浏览…")
        self.browse_btn.clicked.connect(self._browse_compiler)
        self.redetect_btn = QPushButton("重新检测版本")
        self.redetect_btn.clicked.connect(self._refresh_standards)
        compiler_row.addWidget(self.browse_btn)
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
        root.addWidget(build)

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
        self._on_compiler_changed(get_compiler_cmd())
        mode = get_run_mode()
        self.run_mode_combo.setCurrentIndex(0 if mode == "external" else 1)
        self.compile_edit.setText(get_compile_flags())
        self.link_edit.setText(get_link_flags())

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

    def _apply_self_style(self) -> None:
        p = ui_palette()
        self.setStyleSheet(dialog_qss(p))
        for w in self.findChildren(QLabel, "hint"):
            w.setStyleSheet(f"color: {p['text3']}; font-size: 12px;")
