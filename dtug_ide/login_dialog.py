"""登录 / 注册对话框，模仿主页 main / werewolf 的认证交互。

提供三种登录方式，与网站保持一致：
  1. 内置账号密码登录 / 注册（Python urllib 直连 auth-worker，不受浏览器 CORS 限制）。
  2. 「在浏览器中用主页账号登录」按钮（完全照搬 werewolf 的跳转链接
     https://cyx012113.top/#login），用于在网站完成登录。
  3. 已登录主页的用户可把 token 粘贴回桌面端直接绑定（桌面端读不到主页的
     共享 cookie，故用此桥接完成鉴权）。

网络请求在 QThread 中执行，避免阻塞 UI。
"""

from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtCore import QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from .auth import AuthClient, HOME_LOGIN_URL


class _AuthWorker(QThread):
    """后台执行登录 / 注册 / token 绑定请求。"""

    done = pyqtSignal(bool, dict, str)  # ok, payload, mode

    def __init__(self, client: AuthClient, mode: str, username: str = "", password: str = "",
                 email: str = "", token: str = "") -> None:
        super().__init__()
        self.client = client
        self.mode = mode
        self.username = username
        self.password = password
        self.email = email
        self.token = token

    def run(self) -> None:
        if self.mode == "login":
            ok, data = self.client.login(self.username, self.password)
            if ok:
                token = data.get("token")
                if token:
                    # 与网站 checkAuthStatus 一致：用 /me 取回最新用户信息
                    # （放在后台线程执行，避免阻塞 UI）
                    ok_me, me = self.client.get_me(token)
                    user = me if (ok_me and isinstance(me, dict)) else data.get("user", {})
                    self.done.emit(True, {"token": token, "user": user}, "login")
                    return
            # 登录失败（或响应异常）原样回传错误信息
            self.done.emit(ok, data, "login")
        elif self.mode == "register":
            ok, data = self.client.register(self.username, self.email, self.password)
            self.done.emit(ok, data, "register")
        else:  # token 绑定
            ok, me = self.client.get_me(self.token)
            if ok and isinstance(me, dict):
                self.done.emit(True, {"token": self.token, "user": me}, "token")
            else:
                self.done.emit(False, me if isinstance(me, dict) else {"error": "无效的 token"}, "token")


