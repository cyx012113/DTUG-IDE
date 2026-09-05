"""聊天面板（QDockWidget）：会话列表 + 消息区 + 输入框，挂到 DTUG-IDE 主窗口右侧。

- 私聊：输入对方用户名即可打开 ``dm:<me>~<peer>`` 房间；
- 群聊：建群后由 chat-worker 返回成员关系，自动出现在会话列表；
- 所有消息经 :class:`dtug_ide.chat_client.ChatClient` 的 HTTP 轮询收发。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDockWidget,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .chat_client import ChatClient, dm_room
from .chat_group_manage import GroupManageDialog
from .chat_widgets import MessageList, StatusPill
from .theme import (
    button_green_qss,
    button_neutral_qss,
    chat_qss,
    ui_palette,
)


def _msg_to_packet(m: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": m.get("id"),
        "sender": m.get("sender"),
        "text": m.get("text"),
        "timestamp": m.get("created_at"),
        "is_op": m.get("is_op"),
    }


def _system_packet(text: str) -> Dict[str, Any]:
    from time import time

    return {"type": "system", "text": text, "timestamp": int(time())}


# 常用表情（8 列网格；点击插入到输入框光标处）
EMOJIS = [
    "😀", "😃", "😄", "😁", "😆", "😅", "🤣", "😂",
    "🙂", "🙃", "😉", "😊", "😇", "🥰", "😍", "😘",
    "😋", "😛", "😜", "🤪", "😝", "🤑", "🤗", "🤭",
    "🤔", "🤐", "🤨", "😐", "😑", "😶", "😏", "😒",
    "🙄", "😬", "😴", "🤤", "😪", "😵", "🤯", "🥳",
    "🥸", "😎", "🤓", "🧐", "😕", "😟", "🙁", "😢",
    "😭", "😤", "😠", "😡", "🤬", "😳", "🥺", "😱",
    "👍", "👎", "👌", "✌️", "🤝", "🙏", "💪", "👏",
    "❤️", "💔", "✨", "🔥", "🎉", "🎊", "💡", "✅",
]


class EmojiPicker(QWidget):
    """轻量表情面板：Popup 窗口，点击外部自动关闭。"""

    def __init__(self, on_pick, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self._on_pick = on_pick
        p = ui_palette()
        self.setStyleSheet(f"background:{p['window']};border:1px solid {p['border']};")
        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(2)
        for index, emoji in enumerate(EMOJIS):
            button = QPushButton(emoji)
            button.setFixedSize(30, 30)
            button.setStyleSheet(
                "QPushButton{border:none;}"
                f"QPushButton:hover{{background:{p['selection']};}}"
            )
            button.clicked.connect(
                lambda _checked=False, value=emoji: self._pick(value)
            )
            grid.addWidget(button, index // 8, index % 8)

    def _pick(self, emoji: str) -> None:
        self.close()
        if callable(self._on_pick):
            self._on_pick(emoji)


class ChatDock(QDockWidget):
    """DTUG-IDE 聊天面板。"""

    def __init__(self, client: ChatClient, parent: Optional[QWidget] = None) -> None:
        super().__init__("聊天", parent)
        self.setObjectName("chatDock")
        self.client = client
        self._me = ""
        self._is_op = False
        self._logged_in = False
        self._rooms: Dict[str, str] = {}          # room -> 显示名
        self._dm_rooms: List[str] = []            # 已打开的私聊房间（保序）
        self._dm_peer_names: Dict[str, str] = {}  # room -> 对方显示名（保留发起时大小写）
        self._groups: List[Dict[str, Any]] = []   # 群聊信息（来自 /groups/mine）
        self._current_room = ""
        self._emoji_popup: Optional[EmojiPicker] = None

        self._build_ui()
        self._connect_signals()
        self.set_logged_in(False)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        p = ui_palette()
        central = QWidget()
        central.setObjectName("chatRoot")
        self._central = central
        central.setStyleSheet(chat_qss(p))
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # 头部
        header = QHBoxLayout()
        self._title = QLabel("聊天")
        self._title.setStyleSheet("font-weight:bold;font-size:14px;color:" + p["text"] + ";")
        self._status = StatusPill("未登录", "neutral")
        self._dm_btn = QPushButton("私聊")
        self._dm_btn.setStyleSheet(button_neutral_qss(p))
        self._grp_btn = QPushButton("建群")
        self._grp_btn.setStyleSheet(button_neutral_qss(p))
        self._join_btn = QPushButton("加群")
        self._join_btn.setStyleSheet(button_neutral_qss(p))
        self._join_btn.setToolTip("输入群号申请加入")
        self._manage_btn = QPushButton("群管理")
        self._manage_btn.setStyleSheet(button_neutral_qss(p))
        self._manage_btn.setToolTip("管理当前群聊（群主 / 管理员可用）")
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._status)
        header.addWidget(self._dm_btn)
        header.addWidget(self._grp_btn)
        header.addWidget(self._join_btn)
        header.addWidget(self._manage_btn)
        root.addLayout(header)

        # 已登录页
        logged = QWidget()
        lcol = QVBoxLayout(logged)
        lcol.setContentsMargins(0, 0, 0, 0)
        lcol.setSpacing(6)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._conv_list = QListWidget()
        self._conv_list.setMinimumWidth(140)

        right = QWidget()
        rcol = QVBoxLayout(right)
        rcol.setContentsMargins(0, 0, 0, 0)
        rcol.setSpacing(6)
        self._msg_list = MessageList()
        self._msg_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._input = QLineEdit()
        self._input.setPlaceholderText("输入消息，回车发送…")
        self._send_btn = QPushButton("发送")
        self._send_btn.setStyleSheet(button_green_qss(p))
        self._emoji_btn = QPushButton("😊")
        self._emoji_btn.setFixedWidth(34)
        self._emoji_btn.setToolTip("插入表情")
        self._emoji_btn.setStyleSheet(button_neutral_qss(p))
        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        input_row.addWidget(self._emoji_btn)
        input_row.addWidget(self._input, 1)
        input_row.addWidget(self._send_btn)
        rcol.addWidget(self._msg_list, 1)
        rcol.addLayout(input_row)

        split.addWidget(self._conv_list)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        lcol.addWidget(split)

        # 未登录页
        self._overlay = QLabel("请先登录以使用聊天功能")
        self._overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overlay.setStyleSheet(f"color:{p['text3']};font-size:13px;")

        # 登录 / 未登录 切换栈
        stack = QStackedWidget()
        stack.addWidget(self._overlay)
        stack.addWidget(logged)
        self._stack = stack
        root.addWidget(stack, 1)

        self.setWidget(central)
        self.setMinimumWidth(360)

    # ------------------------------------------------------------------
    def apply_theme(self) -> None:
        """重新读取当前调色板并刷新面板内所有控件（主窗口切换主题时调用）。

        聊天面板按钮在构建时各自套用了暗色/亮色样式表，若不在主题切换时
        重新套用，会残留旧主题的黑色背景。
        """
        from .theme import (
            ui_palette, button_neutral_qss, button_green_qss, chat_qss,
        )
        p = ui_palette()
        if hasattr(self, "_central"):
            self._central.setStyleSheet(chat_qss(p))
        self._title.setStyleSheet("font-weight:bold;font-size:14px;color:" + p["text"] + ";")
        self._dm_btn.setStyleSheet(button_neutral_qss(p))
        self._grp_btn.setStyleSheet(button_neutral_qss(p))
        self._join_btn.setStyleSheet(button_neutral_qss(p))
        self._manage_btn.setStyleSheet(button_neutral_qss(p))
        self._send_btn.setStyleSheet(button_green_qss(p))
        self._emoji_btn.setStyleSheet(button_neutral_qss(p))
        self._overlay.setStyleSheet(f"color:{p['text3']};font-size:13px;")

    def _connect_signals(self) -> None:
        self._dm_btn.clicked.connect(self._on_dm_clicked)
        self._grp_btn.clicked.connect(self._on_group_clicked)
        self._join_btn.clicked.connect(self._on_join_group)
        self._manage_btn.clicked.connect(self._on_manage_group)
        self._emoji_btn.clicked.connect(self._show_emoji_picker)
        self._send_btn.clicked.connect(self._on_send)
        self._input.returnPressed.connect(self._on_send)
        self._conv_list.itemClicked.connect(self._on_conv_clicked)
        self._msg_list.customContextMenuRequested.connect(self._on_msg_context_menu)

        self.client.message_received.connect(self._on_message)
        self.client.history_received.connect(self._on_history)
        self.client.conversations_updated.connect(self._on_groups)
        self.client.dms_updated.connect(self._on_dms)
        self.client.error.connect(self._on_error)
        self.client.state_changed.connect(self._on_state)

    # ------------------------------------------------------------------
    # 登录状态
    # ------------------------------------------------------------------
    def set_logged_in(self, logged_in: bool, username: str = "", is_op: bool = False) -> None:
        self._logged_in = logged_in
        self._me = username
        self._is_op = bool(is_op)
        self._stack.setCurrentIndex(0 if not logged_in else 1)
        self._status.set_status("未登录" if not logged_in else "在线", "neutral" if not logged_in else "success")
        self._msg_list.set_identity(username)
        if logged_in:
            self._rooms.clear()
            self._dm_rooms.clear()
            self._dm_peer_names.clear()
            self._groups = []
            self._conv_list.clear()
            self._msg_list.clear()
            self._current_room = ""
            self.client.fetch_groups()
            self.client.fetch_dms()

    # ------------------------------------------------------------------
    # 会话操作
    # ------------------------------------------------------------------
    def _open_room(self, room: str, display: str) -> None:
        self._rooms[room] = display
        self._current_room = room
        self.client.open_room(room)
        self._msg_list.clear()
        self._highlight_current()

    def _on_dm_clicked(self) -> None:
        if not self._logged_in:
            return
        peer, ok = QInputDialog.getText(self, "发起私聊", "输入对方用户名：")
        if not ok or not peer.strip():
            return
        peer = peer.strip()
        if peer.lower() == self._me.lower():
            return
        room = dm_room(self._me, peer)
        if room not in self._dm_rooms:
            self._dm_rooms.append(room)
        self._dm_peer_names[room] = peer
        self.client.add_dm(peer)  # 云端持久化私聊会话
        self._rebuild_conv_list()
        self._open_room(room, f"私聊 · {peer}")

    def _on_group_clicked(self) -> None:
        if not self._logged_in:
            return
        name, ok = QInputDialog.getText(self, "新建群聊", "输入群名称：")
        if not ok or not name.strip():
            return
        gid = self.client.create_group(name.strip())
        if gid:
            self.client.fetch_groups()  # create_group 不再 emit 单条更新，这里拉完整列表
            room = "grp:" + str(gid)
            self._open_room(room, f"群 · {name.strip()}")

    def _on_conv_clicked(self, item: QListWidgetItem) -> None:
        room = item.data(Qt.ItemDataRole.UserRole)
        if not room:
            return
        self._open_room(room, self._rooms.get(room, room))

    def _rebuild_conv_list(self) -> None:
        """统一重建会话列表：先私聊（云端恢复 + 本地），再群聊。"""
        self._conv_list.clear()
        for room in self._dm_rooms:
            peer = self._dm_peer_names.get(room) or dm_room_peer(room, self._me)
            self._add_conv_item(room, f"私聊 · {peer}")
        for g in self._groups:
            gid = g.get("id")
            if not gid:
                continue
            room = "grp:" + str(gid)
            self._rooms[room] = f"群 · {g.get('name', gid)}"
            # 群号仅群主 / 管理员可见（悬停提示）
            tip = f"群号：{gid}" if self._is_group_manager(g) else ""
            self._add_conv_item(room, self._rooms[room], tip)
        self._highlight_current()

    def _add_conv_item(self, room: str, display: str, tooltip: str = "") -> None:
        item = QListWidgetItem(display)
        item.setData(Qt.ItemDataRole.UserRole, room)
        if tooltip:
            item.setToolTip(tooltip)
        self._conv_list.addItem(item)

    def _highlight_current(self) -> None:
        for i in range(self._conv_list.count()):
            item = self._conv_list.item(i)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) == self._current_room)

    # ------------------------------------------------------------------
    # 收发
    # ------------------------------------------------------------------
    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text or not self._logged_in:
            return
        self._input.clear()
        self.client.send_text(text)

    def _on_message(self, m: Dict[str, Any]) -> None:
        self._msg_list.append_message(_msg_to_packet(m))

    def _on_history(self, msgs: List[Dict[str, Any]]) -> None:
        self._msg_list.set_messages([_msg_to_packet(m) for m in msgs])

    def _on_groups(self, groups: List[Dict[str, Any]]) -> None:
        """/groups/mine 返回（整表刷新）：替换本地群列表后重建会话列表。"""
        if not self._logged_in:
            return
        self._groups = list(groups or [])
        self._rebuild_conv_list()

    def _on_dms(self, dms: List[Dict[str, Any]]) -> None:
        """云端私聊会话恢复：刷新 / 换设备后仍能拿回私聊列表。"""
        if not self._logged_in:
            return
        for entry in dms or []:
            room = str(entry.get("room") or "")
            if not room.startswith("dm:"):
                continue
            if room not in self._dm_rooms:
                self._dm_rooms.append(room)
            peer = str(entry.get("peer") or "")
            if peer and room not in self._dm_peer_names:
                self._dm_peer_names[room] = peer
        self._rebuild_conv_list()

    # ------------------------------------------------------------------
    # 表情
    # ------------------------------------------------------------------
    def _show_emoji_picker(self) -> None:
        """在表情按钮上方弹出表情面板（点击外部自动关闭）。"""
        popup = EmojiPicker(self._insert_emoji, self)
        self._emoji_popup = popup
        popup.adjustSize()
        anchor = self._emoji_btn.mapToGlobal(self._emoji_btn.rect().topLeft())
        popup.move(anchor.x(), anchor.y() - popup.sizeHint().height() - 4)
        popup.show()

    def _insert_emoji(self, emoji: str) -> None:
        self._input.insert(emoji)
        self._input.setFocus()

    # ------------------------------------------------------------------
    # 消息右键：撤回 / 删除
    # ------------------------------------------------------------------
    def _on_msg_context_menu(self, pos) -> None:
        """右键消息：本人可撤回；群主 / 管理员可删除群内他人消息。"""
        packet = self._msg_list.packet_at(pos)
        if not packet or packet.get("type") == "system":
            return
        msg_id = packet.get("id")
        if not msg_id:
            return
        sender = str(packet.get("sender") or "")
        is_mine = bool(sender) and sender.lower() == self._me.lower()
        group = self._current_group()
        if not is_mine and not (group and self._is_group_manager(group)):
            return

        menu = QMenu(self)
        action = menu.addAction("撤回" if is_mine else "删除")
        chosen = menu.exec(self._msg_list.viewport().mapToGlobal(pos))
        if chosen is not action:
            return
        if not self.client.delete_message(self._current_room, int(msg_id)):
            return
        self._msg_list.remove_message(msg_id)

    # ------------------------------------------------------------------
    # 群：加群 / 管理 / 权限判定
    # ------------------------------------------------------------------
    def _current_group(self) -> Optional[Dict[str, Any]]:
        """当前会话对应的群信息（非群聊会话返回 None）。"""
        if not self._current_room.startswith("grp:"):
            return None
        gid = self._current_room[4:]
        for group in self._groups:
            if str(group.get("id")) == gid:
                return group
        return None

    def _is_group_manager(self, group: Optional[Dict[str, Any]]) -> bool:
        """群主 / 管理员 / 全局管理员：可见群号、可管理该群。"""
        if not group:
            return False
        if self._is_op:
            return True
        if str(group.get("owner") or "").lower() == self._me.lower():
            return True
        return str(group.get("my_role") or "") == "admin"

    def _on_manage_group(self) -> None:
        if not self._logged_in:
            return
        group = self._current_group()
        if not group:
            QMessageBox.information(self, "群管理", "请先在左侧选中一个群聊会话。")
            return
        if not self._is_group_manager(group):
            QMessageBox.information(self, "群管理", "仅群主 / 管理员可以管理群聊。")
            return
        GroupManageDialog(self.client, self._me, self._is_op, group, self).exec()
        self.client.fetch_groups()

    def _on_join_group(self) -> None:
        if not self._logged_in:
            return
        gid, ok = QInputDialog.getText(self, "加入群聊", "输入群号：")
        if not ok or not gid.strip():
            return
        data = self.client.join_group(gid.strip())
        if not data:
            return
        if data.get("success"):
            QMessageBox.information(
                self, "加入群聊",
                str(data.get("message") or "已加入群聊。"),
            )
        else:
            QMessageBox.warning(
                self, "加入群聊",
                str(data.get("error") or "加入失败。"),
            )
        self.client.fetch_groups()

    def _on_error(self, text: str) -> None:
        self._msg_list.append_message(_system_packet(f"⚠ {text}"))
        self._status.set_status("错误", "danger")

    def _on_state(self, state: str, msg: str) -> None:
        if state == "open":
            self._status.set_status("在线", "success")
        elif state == "connecting":
            self._status.set_status("连接中", "info")
        else:
            self._status.set_status("在线", "success")


def dm_room_peer(room: str, me: str) -> str:
    if not room.startswith("dm:"):
        return room
    parts = room[3:].split("~")
    for part in parts:
        if part.lower() != str(me).lower():
            return part
    return room


__all__ = ["ChatDock"]
