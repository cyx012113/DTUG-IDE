"""新建 / 编辑题目对话框。"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout,
)

from .problem_store import ProblemInfo
from .theme import dialog_qss, group_qss, plain_edit_qss, ui_palette


class ProblemEditDialog(QDialog):
    """题目元信息 + 题面编辑。``edit`` 为 None 时是新建，否则为编辑。"""

    saved = pyqtSignal(object)  # ProblemInfo

    def __init__(self, parent=None, edit: Optional[ProblemInfo] = None) -> None:
        super().__init__(parent)
        self._edit = edit
        self.setWindowTitle("编辑题目" if edit else "新建题目")
        self.setMinimumSize(640, 560)
        self.setStyleSheet(dialog_qss(ui_palette()))
        self._build_ui()
        if edit is not None:
            self._fill(edit)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        p = ui_palette()
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        form_box = QVBoxLayout()
        form = QFormLayout()
        form.setSpacing(8)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("如：两数之和 / P1001")
        self.title_edit.setStyleSheet(plain_edit_qss(p))
        form.addRow("标题", self.title_edit)

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("如：洛谷 / Codeforces / 校内模拟")
        self.source_edit.setStyleSheet(plain_edit_qss(p))
        form.addRow("来源", self.source_edit)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("用逗号分隔，如：模拟, 枚举, 入门")
        self.tags_edit.setStyleSheet(plain_edit_qss(p))
        form.addRow("标签", self.tags_edit)

        limits = QHBoxLayout()
        limits.setSpacing(8)
        self.tl_spin = QDoubleSpinBox()
        self.tl_spin.setRange(0.1, 60.0)
        self.tl_spin.setValue(1.0)
        self.tl_spin.setSuffix(" s")
        self.ml_spin = QDoubleSpinBox()
        self.ml_spin.setRange(1, 4096)
        self.ml_spin.setValue(256.0)
        self.ml_spin.setSuffix(" MB")
        limits.addWidget(self.tl_spin)
        limits.addWidget(self.ml_spin)
        limits.addStretch(1)
        form.addRow("时限 / 内存", limits)
        form_box.addLayout(form)
        root.addLayout(form_box)

        hint = QLabel("题面（支持 Markdown；公式可用 $…$ 包裹）")
        hint.setStyleSheet(f"color: {p['text2']}; font-size: 12px;")
        root.addWidget(hint)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setStyleSheet(plain_edit_qss(p))
        root.addWidget(self.desc_edit, stretch=1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _fill(self, info: ProblemInfo) -> None:
        self.title_edit.setText(info.title)
        self.source_edit.setText(info.source)
        self.tags_edit.setText(", ".join(info.tags))
        self.tl_spin.setValue(info.time_limit)
        self.ml_spin.setValue(info.memory_limit)
        self.desc_edit.setPlainText(info.description)

    # ------------------------------------------------------------------
    def values(self) -> dict:
        tags: List[str] = [
            t.strip() for t in self.tags_edit.text().replace("，", ",").split(",") if t.strip()
        ]
        return {
            "title": self.title_edit.text().strip(),
            "source": self.source_edit.text().strip(),
            "tags": tags,
            "time_limit": float(self.tl_spin.value()),
            "memory_limit": float(self.ml_spin.value()),
            "description": self.desc_edit.toPlainText(),
        }

    def _accept(self) -> None:
        if not self.title_edit.text().strip():
            self.title_edit.setFocus()
            return
        self.accept()