class LoginDialog(QDialog):
    """登录 / 注册 / token 绑定对话框，成功后 emit (token, user, remember)。"""

    login_success = pyqtSignal(str, dict, bool)

    def __init__(self, client: AuthClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self._busy = False
        self.setWindowTitle("登录 / 注册 - DTUG IDE")
        self.setMinimumWidth(400)
        self.setStyleSheet(
            "QDialog { background: #0b0e17; }"
            "QLabel { color: #e8edf5; }"
            "QLineEdit { background: #0d1117; color: #e8edf5; border: 1px solid #2a3a5a;"
            " border-radius: 6px; padding: 8px; }"
            "QPushButton { background: #1a2332; color: #e8edf5; border: 1px solid #2a3a5a;"
            " border-radius: 6px; padding: 8px 14px; }"
            "QPushButton:hover { border-color: #4f9cf7; }"
            "QPushButton#primary { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 #34d399, stop:1 #22d3ee); color: #0b0e17; font-weight: bold; }"
            "QPushButton#home { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 #6366f1, stop:1 #8b5cf6); color: #ffffff; font-weight: bold; }"
            "QCheckBox { color: #e8edf5; }"
            "QFrame#sep { background: #2a3a5a; }"
        )
        self._build_ui()
        self._set_mode("login")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 20, 20, 20)

        title = QLabel("DTUG IDE 账号")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #e8edf5;")
        root.addWidget(title)

        # ---- 方式 2：在浏览器中用主页账号登录（完全照搬 werewolf 跳转） ----
        self.home_btn = QPushButton("🌐 在浏览器中用主页账号登录")
        self.home_btn.setObjectName("home")
        self.home_btn.clicked.connect(self._open_home_login)
        root.addWidget(self.home_btn)

        # ---- 方式 2b：内嵌主页登录页（QWebEngineView，登录后自动回调） ----
        self.embed_btn = QPushButton("🪟 内嵌网页中登录（推荐）")
        self.embed_btn.setObjectName("home")
        self.embed_btn.clicked.connect(self._open_embedded_login)
        root.addWidget(self.embed_btn)

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

        # 分隔线
        sep = QLabel("")
        sep.setObjectName("sep")
        sep.setFixedHeight(1)
        root.addWidget(sep)

        # ---- 方式 1：内置账号密码登录 / 注册 ----
        form = QFormLayout()
        form.setSpacing(10)

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("用户名")
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("邮箱（可选，注册时填写）")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("密码")

        form.addRow("用户名", self.username_edit)
        form.addRow("邮箱", self.email_edit)
        form.addRow("密码", self.password_edit)
        root.addLayout(form)

        self.remember_check = QCheckBox("记住我（在此设备保持登录）")
        self.remember_check.setChecked(True)
        root.addWidget(self.remember_check)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #f87171; font-size: 13px;")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.toggle_btn = QPushButton("切换到注册")
        self.toggle_btn.setFlat(True)
        self.toggle_btn.setStyleSheet("color: #4f9cf7; border: none; padding: 4px;")
        self.toggle_btn.clicked.connect(lambda: self._set_mode("register" if self._mode == "login" else "login"))

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)

        self.submit_btn = QPushButton("登录")
        self.submit_btn.setObjectName("primary")
        self.submit_btn.clicked.connect(self._submit)

        btn_row.addWidget(self.toggle_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.submit_btn)
        root.addLayout(btn_row)

        # 回车快捷提交
        self.username_edit.returnPressed.connect(self._submit)
        self.password_edit.returnPressed.connect(self._submit)
        self.email_edit.returnPressed.connect(self._submit)
        self.token_edit.returnPressed.connect(self._bind_token)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode == "login":
            self.email_edit.hide()
            self.submit_btn.setText("登录")
            self.toggle_btn.setText("没有账号？注册")
        else:
            self.email_edit.show()
            self.submit_btn.setText("注册")
            self.toggle_btn.setText("已有账号？登录")
        self.error_label.clear()

    def _open_home_login(self) -> None:
        # 完全照搬 werewolf：打开主页登录页（带 #login 锚点唤起登录弹窗）
        QDesktopServices.openUrl(QUrl(HOME_LOGIN_URL))

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

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.submit_btn.setEnabled(not busy)
        self.toggle_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(not busy)
        self.token_bind_btn.setEnabled(not busy)
        self.home_btn.setEnabled(not busy)
        if busy:
            self.submit_btn.setText(label or "处理中...")
        else:
            self.submit_btn.setText("登录" if self._mode == "login" else "注册")

    def _submit(self) -> None:
        if self._busy:
            return
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            self._show_error("用户名和密码不能为空")
            return

        self._set_busy(True, "登录中..." if self._mode == "login" else "注册中...")
        self.error_label.clear()
        self._worker = _AuthWorker(
            self.client, self._mode, username, password, self.email_edit.text().strip()
        )
        self._worker.done.connect(self._on_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

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

        # 注册：服务端不返回 token（与网站一致），成功后引导登录
        if mode == "register":
            if not ok:
                self._show_error(str(data.get("error", "注册失败")))
                return
            saved_username = self.username_edit.text().strip()
            self.password_edit.clear()
            self._set_mode("login")
            if saved_username:
                self.username_edit.setText(saved_username)
            self._show_success("注册成功！请登录")
            return

        # 登录 / token 绑定
        if not ok:
            self._show_error(str(data.get("error", "登录失败")))
            return
        token = data.get("token")
        user = data.get("user") or {}
        if not token:
            self._show_error("服务器响应缺少 token")
            return
        user.setdefault("username", self.username_edit.text().strip())

        self.login_success.emit(token, user, self.remember_check.isChecked())
        self.accept()
