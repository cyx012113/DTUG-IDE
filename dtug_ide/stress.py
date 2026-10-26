"""对拍器（Stress Test）：生成器 + 暴力 + 待测程序自动对拍。

典型用途：写完一道题但不确定对错，用暴力写份简单正确的标程，让生成器不停造
数据，两边输出不一致（或待测程序 RE / TLE）就地保存现场，极大缩短调试时间。

流程（``StressWorker``）：
  1. 分别编译 gen / brute / main 三个程序（任一闪退即中止并报告编译输出）；
  2. 循环 N 轮：跑 gen 得输入 → 分别喂给 brute 与 main → 比较输出；
  3. 不一致或 main 异常退出 ⇒ 保存 ``<输出目录>/fail_<轮次>/``
     （input.txt / brute.out / main.out / reason.txt），可继续做**行级最小化**；
  4. 「失败即停」开时找到一个就停；否则跑完所有轮数。

比较语义：仅去除尾部空白后精确比较（与绝大多数 OJ 的默认比对一致）。

复用现有基础设施：``Compiler.compile`` / ``Compiler.run_sandboxed``（沙箱 +
时限 + 内存上限），不引第三方依赖。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QSplitter, QVBoxLayout,
)

from .compiler import Compiler
from .editor import CodeEditor
from .theme import (
    button_danger_qss, button_green_qss, button_neutral_qss, console_qss,
    dialog_qss, group_qss, plain_edit_qss, ui_palette,
)

GEN_TEMPLATE = """\
// 对拍·数据生成器：每轮向 stdout 输出一组测试数据
// （可 #include "random.h" 用 pg::Random；固定种子可复现）
#include <iostream>
#include "random.h"
using namespace std;

int main() {
    pg::Random r;
    int n = r.integer(1, 12);
    cout << n << '\\n';
    for (int i = 0; i < n; i++) {
        if (i) cout << ' ';
        cout << r.integer(-100, 100);
    }
    cout << '\\n';
    return 0;
}
"""

BRUTE_TEMPLATE = """\
// 对拍·暴力标程：正确但可以慢（小数据下与生成器配套使用）
#include <iostream>
using namespace std;

int main() {
    int n; if (!(cin >> n)) return 0;
    long long sum = 0, x;
    for (int i = 0; i < n; i++) { cin >> x; sum += x; }
    cout << sum << '\\n';
    return 0;
}
"""

MAIN_TEMPLATE = """\
// 对拍·待测程序：把你要验证的代码放在这里
// （通常是从编辑器「载入当前代码」自动填入）
#include <iostream>
using namespace std;

