"""聊天后端客户端：通过 HTTP 轮询 chat-worker，复用 DTUG-IDE 现有登录 token。

- 鉴权：请求头 ``Authorization: Bearer <token>``，token 即 DTUG-IDE 登录后拿到的 JWT。
- 客户端绑定签名（2026-10 主页新机制）：token payload 带 sid 且与本机私钥匹配时，
  每个请求附带 ``X-Client-Sid/Sig/Ts/Nonce``（见 client_sig.py）；否则不带头，
  服务端走兼容放行。token 被偷也无法在别的机器上冒用。
- 传输：标准库 ``urllib``（无第三方依赖）；网络请求放在后台线程，避免阻塞 UI。
- 实时性：客户端每 1.5s 轮询一次 ``/poll``，聊天延迟约 1–2s（对聊天无感）。
- 房间命名：私聊 ``dm:<a>~<b>``（a,b 字典序小写），群聊 ``grp:<group_id>``。
- 令牌失效：轮询收到 401 时发出 ``auth_expired`` 信号，由 UI 清理凭证并引导重新登录
  （令牌有效期 24h；/me 的滑动续签由主窗口周期性调用 auth.get_me 捕获新 token）。
"""

from __future__ import annotations

import json
import threading
from typing import Any, List, Optional
from urllib.parse import quote
import urllib.error
import urllib.request

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from .client_sig import default_signer
from .version import __version__

CHAT_BASE_URL = "https://chat-worker.cyx012113.top"


def dm_room(a: str, b: str) -> str:
    """构造两人私聊房间名（对顺序无关）。"""
    pair = [str(a).lower(), str(b).lower()]
    pair.sort()
    return "dm:" + "~".join(pair)


def _is_dm(room: str) -> bool:
    return isinstance(room, str) and room.startswith("dm:")


def _dm_peer(room: str, me: str) -> str:
    """从私聊房间名取出对方用户名（用于列表显示）。"""
    if not _is_dm(room):
        return room
    parts = room[3:].split("~")
    for part in parts:
        if part.lower() != str(me).lower():
            return part
    return room


