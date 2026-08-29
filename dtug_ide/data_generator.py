"""数据生成面板（合并自隔壁项目 Data-Generator 的核心能力）。

把 Data-Generator 的 C++11 单头库 ``random.h`` 随 IDE 打包，用户可编写基于
``pg::Random`` 的生成器（或任何向 stdout 输出数据的程序），一键生成多组测试
数据；若再提供一份「标程」，可顺带产出对应的 ``.out``。生成的样例可一键导入为
当前文件的评测用例。

生成器每轮运行复用 Compiler 的沙箱执行（无黑窗、带时限）。
"""

from __future__ import annotations

import sys
import shutil
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QPlainTextEdit, QProgressBar, QSplitter, QVBoxLayout,
)

from .compiler import Compiler
from .editor import CodeEditor
from .theme import (
    button_green_qss, button_neutral_qss, console_qss, dialog_qss, group_qss,
    plain_edit_qss, ui_palette,
)

def _resources_dir() -> Path:
    """定位 resources 目录。

    开发模式用 ``__file__`` 相对路径；冻结模式（PyInstaller onedir）下模块被
    打进 exe 内的 PYZ 归档，``__file__`` 不再是真实磁盘路径，需改用 bootloader
    解包出来的 ``sys._MEIPASS``。
    """
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "dtug_ide" / "resources"
    return Path(__file__).resolve().parent / "resources"


RESOURCES = _resources_dir()
RANDOM_H = RESOURCES / "random.h"

GENERATOR_TEMPLATE = """\
// 数据生成器模板（基于 Data-Generator 的 random.h）
// 向 stdout 输出一组测试数据；面板会运行本程序 N 次，每次输出保存为 gen_i.in
#include <iostream>
#include "random.h"
using namespace std;

int main() {
    pg::Random r;            // 固定种子可写 pg::Random r(123);
    int n = r.integer(1, 10);
    cout << n << '\\n';
    for (int i = 0; i < n; i++) {
        if (i) cout << ' ';
        cout << r.integer(-1000, 1000);
    }
    cout << '\\n';
    return 0;
}
"""

SOLUTION_TEMPLATE = """\
// 可选：标程。提供后，面板会用它对每个 gen_i.in 运行，产出 gen_i.out
#include <iostream>
using namespace std;
int main() {
    int n; cin >> n;
    long long sum = 0, x;
    while (n--) { cin >> x; sum += x; }
    cout << sum << '\\n';
    return 0;
}
"""


