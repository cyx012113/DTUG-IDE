"""内嵌主页登录页（QWebEngineView），通过回调 URL 捕获 token。

桌面端（localhost）读不到 .cyx012113.top 的共享 cookie，故主页在跳转到「外部」
（非 cyx012113.top）redirect 时会把 token 附加到 URL 上（见 D:\\Desktop\\网站\\main
的 appendTokenToExternalRedirect）。本模块加载主页登录页，拦截跳转到
http://127.0.0.1:<PORT>/callback?token=... 的导航，从而拿到 token 完成鉴权。
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse, parse_qs, quote

from PyQt6.QtCore import QThread, QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .auth import AuthClient, HOME_LOGIN_URL

CALLBACK_PORT = 8731
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PATH = "/callback"
CALLBACK_URL = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"


class _CallbackPage(QWebEnginePage):
    """拦截跳转到本地回调 URL 的导航，从中提取 token（不真正发起请求）。"""

    token_found = pyqtSignal(str)

    def acceptNavigationRequest(self, url: QUrl, nav_type, isMainFrame: bool) -> bool:  # noqa: N802
        parsed = urlparse(url.toString())
        if parsed.hostname == CALLBACK_HOST and parsed.path == CALLBACK_PATH:
            qs = parse_qs(parsed.query)
            tokens = qs.get("token")
            if tokens:
                self.token_found.emit(tokens[0])
            return False  # token 已捕获，取消该导航
        return super().acceptNavigationRequest(url, nav_type, isMainFrame)


class _TokenValidateWorker(QThread):
    """后台用 token 调 /me 校验并取回用户信息。"""

    done = pyqtSignal(bool, object)

    def __init__(self, client: AuthClient, token: str) -> None:
        super().__init__()
        self.client = client
        self.token = token

    def run(self) -> None:
        ok, me = self.client.get_me(self.token)
        if ok and isinstance(me, dict):
            self.done.emit(True, me)
        else:
            self.done.emit(False, None)


class EmbeddedLoginDialog(QDialog):
    """内嵌主页登录页的对话框；登录成功后 emit (token, user)。"""

    login_success = pyqtSignal(str, dict)

    def __init__(self, client: AuthClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self._token: Optional[str] = None
        self.setWindowTitle("在主页登录 - DTUG IDE")
        self.resize(960, 680)
        self.setStyleSheet(
            "QDialog { background: #0b0e17; }"
            "QLabel { color: #e8edf5; }"
            "QPushButton { background: #1a2332; color: #e8edf5;"
            " border: 1px solid #2a3a5a; border-radius: 6px; padding: 8px 14px; }"
            "QPushButton:hover { border-color: #4f9cf7; }"
        )
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView()
        self.page = _CallbackPage(self.view)
        self.view.setPage(self.page)
        self.page.token_found.connect(self._on_token)
        root.addWidget(self.view, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(10, 8, 10, 8)
        self.status = QLabel("请在上方主页完成登录；登录成功后将自动返回本应用。")
        self.status.setStyleSheet("color: #93a4c0;")
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        bar.addWidget(self.cancel_btn)
        root.addLayout(bar)

    def _load(self) -> None:
        url = QUrl(HOME_LOGIN_URL + "?redirect=" + quote(CALLBACK_URL, safe="") + "#login")
        self.view.load(url)

    def _on_token(self, token: str) -> None:
        self._token = token
        self.status.setText("已获取到 token，正在校验身份...")
        self.cancel_btn.setEnabled(False)
        self._worker = _TokenValidateWorker(self.client, token)
        self._worker.done.connect(self._on_validated)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_validated(self, ok: bool, user: Optional[Dict[str, Any]]) -> None:
        if not ok or not isinstance(user, dict):
            self.status.setText("❌ token 校验失败，请重试或改用其他登录方式。")
            self.cancel_btn.setEnabled(True)
            self._load()  # 重新加载登录页
            return
        user.setdefault("username", "")
        self.login_success.emit(self._token or "", user)
        self.accept()