class ChatClient(QObject):
    """DTUG-IDE 聊天客户端（HTTP 轮询）。"""

    state_changed = pyqtSignal(str, str)
    message_received = pyqtSignal(dict)
    history_received = pyqtSignal(list)
    conversations_updated = pyqtSignal(list)
    dms_updated = pyqtSignal(list)  # 云端私聊会话列表：[{room, peer}, ...]
    error = pyqtSignal(str)
    auth_expired = pyqtSignal()  # 令牌失效（401）：提示重新登录

    def __init__(self, base_url: str = CHAT_BASE_URL, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._base = base_url.rstrip("/")
        self._token = ""
        self._username = ""
        self._is_op = False
        self._current_room = ""
        self._last_id = 0
        self._loading_history = False
        self._send_lock = threading.Lock()
        self._send_queue: List[str] = []
        self._groups_counter = 0
        self._thread: Optional["_PollThread"] = None
        self._lock = threading.RLock()
        self._auth_dead = False  # 令牌已失效：暂停轮询，避免 401 请求风暴

    # ------------------------------------------------------------------
    # 凭证
    # ------------------------------------------------------------------
    def set_credentials(self, token: str, username: str, is_op: bool = False) -> None:
        with self._lock:
            self._token = token or ""
            self._username = username or ""
            self._is_op = bool(is_op)
            self._auth_dead = False  # 新凭证（含重新登录 / 改名后重设）解除失效锁

    def clear_credentials(self) -> None:
        with self._lock:
            self._token = ""
            self._username = ""
            self._is_op = False

    @property
    def username(self) -> str:
        with self._lock:
            return self._username

    def start(self) -> None:
        with self._lock:
            if not self._token or self._thread is not None:
                return
        self._thread = _PollThread(self)
        self._thread.start()

    def stop(self) -> None:
        thread: Optional["_PollThread"] = None
        with self._lock:
            if self._thread is not None:
                thread = self._thread
                self._thread = None
        if thread is not None:
            thread.stop()
            thread.wait(3000)

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------
    def open_room(self, room: str) -> None:
        with self._lock:
            self._current_room = room
            self._last_id = 0
            self._loading_history = True
        self.state_changed.emit("open", f"已打开会话：{room}")

    def current_room(self) -> str:
        with self._lock:
            return self._current_room

    def send_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            if not self._current_room or not self._token:
                return
            self._send_queue.append(text)

    def _run_async(self, fn) -> None:
        """在后台线程执行一次网络操作，避免阻塞 UI 线程（登录 / 启动时的拉取）。"""
        worker = _OneShotWorker(fn, self)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        token: Optional[str] = None,
    ) -> tuple[int, dict]:
        auth = token if token is not None else self._token
        url = self._base + path
        data = None
        headers = {
            "Authorization": f"Bearer {auth}",
            "User-Agent": f"DTUG-IDE/{__version__}",
        }
        # 客户端绑定签名：token 带 sid 且与本机私钥匹配时才附加 X-Client-*，
        # 否则 headers_for 返回 {}（服务端对无 sid 请求兼容放行）。
        headers.update(default_signer().headers_for(method, path, auth))
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {}
            return exc.code, payload
        except Exception as exc:  # noqa: BLE001 - 网络异常统一上抛
            raise RuntimeError(f"{method} {path} 失败：{exc}") from exc

    # ------------------------------------------------------------------
    # 轮询（后台线程调用）
    # ------------------------------------------------------------------
    def _poll_once(self) -> None:
        with self._lock:
            if self._auth_dead:
                return  # 令牌已失效，等 UI 重新登录后再恢复
            token = self._token
            room = self._current_room
            last_id = self._last_id
            loading = self._loading_history
            queue = list(self._send_queue)
            self._send_queue.clear()
            self._groups_counter = (self._groups_counter + 1) % 7
            do_groups = self._groups_counter == 0

        if not token or not room:
            return

        # 1) 处理待发送队列
        for text in queue:
            try:
                status, _payload = self._request(
                    "POST", "/send", {"room": room, "text": text}, token=token
                )
                if status == 401:
                    # 令牌失效（过期 / tv 递增 / 被吊销）：通知 UI 重新登录，
                    # 剩余队列不再重试（避免无效请求风暴）。
                    with self._lock:
                        self._auth_dead = True
                    self.auth_expired.emit()
                    return
            except Exception as exc:  # noqa: BLE001
                self.error.emit(f"发送失败：{exc}")

        # 2) 拉取新消息
        try:
            status, data = self._request(
                "GET", f"/poll?room={quote(room, safe='~')}&after={last_id}", token=token
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"接收失败：{exc}")
            return

        if status == 401:
            with self._lock:
                self._auth_dead = True
            self.auth_expired.emit()
            return

        if not data.get("success"):
            self.error.emit(data.get("error", "聊天服务返回错误"))
            return

        msgs: List[dict] = data.get("messages", [])
        # 房间可能已切换：丢弃旧房间的响应，避免消息串到新会话（「消息加载错乱」）
        with self._lock:
            still_current = (self._current_room == room)
        if not still_current:
            return
        if msgs:
            new_last = max(int(m.get("id", 0)) for m in msgs)
            with self._lock:
                if new_last > self._last_id:
                    self._last_id = new_last
            if loading:
                self.history_received.emit(msgs)
                with self._lock:
                    self._loading_history = False
            else:
                for m in msgs:
                    self.message_received.emit(m)
        else:
            with self._lock:
                if self._loading_history:
                    self._loading_history = False

        # 3) 周期性刷新群组列表
        if do_groups:
            try:
                _s, gdata = self._request("GET", "/groups/mine", token=token)
                if gdata.get("success"):
                    self.conversations_updated.emit(gdata.get("groups", []))
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # 群组操作（在主线程调用，内部每次起短请求）
    # ------------------------------------------------------------------
    def fetch_groups(self) -> None:
        """异步拉取群组列表（后台线程，不阻塞 UI）。结果经 conversations_updated 信号返回。"""

        def _do() -> None:
            try:
                _s, gdata = self._request("GET", "/groups/mine")
                if gdata.get("success"):
                    self.conversations_updated.emit(gdata.get("groups", []))
            except Exception as exc:  # noqa: BLE001
                self.error.emit(f"获取群组失败：{exc}")

        self._run_async(_do)

    def create_group(self, name: str) -> Optional[str]:
        try:
            _s, data = self._request("POST", "/groups", {"name": name})
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"建群失败：{exc}")
            return None
        if data.get("success"):
            # 不再 emit 单条 conversations_updated —— 接收方是整表替换语义，
            # emit 一条会把会话列表清成只剩这个新群。由调用方自行 fetch_groups()。
            return str(data.get("group_id") or "")
        self.error.emit(data.get("error", "建群失败"))
        return None

    def add_member(self, group_id: str, username: str) -> None:
        try:
            _s, data = self._request(
                "POST", "/groups/members", {"group_id": group_id, "username": username}
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"添加成员失败：{exc}")
            return
        if not data.get("success"):
            self.error.emit(data.get("error", "添加成员失败"))

    def _on_state(self, state: str, msg: str) -> None:
        self.state_changed.emit(state, msg)

    # ------------------------------------------------------------------
    # 私聊云端持久化
    # ------------------------------------------------------------------
    def fetch_dms(self) -> None:
        """异步拉取当前用户的私聊会话列表（后台线程），结果经 dms_updated 信号返回。"""

        def _do() -> None:
            try:
                _s, data = self._request("GET", "/dms/mine")
            except Exception as exc:  # noqa: BLE001
                self.error.emit(f"获取私聊列表失败：{exc}")
                return
            if data.get("success"):
                self.dms_updated.emit(data.get("dms", []) or [])
            else:
                self.error.emit(data.get("error", "获取私聊列表失败"))

        self._run_async(_do)

    def add_dm(self, peer: str) -> None:
        """记录一条私聊会话到云端（发起私聊时调用；失败不影响本地打开）。"""
        try:
            self._request("POST", "/dms", {"peer": peer})
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 群管理（主线程直接发起短请求）
    # ------------------------------------------------------------------
    def fetch_group_members(self, group_id: str) -> Optional[dict]:
        try:
            _s, data = self._request("GET", f"/groups/members?group_id={quote(str(group_id), safe='')}")
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"获取群成员失败：{exc}")
            return None
        return data if data.get("success") else None

    def rename_group(self, group_id: str, name: str) -> bool:
        try:
            _s, data = self._request("POST", "/groups/rename", {"group_id": group_id, "name": name})
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"改名失败：{exc}")
            return False
        if not data.get("success"):
            self.error.emit(data.get("error", "改名失败"))
            return False
        return True

    def remove_member(self, group_id: str, username: str) -> bool:
        try:
            _s, data = self._request(
                "POST", "/groups/remove", {"group_id": group_id, "username": username}
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"踢人失败：{exc}")
            return False
        if not data.get("success"):
            self.error.emit(data.get("error", "踢人失败"))
            return False
        return True

    def mute_member(self, group_id: str, username: str, muted: bool) -> bool:
        try:
            _s, data = self._request(
                "POST", "/groups/mute",
                {"group_id": group_id, "username": username, "muted": 1 if muted else 0},
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"禁言操作失败：{exc}")
            return False
        if not data.get("success"):
            self.error.emit(data.get("error", "禁言操作失败"))
            return False
        return True

    def set_admin(self, group_id: str, username: str, make_admin: bool) -> bool:
        try:
            _s, data = self._request(
                "POST", "/groups/set-admin",
                {"group_id": group_id, "username": username, "admin": 1 if make_admin else 0},
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"设置管理员失败：{exc}")
            return False
        if not data.get("success"):
            self.error.emit(data.get("error", "设置管理员失败"))
            return False
        return True

    def transfer_group(self, group_id: str, username: str) -> bool:
        try:
            _s, data = self._request(
                "POST", "/groups/transfer", {"group_id": group_id, "username": username}
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"转让失败：{exc}")
            return False
        if not data.get("success"):
            self.error.emit(data.get("error", "转让失败"))
            return False
        return True

    def dismiss_group(self, group_id: str) -> bool:
        try:
            _s, data = self._request("POST", "/groups/dismiss", {"group_id": group_id})
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"解散失败：{exc}")
            return False
        if not data.get("success"):
            self.error.emit(data.get("error", "解散失败"))
            return False
        return True

    def group_settings(self, group_id: str, join_mode: int, allow_invite: bool, mute_all: bool) -> bool:
        try:
            _s, data = self._request(
                "POST", "/groups/settings",
                {
                    "group_id": group_id,
                    "join_mode": int(join_mode),
                    "allow_invite": 1 if allow_invite else 0,
                    "mute_all": 1 if mute_all else 0,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"保存群设置失败：{exc}")
            return False
        if not data.get("success"):
            self.error.emit(data.get("error", "保存群设置失败"))
            return False
        return True

    def join_group(self, group_id: str) -> Optional[dict]:
        try:
            _s, data = self._request("POST", "/groups/join", {"group_id": group_id})
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"申请进群失败：{exc}")
            return None
        return data

    def list_join_requests(self, group_id: str) -> Optional[dict]:
        try:
            _s, data = self._request("GET", f"/groups/requests?group_id={quote(str(group_id), safe='')}")
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"获取申请失败：{exc}")
            return None
        return data if data.get("success") else None

    def process_request(self, group_id: str, username: str, approve: bool) -> bool:
        path = "/groups/requests/approve" if approve else "/groups/requests/reject"
        try:
            _s, data = self._request("POST", path, {"group_id": group_id, "username": username})
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"处理申请失败：{exc}")
            return False
        if not data.get("success"):
            self.error.emit(data.get("error", "处理申请失败"))
            return False
        return True

    def delete_message(self, room: str, msg_id: int) -> bool:
        try:
            _s, data = self._request("POST", "/messages/delete", {"room": room, "id": int(msg_id)})
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"删除消息失败：{exc}")
            return False
        if not data.get("success"):
            self.error.emit(data.get("error", "删除消息失败"))
            return False
        return True


class _OneShotWorker(QThread):
    """后台执行一次网络请求；结束后自动销毁。"""

    def __init__(self, fn, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: N802 - Qt API name
        try:
            self._fn()
        except Exception:  # noqa: BLE001
            pass


class _PollThread(QThread):
    """后台轮询线程：每 1.5s 拉取一次当前房间的消息。"""

    def __init__(self, client: ChatClient) -> None:
        super().__init__(client)
        self._client = client
        self._stop = threading.Event()

    def run(self) -> None:  # noqa: N802 - Qt API name
        self._client._on_state("connecting", "正在连接聊天服务…")
        while not self._stop.is_set():
            self._client._poll_once()
            self._stop.wait(1.5)

    def stop(self) -> None:
        self._stop.set()


__all__ = ["ChatClient", "dm_room", "CHAT_BASE_URL"]
