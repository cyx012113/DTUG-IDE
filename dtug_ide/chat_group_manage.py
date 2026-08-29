"""群管理对话框：群主 / 管理员可用的完整群管理能力。

权限边界与 chat-worker 后端（``getGroupActor`` 及各 handler）严格一致：

- 禁言：群主可禁言任何人（含管理员）；管理员仅能禁言普通成员；任何人都不能禁言群主。
- 踢人：群主 / 管理员可踢；不可踢群主；管理员不可踢管理员。
- 设 / 撤管理员、转让群主、解散群：仅群主（全局管理员 ``is_op`` 视同群主）。
- 群设置（加入方式 / 允许成员拉人 / 全员禁言）：群主 / 管理员。
- 全员禁言不对群主 / 管理员生效（后端 ``handleSend`` 已保证，这里只负责开关）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .theme import button_green_qss, button_neutral_qss, tree_qss, ui_palette

JOIN_MODES = ((0, "禁止加入"), (1, "需要审核"), (2, "自由加入"))


class GroupManageDialog(QDialog):
    """群管理对话框：群号可见 / 改名 / 群设置 / 成员管理 / 入群审批 / 解散。"""

    def __init__(
        self,
        client: Any,
        me: str,
        is_op: bool,
        group: Dict[str, Any],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self._me = str(me or "")
        self._is_op = bool(is_op)
        self._group = dict(group or {})
        self._gid = str(self._group.get("id") or "")
        self._owner = str(self._group.get("owner") or "")
        self._members: List[Dict[str, Any]] = []
        self._join_mode = 1
        self._allow_invite = False
        self._mute_all = False
        self._join_radios: Dict[int, QRadioButton] = {}

        p = ui_palette()
        self.setWindowTitle(f"群管理 · {self._group.get('name') or self._gid}")
        self.resize(470, 600)
        self.setStyleSheet(f"background:{p['window']};color:{p['text']};")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ---- 群号（群主 / 管理员可见，可复制）----
        id_row = QHBoxLayout()
        id_row.addWidget(QLabel(f"群号：{self._gid}"))
        id_row.addStretch(1)
        self._copy_btn = QPushButton("复制群号")
        self._copy_btn.setStyleSheet(button_neutral_qss(p))
        self._copy_btn.clicked.connect(self._on_copy_id)
        id_row.addWidget(self._copy_btn)
        root.addLayout(id_row)

        # ---- 改名 ----
        name_row = QHBoxLayout()
        self._name_edit = QLineEdit(str(self._group.get("name") or ""))
        self._rename_btn = QPushButton("改名")
        self._rename_btn.setStyleSheet(button_neutral_qss(p))
        self._rename_btn.clicked.connect(self._on_rename)
        name_row.addWidget(self._name_edit, 1)
        name_row.addWidget(self._rename_btn)
        root.addLayout(name_row)

        # ---- 加入方式 ----
        root.addWidget(QLabel("加入方式："))
        for value, label in JOIN_MODES:
            radio = QRadioButton(label)
            self._join_radios[value] = radio
            root.addWidget(radio)

        self._allow_chk = QCheckBox("允许普通成员拉人（管理员无视此限制）")
        self._mute_chk = QCheckBox("全员禁言（不对群主 / 管理员生效）")
        root.addWidget(self._allow_chk)
        root.addWidget(self._mute_chk)

        self._save_btn = QPushButton("保存群设置")
        self._save_btn.setStyleSheet(button_green_qss(p))
        self._save_btn.clicked.connect(self._on_save_settings)
        root.addWidget(self._save_btn)

        # ---- 成员管理 ----
        root.addWidget(QLabel("成员："))
        self._member_list = QListWidget()
        self._member_list.setStyleSheet(tree_qss(p))
        self._member_list.currentItemChanged.connect(
            lambda *_args: self._refresh_action_buttons()
        )
        root.addWidget(self._member_list, 1)

        act_row = QHBoxLayout()
        self._mute_btn = QPushButton("禁言")
        self._kick_btn = QPushButton("踢出")
        self._admin_btn = QPushButton("设为管理员")
        self._transfer_btn = QPushButton("转让群主")
        for button in (
            self._mute_btn,
            self._kick_btn,
            self._admin_btn,
            self._transfer_btn,
        ):
            button.setStyleSheet(button_neutral_qss(p))
            act_row.addWidget(button)
        self._mute_btn.clicked.connect(self._on_mute)
        self._kick_btn.clicked.connect(self._on_kick)
        self._admin_btn.clicked.connect(self._on_set_admin)
        self._transfer_btn.clicked.connect(self._on_transfer)
        root.addLayout(act_row)

        # ---- 邀请 ----
        inv_row = QHBoxLayout()
        self._invite_edit = QLineEdit()
        self._invite_edit.setPlaceholderText("输入用户名邀请进群…")
        self._invite_btn = QPushButton("邀请")
        self._invite_btn.setStyleSheet(button_neutral_qss(p))
        self._invite_btn.clicked.connect(self._on_invite)
        inv_row.addWidget(self._invite_edit, 1)
        inv_row.addWidget(self._invite_btn)
        root.addLayout(inv_row)

        # ---- 底部 ----
        bottom = QHBoxLayout()
        self._requests_btn = QPushButton("查看入群申请")
        self._requests_btn.setStyleSheet(button_neutral_qss(p))
        self._requests_btn.clicked.connect(self._on_requests)
        self._dismiss_btn = QPushButton("解散群聊")
        self._dismiss_btn.setStyleSheet(button_neutral_qss(p))
        self._dismiss_btn.clicked.connect(self._on_dismiss)
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(button_neutral_qss(p))
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(self._requests_btn)
        bottom.addWidget(self._dismiss_btn)
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._load()

    # ------------------------------------------------------------------
    # 权限判定
    # ------------------------------------------------------------------
    def _my_role(self) -> str:
        if self._owner.lower() == self._me.lower():
            return "owner"
        for member in self._members:
            if str(member.get("username") or "").lower() == self._me.lower():
                return str(member.get("role") or "member")
        return "member"

    def _is_owner_role(self) -> bool:
        """群主（或全局管理员 is_op）。"""
        return self._is_op or self._my_role() == "owner"

    def _can_manage(self) -> bool:
        return self._is_op or self._my_role() in ("owner", "admin")

    def _selected_member(self) -> str:
        item = self._member_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _find_member(self, username: str) -> Optional[Dict[str, Any]]:
        target = str(username or "").lower()
        for member in self._members:
            if str(member.get("username") or "").lower() == target:
                return member
        return None

    # ------------------------------------------------------------------
    # 数据加载与渲染
    # ------------------------------------------------------------------
    def _load(self) -> None:
        data = self.client.fetch_group_members(self._gid)
        if not data:
            QMessageBox.warning(
                self,
                "群管理",
                "无法读取群成员。\n"
                "请确认 chat-worker 已部署 /groups/members 新接口，且你拥有管理权限。",
            )
            return
        self._owner = str(data.get("owner") or self._owner)
        self._join_mode = int(data.get("join_mode") or 1)
        self._allow_invite = bool(data.get("allow_invite"))
        self._mute_all = bool(data.get("mute_all"))
        self._members = list(data.get("members") or [])
        self._sync_settings()
        self._render_members()
        self._apply_permissions()

    def _sync_settings(self) -> None:
        mode = self._join_mode if self._join_mode in self._join_radios else 1
        self._join_radios[mode].setChecked(True)
        self._allow_chk.setChecked(self._allow_invite)
        self._mute_chk.setChecked(self._mute_all)
        self._name_edit.setText(str(self._group.get("name") or ""))

    def _render_members(self) -> None:
        self._member_list.clear()
        for member in self._members:
            username = str(member.get("username") or "")
            role = str(member.get("role") or "member")
            tags: List[str] = []
            if username.lower() == self._owner.lower():
                tags.append("群主")
            elif role == "admin":
                tags.append("管理员")
            if member.get("muted"):
                tags.append("已禁言")
            label = username + (f"（{'，'.join(tags)}）" if tags else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, username)
            self._member_list.addItem(item)
        self._refresh_action_buttons()

    def _apply_permissions(self) -> None:
        manage = self._can_manage()
        owner = self._is_owner_role()
        self._name_edit.setEnabled(manage)
        self._rename_btn.setEnabled(manage)
        for radio in self._join_radios.values():
            radio.setEnabled(manage)
        self._allow_chk.setEnabled(manage)
        self._mute_chk.setEnabled(manage)
        self._save_btn.setEnabled(manage)
        self._requests_btn.setEnabled(manage)
        self._invite_edit.setEnabled(manage)
        self._invite_btn.setEnabled(manage)
        self._dismiss_btn.setEnabled(owner)
        self._refresh_action_buttons()

    def _refresh_action_buttons(self) -> None:
        """按「我的角色 × 目标成员角色」动态开关操作按钮（与后端权限一致）。"""
        username = self._selected_member()
        target = self._find_member(username)
        owner_role = self._is_owner_role()
        manage = self._can_manage()

        if target is None:
            self._mute_btn.setEnabled(False)
            self._kick_btn.setEnabled(False)
            self._admin_btn.setEnabled(False)
            self._transfer_btn.setEnabled(False)
            return

        target_role = str(target.get("role") or "member")
        target_is_owner = username.lower() == self._owner.lower()

        # 禁言：群主可禁言任何人（含管理员）；管理员仅普通成员；不能禁言群主
        self._mute_btn.setEnabled(
            (not target_is_owner) and (owner_role or (manage and target_role == "member"))
        )
        # 踢人：不可踢群主；管理员不可踢管理员
        self._kick_btn.setEnabled(
            (not target_is_owner) and (owner_role or (manage and target_role == "member"))
        )
        # 设 / 撤管理员、转让群主：仅群主，且目标不能是群主本人
        self._admin_btn.setEnabled(owner_role and not target_is_owner)
        self._transfer_btn.setEnabled(owner_role and not target_is_owner)

        self._mute_btn.setText("解除禁言" if target.get("muted") else "禁言")
        self._admin_btn.setText(
            "取消管理员" if target_role == "admin" else "设为管理员"
        )

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _on_copy_id(self) -> None:
        QApplication.clipboard().setText(self._gid)
        self._copy_btn.setText("已复制")

    def _on_rename(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            return
        if self.client.rename_group(self._gid, name):
            self._group["name"] = name
            self.setWindowTitle(f"群管理 · {name}")
            self._load()

    def _on_save_settings(self) -> None:
        mode = 1
        for value, radio in self._join_radios.items():
            if radio.isChecked():
                mode = value
                break
        if self.client.group_settings(
            self._gid, mode, self._allow_chk.isChecked(), self._mute_chk.isChecked()
        ):
            self._load()

    def _on_invite(self) -> None:
        name = self._invite_edit.text().strip()
        if not name:
            return
        self.client.add_member(self._gid, name)
        self._invite_edit.clear()
        self._load()

    def _on_mute(self) -> None:
        username = self._selected_member()
        if not username:
            return
        member = self._find_member(username) or {}
        muted = not bool(member.get("muted"))
        if self.client.mute_member(self._gid, username, muted):
            self._load()

    def _on_kick(self) -> None:
        username = self._selected_member()
        if not username:
            return
        answer = QMessageBox.question(
            self,
            "踢出成员",
            f"确定将 {username} 踢出群聊？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.client.remove_member(self._gid, username):
            self._load()

    def _on_set_admin(self) -> None:
        username = self._selected_member()
        if not username:
            return
        member = self._find_member(username) or {}
        make_admin = str(member.get("role") or "") != "admin"
        if self.client.set_admin(self._gid, username, make_admin):
            self._load()

    def _on_transfer(self) -> None:
        username = self._selected_member()
        if not username:
            return
        if username.lower() == self._me.lower():
            QMessageBox.information(self, "转让群主", "不能转让给自己。")
            return
        answer = QMessageBox.question(
            self,
            "转让群主",
            f"确定将群主转让给 {username}？转让后你将变为普通成员。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.client.transfer_group(self._gid, username):
            self._load()

    def _on_dismiss(self) -> None:
        answer = QMessageBox.question(
            self,
            "解散群聊",
            f"确定解散群 {self._gid}？该操作不可撤销，群消息将被清除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.client.dismiss_group(self._gid):
            self.accept()

    def _on_requests(self) -> None:
        data = self.client.list_join_requests(self._gid)
        requests = (data or {}).get("requests") or []
        if not requests:
            QMessageBox.information(self, "入群申请", "暂无待处理的入群申请。")
            return
        names = [str(item.get("username") or "") for item in requests]
        name, ok = QInputDialog.getItem(
            self, "入群申请", "选择申请人：", names, 0, False
        )
        if not ok or not name:
            return
        answer = QMessageBox.question(
            self,
            "处理申请",
            f"同意 {name} 加入群聊？（选 No 为拒绝）",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return
        if self.client.process_request(
            self._gid, name, answer == QMessageBox.StandardButton.Yes
        ):
            self._load()


__all__ = ["GroupManageDialog"]