int main() {
    int n; if (!(cin >> n)) return 0;
    long long sum = 0, x;
    for (int i = 0; i < n; i++) { cin >> x; sum += x; }
    cout << sum << '\\n';
    return 0;
}
"""


def _normalize(text: str) -> str:
    """比对前归一化：仅去尾部空白（保留行内差异）。"""
    return (text or "").rstrip()


class StressWorker(QThread):
    """后台对拍线程。"""

    progress = pyqtSignal(str)          # 过程日志
    round_done = pyqtSignal(int, bool)  # (轮次, 是否通过)
    found = pyqtSignal(dict)            # 找到反例：{dir, round, input, brute, main, reason, shrunk}
    finished = pyqtSignal(str)          # 总结

    def __init__(
        self,
        compiler: Compiler,
        gen_code: str,
        brute_code: str,
        main_code: str,
        *,
        rounds: int = 100,
        time_limit: float = 1.0,
        memory_limit: float = 256.0,
        stop_on_fail: bool = True,
        shrink: bool = True,
        save_dir: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.compiler = compiler
        self.gen_code = gen_code
        self.brute_code = brute_code
        self.main_code = main_code
        self.rounds = max(1, min(int(rounds), 100000))
        self.time_limit = max(0.1, float(time_limit))
        self.memory_limit = max(1.0, float(memory_limit))
        self.stop_on_fail = bool(stop_on_fail)
        self.shrink = bool(shrink)
        self.save_dir = Path(save_dir) if save_dir else (
            Path(tempfile.gettempdir()) / f"dtug_stress_{int(time.time())}"
        )
        self._stop = threading.Event()
        # 已编译程序的 exe 缓存（_save_case 最小化后重跑要用）
        self._exe_cache = {}

    # ------------------------------------------------------------------
    def request_stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: N802 - Qt API
        work = Path(tempfile.mkdtemp(prefix="dtug_stress_"))
        exes: List[str] = []
        try:
            self.progress.emit("编译生成器…")
            g = self.compiler.compile(self.gen_code)
            if not g.success:
                self.finished.emit("生成器编译失败：\n" + (g.output or g.error or ""))
                return
            gen_exe = g.exe_path or ""
            exes.append(gen_exe)

            self.progress.emit("编译暴力标程…")
            b = self.compiler.compile(self.brute_code)
            if not b.success:
                self.finished.emit("暴力标程编译失败：\n" + (b.output or b.error or ""))
                return
            brute_exe = b.exe_path or ""
            exes.append(brute_exe)

            self.progress.emit("编译待测程序…")
            m = self.compiler.compile(self.main_code)
            if not m.success:
                self.finished.emit("待测程序编译失败：\n" + (m.output or m.error or ""))
                return
            main_exe = m.exe_path or ""
            exes.append(main_exe)
            self._exe_cache = {"brute": brute_exe, "main": main_exe}

            self.save_dir.mkdir(parents=True, exist_ok=True)
            passed = 0
            for i in range(1, self.rounds + 1):
                if self._stop.is_set():
                    self.finished.emit(f"已手动停止（已完成 {passed} 轮，全部通过）")
                    return

                # 1) 生成数据
                grun = self.compiler.run_sandboxed(
                    gen_exe, input_data="", time_limit=max(self.time_limit, 5.0),
                    memory_limit_mb=self.memory_limit, working_dir=work,
                )
                if grun.status:
                    self.finished.emit(
                        f"第 {i} 轮生成器运行异常（{grun.status}），请检查生成器：\n{grun.stderr}"
                    )
                    return
                data_in = grun.stdout

                # 2) 两边分别跑
                brun = self.compiler.run_sandboxed(
                    brute_exe, input_data=data_in, time_limit=self.time_limit,
                    memory_limit_mb=self.memory_limit, working_dir=work,
                )
                if brun.status:
                    self.finished.emit(
                        f"第 {i} 轮暴力标程运行异常（{brun.status}）——通常是标程写错，"
                        f"请先修标程：\n{brun.stderr}"
                    )
                    return
                mrun = self.compiler.run_sandboxed(
                    main_exe, input_data=data_in, time_limit=self.time_limit,
                    memory_limit_mb=self.memory_limit, working_dir=work,
                )

                # 3) 判定
                reason = ""
                if mrun.status:
                    reason = f"待测程序运行异常：{mrun.status}"
                elif _normalize(mrun.stdout) != _normalize(brun.stdout):
                    reason = "输出不一致（WA）"

                if reason:
                    info = self._save_case(i, data_in, brun.stdout, mrun.stdout, reason)
                    self.found.emit(info)
                    if self.stop_on_fail:
                        self.finished.emit(
                            f"第 {i} 轮发现反例（{reason}），现场已保存到：\n{info['dir']}"
                        )
                        return
                    self.progress.emit(f"第 {i} 轮发现反例（{reason}），继续…")
                    continue

                passed += 1
                self.round_done.emit(i, True)
                self.progress.emit(f"第 {i}/{self.rounds} 轮通过")

            self.finished.emit(f"对拍完成：{self.rounds} 轮全部通过，未发现反例 🎉")
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(f"对拍异常：{exc}")
        finally:
            for exe in exes:
                try:
                    self.compiler.cleanup(exe)
                except Exception:
                    pass
            shutil.rmtree(work, ignore_errors=True)

    # ------------------------------------------------------------------
    def _save_case(self, round_no: int, data_in: str, brute_out: str, main_out: str, reason: str) -> dict:
        """保存反例现场；开启动最小化时先做行级收缩。"""
        shrunk = False
        if self.shrink:
            shrunk_input = self._shrink(data_in, brute_out, main_out, reason)
            if shrunk_input is not None and shrunk_input != data_in:
                data_in = shrunk_input
                shrunk = True
                # 最小化后输入变了，重跑一次拿到与新输入对应的输出
                # （shrink 内部已保证「仍是反例」，这里补存准确输出）
                brun = self.compiler.run_sandboxed(
                    self._exe_cache["brute"], input_data=data_in,
                    time_limit=self.time_limit, memory_limit_mb=self.memory_limit,
                )
                mrun = self.compiler.run_sandboxed(
                    self._exe_cache["main"], input_data=data_in,
                    time_limit=self.time_limit, memory_limit_mb=self.memory_limit,
                )
                brute_out, main_out = brun.stdout, mrun.stdout

        case_dir = self.save_dir / f"fail_{round_no}"
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "input.txt").write_text(data_in, encoding="utf-8")
        (case_dir / "brute.out").write_text(brute_out, encoding="utf-8")
        (case_dir / "main.out").write_text(main_out, encoding="utf-8")
        (case_dir / "reason.txt").write_text(
            f"轮次: {round_no}\n原因: {reason}\n已最小化: {'是' if shrunk else '否'}\n",
            encoding="utf-8",
        )
        return {
            "dir": str(case_dir),
            "round": round_no,
            "input": data_in,
            "brute": brute_out,
            "main": main_out,
            "reason": reason,
            "shrunk": shrunk,
        }

    # ------------------------------------------------------------------
    def _shrink(self, data_in: str, brute_out: str, main_out: str, reason: str) -> Optional[str]:
        """行级贪心最小化：能删的行依次删掉，只要「删后仍是反例」就保留删除。

        有尝试次数与时间上限（对拍是交互式工具，不能卡住 UI 线程太久）。
        返回最小化后的输入；无法收缩返回 None。
        """
        # exe 缓存：_save_case 里重跑要用（run() 里已编译好）
        if "brute" not in self._exe_cache:
            return None
        brute_exe = self._exe_cache["brute"]
        main_exe = self._exe_cache["main"]

        def still_fails(text: str) -> bool:
            b = self.compiler.run_sandboxed(
                brute_exe, input_data=text, time_limit=self.time_limit,
                memory_limit_mb=self.memory_limit,
            )
            if b.status:
                return False  # 标程挂了的输入没有最小化意义
            m = self.compiler.run_sandboxed(
                main_exe, input_data=text, time_limit=self.time_limit,
                memory_limit_mb=self.memory_limit,
            )
            if reason.startswith("待测程序运行异常"):
                return m.status == reason.split("：", 1)[1]
            return _normalize(m.stdout) != _normalize(b.stdout)

        lines = data_in.split("\n")
        changed = False
        attempts = 0
        deadline = time.time() + 15.0
        i = 0
        while i < len(lines) and attempts < 400 and time.time() < deadline:
            if self._stop.is_set():
                break
            attempts += 1
            trial = lines[:i] + lines[i + 1:]
            candidate = "\n".join(trial)
            if candidate.strip() and still_fails(candidate):
                lines = trial
                changed = True
                # 不递增 i：同一位置换了新行，继续尝试删
            else:
                i += 1
        if not changed:
            return None
        return "\n".join(lines)


class StressDialog(QDialog):
    """对拍器对话框。``main_code`` 一般为当前编辑器内容（由主窗口传入）。"""

    def __init__(self, compiler: Compiler, main_code: str = "", parent=None) -> None:
        super().__init__(parent)
        self.compiler = compiler
        self._worker: Optional[StressWorker] = None
        self._last_find_dir = ""
        self.setWindowTitle("对拍 - DTUG IDE")
        self.resize(1080, 720)
        self.setStyleSheet(dialog_qss(ui_palette()))
        self._build_ui(main_code)

    # ------------------------------------------------------------------
    def _build_ui(self, main_code: str) -> None:
        p = ui_palette()
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.gen_edit = CodeEditor()
        self.gen_edit.setPlainText(GEN_TEMPLATE)
        self.brute_edit = CodeEditor()
        self.brute_edit.setPlainText(BRUTE_TEMPLATE)
        self.main_edit = CodeEditor()
        self.main_edit.setPlainText(main_code or MAIN_TEMPLATE)
        splitter.addWidget(self._wrap(self.gen_edit, "数据生成器 (gen.cpp)"))
        splitter.addWidget(self._wrap(self.brute_edit, "暴力标程 (brute.cpp)"))
        splitter.addWidget(self._wrap(self.main_edit, "待测程序 (main.cpp)"))
        root.addWidget(splitter, stretch=1)

        # 参数行
        ctrl = QHBoxLayout()
        ctrl.setSpacing(10)
        ctrl.addWidget(QLabel("轮数:"))
        self.rounds_spin = QSpinBox()
        self.rounds_spin.setRange(1, 100000)
        self.rounds_spin.setValue(200)
        ctrl.addWidget(self.rounds_spin)
        ctrl.addWidget(QLabel("时限:"))
        self.tl_spin = QSpinBox()
        self.tl_spin.setRange(1, 60)
        self.tl_spin.setValue(1)
        self.tl_spin.setSuffix(" s")
        ctrl.addWidget(self.tl_spin)
        ctrl.addWidget(QLabel("内存:"))
        self.ml_spin = QSpinBox()
        self.ml_spin.setRange(1, 4096)
        self.ml_spin.setValue(256)
        self.ml_spin.setSuffix(" MB")
        ctrl.addWidget(self.ml_spin)
        self.stop_check = QCheckBox("失败即停")
        self.stop_check.setChecked(True)
        ctrl.addWidget(self.stop_check)
        self.shrink_check = QCheckBox("最小化反例")
        self.shrink_check.setChecked(True)
        self.shrink_check.setToolTip("行级贪心删除，让反例更小更易读（多花几秒）")
        ctrl.addWidget(self.shrink_check)
        ctrl.addStretch(1)
        root.addLayout(ctrl)

        # 输出目录行
        out = QHBoxLayout()
        out.setSpacing(8)
        out.addWidget(QLabel("保存目录:"))
        self.dir_edit = QLineEdit()
        self.dir_edit.setText(str(Path(tempfile.gettempdir()) / f"dtug_stress_{int(time.time())}"))
        self.dir_edit.setStyleSheet(plain_edit_qss(p))
        out.addWidget(self.dir_edit, stretch=1)
        browse_btn = QPushButton("浏览…")
        browse_btn.setStyleSheet(button_neutral_qss(p))
        browse_btn.clicked.connect(self._choose_dir)
        out.addWidget(browse_btn)
        root.addLayout(out)

        # 操作行
        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.start_btn = QPushButton("▶ 开始对拍")
        self.start_btn.setStyleSheet(button_green_qss(p))
        self.start_btn.clicked.connect(self._start)
        acts.addWidget(self.start_btn)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setStyleSheet(button_danger_qss(p))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        acts.addWidget(self.stop_btn)
        self.open_dir_btn = QPushButton("📂 打开反例目录")
        self.open_dir_btn.setStyleSheet(button_neutral_qss(p))
        self.open_dir_btn.setEnabled(False)
        self.open_dir_btn.clicked.connect(self._open_find_dir)
        acts.addWidget(self.open_dir_btn)
        acts.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(button_neutral_qss(p))
        close_btn.clicked.connect(self.close)
        acts.addWidget(close_btn)
        root.addLayout(acts)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setStyleSheet(console_qss(p))
        root.addWidget(self.log, stretch=1)

    def _wrap(self, editor: CodeEditor, title: str) -> QGroupBox:
        box = QGroupBox(title)
        box.setStyleSheet(group_qss(ui_palette()))
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.addWidget(editor)
        return box

    # ------------------------------------------------------------------
    def _choose_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择反例保存目录", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

    def _start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.log.clear()
        self.open_dir_btn.setEnabled(False)
        self._last_find_dir = ""
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setRange(0, 0)  # 忙碌动画
        self._worker = StressWorker(
            self.compiler,
            self.gen_edit.toPlainText(),
            self.brute_edit.toPlainText(),
            self.main_edit.toPlainText(),
            rounds=self.rounds_spin.value(),
            time_limit=float(self.tl_spin.value()),
            memory_limit=float(self.ml_spin.value()),
            stop_on_fail=self.stop_check.isChecked(),
            shrink=self.shrink_check.isChecked(),
            save_dir=self.dir_edit.text().strip(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.found.connect(self._on_found)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.request_stop()
        self.stop_btn.setEnabled(False)

    def _on_progress(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _on_found(self, info: dict) -> None:
        self._last_find_dir = str(info.get("dir") or "")
        self.open_dir_btn.setEnabled(True)
        self.log.appendPlainText(
            "❗ 发现反例（第 {round} 轮，{reason}）\n"
            "── 输入 ──\n{input}\n── 暴力输出 ──\n{brute}\n── 待测输出 ──\n{main}\n".format(
                round=info.get("round"),
                reason=info.get("reason"),
                input=(info.get("input") or "")[:2000],
                brute=(info.get("brute") or "")[:2000],
                main=(info.get("main") or "")[:2000],
            )
        )
        if info.get("shrunk"):
            self.log.appendPlainText("（反例已做行级最小化）")

    def _on_finished(self, summary: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.log.appendPlainText(summary)

    def _open_find_dir(self) -> None:
        if self._last_find_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_find_dir))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_stop()
            self._worker.wait(3000)
        super().closeEvent(event)