class _GenWorker(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(list, str)  # list of (name, input_text, expected_text), log

    def __init__(self, compiler: Compiler, gen_code: str, sol_code: str, count: int, parent=None) -> None:
        super().__init__(parent)
        self.compiler = compiler
        self.gen_code = gen_code
        self.sol_code = sol_code
        self.count = max(1, min(count, 200))

    def run(self) -> None:
        work = Path(tempfile.mkdtemp(prefix="dtug_datagen_"))
        try:
            # 把 random.h 放进编译器的临时目录，确保生成器 #include "random.h" 能被 g++ 找到
            # （compiler.compile() 会把源文件写到编译器各自的临时目录，而非本 work 目录）
            if RANDOM_H.exists():
                shutil.copy2(RANDOM_H, self.compiler.temp_dir / "random.h")
            gen_src = work / "generator.cpp"
            gen_src.write_text(self.gen_code, encoding="utf-8")
            self.progress.emit("编译生成器…")
            cres = self.compiler.compile(self.gen_code)
            if not cres.success:
                self.done.emit([], "生成器编译失败:\n" + (cres.output or cres.error or ""))
                return
            gen_exe = cres.exe_path

            sol_exe: Optional[str] = None
            have_sol = self.sol_code.strip() and "int main" in self.sol_code
            if have_sol:
                self.progress.emit("编译标程…")
                sres = self.compiler.compile(self.sol_code)
                if not sres.success:
                    self.done.emit([], "标程编译失败（将只生成 .in）:\n" + (sres.output or sres.error or ""))
                    have_sol = False
                else:
                    sol_exe = sres.exe_path

            cases: List[Tuple[str, str, str]] = []
            for i in range(1, self.count + 1):
                self.progress.emit(f"生成第 {i}/{self.count} 组…")
                run = self.compiler.run_sandboxed(gen_exe or "", input_data="", time_limit=5.0, memory_limit_mb=512.0, working_dir=work)
                if not run.success and run.status:
                    self.done.emit([], f"第 {i} 组生成器运行失败 ({run.status}):\n{run.stderr}")
                    return
                in_text = run.stdout
                (work / f"gen_{i}.in").write_text(in_text, encoding="utf-8")
                expected = ""
                if have_sol and sol_exe:
                    srun = self.compiler.run_sandboxed(sol_exe, input_data=in_text, time_limit=5.0, memory_limit_mb=512.0, working_dir=work)
                    expected = srun.stdout if srun.success or srun.stdout else srun.stderr
                    (work / f"gen_{i}.out").write_text(expected, encoding="utf-8")
                cases.append((f"gen{i}", in_text, expected))
                self.compiler.cleanup(gen_exe)
                if sol_exe:
                    self.compiler.cleanup(sol_exe)
            self.done.emit(cases, f"成功生成 {len(cases)} 组测试数据。")
        except Exception as e:  # noqa: BLE001
            self.done.emit([], f"生成异常: {e}")
        finally:
            shutil.rmtree(work, ignore_errors=True)


class DataGenDialog(QDialog):
    """数据生成对话框。"""

    imported = pyqtSignal(list)  # list of (name, input, expected)

    def __init__(self, compiler: Compiler, parent=None) -> None:
        super().__init__(parent)
        self.compiler = compiler
        self._cases: List[Tuple[str, str, str]] = []
        self.setWindowTitle("数据生成 - DTUG IDE")
        self.resize(960, 640)
        self.setStyleSheet(dialog_qss(ui_palette()))
        self._build_ui()

    def _build_ui(self) -> None:
        p = ui_palette()
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        splitter = QSplitter()
        self.gen_edit = CodeEditor()
        self.gen_edit.setPlainText(GENERATOR_TEMPLATE)
        self.sol_edit = CodeEditor()
        self.sol_edit.setPlainText(SOLUTION_TEMPLATE)
        splitter.addWidget(self._wrap(self.gen_edit, "生成器 (generator.cpp)"))
        splitter.addWidget(self._wrap(self.sol_edit, "标程（可选，产出 .out）"))
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("组数:"))
        self.count_spin = QLineEdit("10")
        self.count_spin.setFixedWidth(60)
        self.count_spin.setStyleSheet(plain_edit_qss(p))
        ctrl.addWidget(self.count_spin)
        self.gen_btn = QPushButton("🎲 生成数据")
        self.gen_btn.setStyleSheet(button_green_qss(p))
        self.gen_btn.clicked.connect(self._generate)
        ctrl.addWidget(self.gen_btn)
        self.import_btn = QPushButton("⬇ 导入为测试用例")
        self.import_btn.setStyleSheet(button_neutral_qss(p))
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._import)
        ctrl.addWidget(self.import_btn)
        ctrl.addStretch()
        self.close_btn = QPushButton("关闭")
        self.close_btn.setStyleSheet(button_neutral_qss(p))
        self.close_btn.clicked.connect(self.accept)
        ctrl.addWidget(self.close_btn)
        root.addLayout(ctrl)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setStyleSheet(console_qss(p))
        self.log.setFont(QFont("Consolas", 10))
        root.addWidget(self.log, stretch=1)

    def _wrap(self, editor: CodeEditor, title: str) -> QGroupBox:
        box = QGroupBox(title)
        box.setStyleSheet(group_qss(ui_palette()))
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.addWidget(editor)
        return box

    def _generate(self) -> None:
        try:
            count = int(self.count_spin.text().strip() or "10")
        except ValueError:
            count = 10
        self.gen_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.progress.setRange(0, 0)
        self._worker = _GenWorker(self.compiler, self.gen_edit.toPlainText(), self.sol_edit.toPlainText(), count)
        self._worker.progress.connect(self.log.appendPlainText)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, cases: list, log: str) -> None:
        self._cases = cases
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.gen_btn.setEnabled(True)
        self.log.appendPlainText(log)
        if cases:
            self.import_btn.setEnabled(True)

    def _import(self) -> None:
        if self._cases:
            self.imported.emit(self._cases)
            self.log.appendPlainText(f"已导入 {len(self._cases)} 组为测试用例。")
