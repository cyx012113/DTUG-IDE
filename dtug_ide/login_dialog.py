"""登录对话框：仅提供「经主页（Cloudflare 人机验证）登录」的桥接方式。

出于安全考虑，桌面端**不内置**用户名 / 密码 / 邮箱输入框——主页登录由
Cloudflare Turnstile 人机验证保护，凭据只在浏览器里输入，桌面端不保存密码。
桌面端通过以下方式拿到登录后的 token：

  1. 「在浏览器中用主页账号登录」按钮：打开主页登录页（带 ``?redirect=...``），
     登录成功后主页把 token 附加到 ``http://127.0.0.1:8731/callback?token=...``，
     由本对话框启动的本地回调服务器捕获。
  2. 「内嵌网页中登录」按钮：在应用内用 QWebEngineView 完成登录，导航被拦截
     后由 EmbeddedLoginDialog 取出 token。
  3.（兜底）已登录主页的用户把 localStorage 的 token 粘贴到此处直接绑定。

网络请求在 QThread 中执行，避免阻塞 UI。
"""

from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtCore import QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from .auth import AuthClient
from .embedded_login import _CallbackServer, build_login_url
from .theme import dialog_qss, ui_palette


class _AuthWorker(QThread):
    """后台执行 token 绑定校验请求（用 /me 取回用户信息）。"""

    done = pyqtSignal(bool, dict, str)  # ok, payload, mode

    def __init__(self, client: AuthClient, mode: str = "token", token: str = "") -> None:
        super().__init__()
        self.client = client
        self.mode = mode
        self.token = token

    def run(self) -> None:
        ok, me = self.client.get_me(self.token)
        if ok and isinstance(me, dict):
            # /me 滑动续签（令牌剩余不足 12h）会下发新 token：优先采用，
            # 否则 24h 后必掉线。续签令牌仍带原 sid，客户端签名不受影响。
            fresh = me.get("token")
            token = fresh if isinstance(fresh, str) and fresh else self.token
            user = {k: v for k, v in me.items() if k not in ("token", "token_refreshed")}
            self.done.emit(True, {"token": token, "user": user}, "token")
        else:
            self.done.emit(False, me if isinstance(me, dict) else {"error": "无效的 token"}, "token")


class LoginDialog(QDialog):
    """登录对话框：成功（经主页 CF 验证）后 emit (token, user, remember)。"""

    login_success = pyqtSignal(str, dict, bool)

    def __init__(self, client: AuthClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self._busy = False
        self.setWindowTitle("登录 - DTUG IDE")
        self.setMinimumWidth(420)
        self.setStyleSheet(dialog_qss(ui_palette()))
        self._build_ui()
        # 启动本地回调服务器：使「在浏览器中登录」也能经 redirect 自动取回 token
        self._server = _CallbackServer(self)
        self._server.token_found.connect(self._on_server_token)
        self._server.start()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 20, 20, 20)

        title = QLabel("登录 DTUG IDE")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #e8edf5;")
        root.addWidget(title)

        hint_cf = QLabel(
            "出于安全，登录由主页 Cloudflare 人机验证（Turnstile）保护，"
            "凭据仅在浏览器中输入，桌面端不保存密码。"
        )
        hint_cf.setStyleSheet("color: #93a4c0; font-size: 12px;")
        hint_cf.setWordWrap(True)
        root.addWidget(hint_cf)

        # 方式 1：在浏览器中用主页账号登录（带 ?redirect=...，登录后本地服务器捕获 token）
        self.home_btn = QPushButton("🌐 在浏览器中用主页账号登录")
        self.home_btn.setObjectName("home")
        self.home_btn.clicked.connect(self._open_home_login)
        root.addWidget(self.home_btn)

        # 方式 2：内嵌主页登录页（QWebEngineView，登录后自动回调）
        self.embed_btn = QPushButton("🪟 内嵌网页中登录（推荐）")
        self.embed_btn.setObjectName("home")
        self.embed_btn.clicked.connect(self._open_embedded_login)
        root.addWidget(self.embed_btn)

        sep = QLabel("")
        sep.setObjectName("sep")
        sep.setFixedHeight(1)
        root.addWidget(sep)

        hint = QLabel("已登录主页？把浏览器控制台中 localStorage.getItem('auth_token') 的值粘贴到下方即可绑定。")
        hint.setStyleSheet("color: #93a4c0; font-size: 12px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        token_row = QHBoxLayout()
        token_row.setSpacing(8)
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("粘贴主页 token 直接登录")
        self.token_bind_btn = QPushButton("绑定")
        self.token_bind_btn.setObjectName("primary")
        self.token_bind_btn.clicked.connect(self._bind_token)
        token_row.addWidget(self.token_edit, 1)
        token_row.addWidget(self.token_bind_btn)
        root.addLayout(token_row)

        self.remember_check = QCheckBox("记住我（在此设备保持登录）")
        self.remember_check.setChecked(True)
        root.addWidget(self.remember_check)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #f87171; font-size: 13px;")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)
        root.addLayout(btn_row)

        self.token_edit.returnPressed.connect(self._bind_token)

    def _open_home_login(self) -> None:
        # 打开主页登录页（带 ?redirect=...）；登录成功后由本地回调服务器
        # （_CallbackServer）经 redirect 捕获 token —— 系统浏览器也能自动登录。
        QDesktopServices.openUrl(build_login_url())

    def _open_embedded_login(self) -> None:
        # 内嵌主页登录页：在应用内用 QWebEngineView 完成登录，
        # 主页跳转到本机回调 URL 时由 EmbeddedLoginDialog 拦截并取出 token。
        from .embedded_login import EmbeddedLoginDialog

        dlg = EmbeddedLoginDialog(self.client, self)
        dlg.login_success.connect(
            lambda token, user: (
                self.login_success.emit(token, user, self.remember_check.isChecked()),
                self.accept(),
            )
        )
        dlg.exec()

    def _on_server_token(self, token: str) -> None:
        # 系统浏览器经 redirect 回带的 token：走与粘贴相同的校验/绑定流程。
        self.token_edit.setText(token)
        self._bind_token()

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.token_bind_btn.setEnabled(not busy)
        self.home_btn.setEnabled(not busy)
        self.embed_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(not busy)
        if busy:
            self.token_bind_btn.setText(label or "校验中...")
        else:
            self.token_bind_btn.setText("绑定")

    def _bind_token(self) -> None:
        if self._busy:
            return
        token = self.token_edit.text().strip()
        if not token:
            self._show_error("请先粘贴主页 token")
            return
        self._set_busy(True, "校验中...")
        self.error_label.clear()
        self._worker = _AuthWorker(self.client, "token", token=token)
        self._worker.done.connect(self._on_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _show_error(self, msg: str) -> None:
        self.error_label.setStyleSheet("color: #f87171; font-size: 13px;")
        self.error_label.setText("❌ " + msg)

    def _show_success(self, msg: str) -> None:
        self.error_label.setStyleSheet("color: #34d399; font-size: 13px;")
        self.error_label.setText("✅ " + msg)

    def _on_done(self, ok: bool, data: Dict[str, Any], mode: str) -> None:
        self._set_busy(False)
        if not ok:
            self._show_error(str(data.get("error", "绑定失败")))
            return
        token = data.get("token")
        user = data.get("user") or {}
        if not token:
            self._show_error("服务器响应缺少 token")
            return
        self.login_success.emit(token, user, self.remember_check.isChecked())
        self.accept()

    def done(self, r: int) -> None:
        """对话框退出时停止本地回调服务器，释放端口。"""
        if getattr(self, "_server", None) is not None:
            self._server.stop()
        super().done(r)
