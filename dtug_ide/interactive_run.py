"""交互式运行对话框（动态交互式调试）。

编译后启动程序，实时把输出流式显示到文本框；用户在输入框回车即把该行作为
stdin 发送给程序，实现「边运行边交互」。支持随时停止与退出状态提示。
"""

from __future__ import annotations

import os
import subprocess
import threading
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)

from .compiler import CREATE_NO_WINDOW, kill_process_tree
from .utils import safe_remove
from .theme import ui_palette, plain_edit_qss, button_neutral_qss, button_danger_qss, dialog_qss


class _ReaderThread(QThread):
    data_ready = pyqtSignal(str)
    process_done = pyqtSignal(int)

    def __init__(self, proc: subprocess.Popen, parent=None) -> None:
        super().__init__(parent)
        self.proc = proc

    def run(self) -> None:
        def pump(stream):
            try:
                while True:
                    chunk = stream.read(1)
                    if not chunk:
                        break
                    self.data_ready.emit(chunk.decode("utf-8", errors="replace"))
            except Exception:
                pass

        t_out = threading.Thread(target=pump, args=(self.proc.stdout,), daemon=True)
        t_err = threading.Thread(target=pump, args=(self.proc.stderr,), daemon=True)
        t_out.start()
        t_err.start()
        try:
            rc = self.proc.wait()
        except Exception:
            rc = -1
        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)
        self.process_done.emit(rc if rc is not None else -1)


class InteractiveRunDialog(QDialog):
    """交互式运行窗口。"""

    def __init__(self, exe_path: str, parent=None) -> None:
        super().__init__(parent)
        self.exe_path = exe_path
        self.proc: Optional[subprocess.Popen] = None
        self.reader: Optional[_ReaderThread] = None
        self.setWindowTitle("交互运行 - DTUG IDE")
        self.resize(720, 520)
        self.setStyleSheet(dialog_qss(ui_palette()))
        self._build_ui()
        self._start()

    def _build_ui(self) -> None:
        p = ui_palette()
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet(plain_edit_qss(p))
        self.output.setFont(QFont("Consolas", 11))
        root.addWidget(self.output, stretch=1)

        bar = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("在此输入，回车发送给程序（Ctrl+C 停止）…")
        self.input.setStyleSheet(plain_edit_qss(p))
        self.input.returnPressed.connect(self._send_input)
        bar.addWidget(self.input, stretch=1)

        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setStyleSheet(button_danger_qss(p))
        self.stop_btn.clicked.connect(self._stop)
        bar.addWidget(self.stop_btn)

        self.close_btn = QPushButton("关闭")
        self.close_btn.setStyleSheet(button_neutral_qss(p))
        self.close_btn.clicked.connect(self.accept)
        bar.addWidget(self.close_btn)
        root.addLayout(bar)

        self.status = QLabel("正在启动…")
        self.status.setStyleSheet(f"color: {p['text2']};")
        root.addWidget(self.status)

    def _start(self) -> None:
        try:
            self.proc = subprocess.Popen(
                [self.exe_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=os.path.dirname(self.exe_path) or ".",
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            self.output.appendPlainText(f"启动失败: {e}")
            self.status.setText("启动失败")
            return
        self.reader = _ReaderThread(self.proc)
        self.reader.data_ready.connect(self._on_data)
        self.reader.process_done.connect(self._on_done)
        self.reader.start()
        self.status.setText("运行中…（输入并回车与程序交互）")

    def _on_data(self, text: str) -> None:
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)

    def _send_input(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            self.status.setText("程序已结束")
            return
        line = self.input.text()
        self.input.clear()
        try:
            self.proc.stdin.write((line + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except Exception as e:
            self.status.setText(f"写入失败: {e}")

    def _on_done(self, rc: int) -> None:
        self.output.appendPlainText(f"\n[程序结束，返回码 {rc}]")
        self.status.setText(f"已结束（返回码 {rc}）")
        self.input.setEnabled(False)

    def _stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                kill_process_tree(self.proc.pid)
            except Exception:
                pass
        self.status.setText("已停止")

    def closeEvent(self, event) -> None:
        self._stop()
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
        safe_remove(self.exe_path)
        event.accept()
