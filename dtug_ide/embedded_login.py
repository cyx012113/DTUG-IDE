"""内嵌主页登录页（QWebEngineView），通过回调 URL 捕获 token。

桌面端（localhost）读不到 .cyx012113.top 的共享 cookie，故主页在跳转到「外部」
（非 cyx012113.top）redirect 时会把 token 附加到 URL 上（见 D:\\Desktop\\网站\\main
的 appendTokenToExternalRedirect）。本模块加载主页登录页，拦截跳转到
http://127.0.0.1:<PORT>/callback?token=... 的导航，从而拿到 token 完成鉴权。

关键修复（内嵌登录页打不开 / 人机验证失败）：
  * 默认 QWebEngineView 的 User-Agent 含 "QtWebEngine" 字样，会被 Cloudflare
    的人机验证（Turnstile）与「浏览器完整性检查」拦截。这里在 userAgentForUrl 中
    改写为常规桌面 Chrome UA，使内嵌浏览器通过校验。
  * 显式开启 JavaScript / LocalStorage / WebGL 等必需的 Web 特性。
  * 监听加载失败与渲染进程异常，给出明确提示与「在浏览器中打开 / 刷新」回退。
"""

from __future__ import annotations

import http.server
import json
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlparse, parse_qs, quote

from PyQt6.QtCore import QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .auth import AuthClient, HOME_LOGIN_URL, HOME_URL
from .client_sig import default_signer, token_sid
from .theme import dialog_qss, ui_palette

CALLBACK_PORT = 8731
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PATH = "/callback"
CALLBACK_URL = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"

# 常规桌面 Chrome UA：去掉 QtWebEngine 标记，避免被 Cloudflare 误判为机器人。
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ----------------------------------------------------------------------
# 客户端绑定签名：把本机 ECDSA 密钥「借」给内嵌主页完成登录绑定
# ----------------------------------------------------------------------
# 主页（main/index.html）登录时会把 ``ensureClientKey()`` 生成的公钥随请求交给
# auth-worker，服务端把 sid 写进 JWT。桌面端要享用同一套「令牌被偷也无法异地
# 使用」的防护，就必须让登录注册的是**本机密钥** —— 而私钥在浏览器里是
# ``extractable=false`` 的 CryptoKey，页面导不出、Python 也拿不到。
#
# 解法：DocumentCreation 阶段（早于一切页面脚本）覆写 ``crypto.subtle.generateKey``：
# 主页调它时，返回由 Python 侧私钥 JWK **导入**成的一对 CryptoKey（私钥同样
# ``extractable=false``，页面内的 XSS 也导不走）。于是：
#   * 登录请求带上去的公钥 = 本机公钥 ⇒ sid 绑定本机会话；
#   * 页面自己之后的签名请求也用这把私钥（行为与原生完全一致）；
#   * Python 侧（client_sig.py）持有同一把私钥，可独立为聊天 / 鉴权请求签名。
#
# 降级：WebCrypto 不可用 / 注入失败时页面生成自己的密钥，登录照常成功，只是
# 桌面端请求不带 X-Client-*（服务端对无 sid 令牌兼容放行），绝不会卡登录。
_CLIENT_KEY_HOOK_TEMPLATE = """(function () {
  if (window.__dtugClientKeyHook) return;
  window.__dtugClientKeyHook = true;
  window.__dtugClientKeyMode = 'init';
  try {
    var subtle = window.crypto && window.crypto.subtle;
    if (!subtle || !subtle.importKey) { window.__dtugClientKeyMode = 'unsupported'; return; }
    var PRIV = __PRIV_JWK__;
    var PUB = __PUB_JWK__;
    var origImport = subtle.importKey.bind(subtle);
    var origGen = subtle.generateKey.bind(subtle);
    subtle.generateKey = function (alg, extractable, usages) {
      if (alg && alg.name === 'ECDSA') {
        return origImport('jwk', PRIV, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign'])
          .then(function (priv) {
            return origImport('jwk', PUB, { name: 'ECDSA', namedCurve: 'P-256' }, true, ['verify'])
              .then(function (pub) {
                window.__dtugClientKeyMode = 'bound';
                return { privateKey: priv, publicKey: pub };
              });
          });
      }
      return origGen(alg, extractable, usages);
    };
  } catch (e) {
    window.__dtugClientKeyMode = 'error';
  }
})();"""


def build_client_key_hook_source() -> str:
    """生成注入脚本源码（把本机公 / 私钥 JWK 内联进去）。"""
    signer = default_signer()
    priv = signer.private_jwk()
    pub = signer.public_jwk()
    # json.dumps 的输出是合法的 JS 对象字面量；内容仅含 base64url 字符与固定键名，
    # 不存在引号 / 反斜杠注入面。
    return _CLIENT_KEY_HOOK_TEMPLATE.replace(
        "__PRIV_JWK__", json.dumps(priv)
    ).replace("__PUB_JWK__", json.dumps(pub))


def build_login_url() -> QUrl:
    """构造主页登录 URL（内嵌网页与系统浏览器共用，确保都能经 redirect 回带 token）。

    关键点：``?redirect=`` 必须放在 ``#login`` 片段「之前」（查询串先于片段），
    否则主页从 ``window.location.search`` 读不到 redirect，token 永远不会被附加。
    redirect 内的 ``:`` 与 ``/`` 必须保留（safe=\":/\"），否则主页
    appendTokenToExternalRedirect 里的 ``new URL(redirect)`` 会把它误判为同域而不附加 token。
    """
    return QUrl(f"{HOME_URL}/?redirect=" + quote(CALLBACK_URL, safe=":/") + "#login")


class _CallbackPage(QWebEnginePage):
    """拦截跳转到本地回调 URL 的导航，从中提取 token（不真正发起请求）。"""

    token_found = pyqtSignal(str)
    load_failed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 关键：用真实 Chrome UA，绕过 Cloudflare 对 QtWebEngine 的标记
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ErrorPageEnabled, True)
        # 允许第三方（challenges.cloudflare.com）加载 Turnstile 资源（默认即允许，显式保持开启）

        # 客户端绑定签名注入（2026-10 新机制）：早于页面脚本覆写 generateKey，
        # 使登录注册的是本机 ECDSA 密钥；失败则页面走自己的密钥（优雅降级）。
        try:
            script = QWebEngineScript()
            script.setName("dtug-client-key-hook")
            script.setSourceCode(build_client_key_hook_source())
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(QWebEngineScript.MainWorld)
            script.setRunsOnSubFrames(False)
            self.scripts().insert(script)
        except Exception:
            pass

    def userAgentForUrl(self, url: QUrl) -> str:  # noqa: N802
        return CHROME_UA

    def acceptNavigationRequest(self, url: QUrl, nav_type, isMainFrame: bool) -> bool:  # noqa: N802
        parsed = urlparse(url.toString())
        if parsed.hostname == CALLBACK_HOST and parsed.path == CALLBACK_PATH:
            qs = parse_qs(parsed.query)
            tokens = qs.get("token")
            if tokens:
                self.token_found.emit(tokens[0])
            return False  # token 已捕获，取消该导航
        return super().acceptNavigationRequest(url, nav_type, isMainFrame)


class _CallbackServer(QThread):
    """本地回调服务器：监听 127.0.0.1:<CALLBACK_PORT>，捕获「系统浏览器」经
    redirect 跳回的 ``?token=...``。

    内嵌 QWebEngine 路径仍由 ``_CallbackPage.acceptNavigationRequest`` 拦截
    （不会真正发起请求），两条路径互斥、互不干扰：
      - 内嵌网页登录：导航被拦截 -> token_found 由页面发出；
      - 系统浏览器登录：真实导航到 localhost -> 本服务器收到请求并捕获 token。
    """

    token_found = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._server: "Optional[http.server.ThreadingHTTPServer]" = None

    def run(self) -> None:
        captured: Dict[str, str] = {}

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == CALLBACK_PATH:
                    qs = parse_qs(parsed.query)
                    tok = qs.get("token")
                    if tok:
                        captured["token"] = tok[0]
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(
                            "<html><head><meta charset='utf-8'></head><body>"
                            "<h2>DTUG IDE</h2><p>登录成功，token 已获取，可关闭此页面。</p>"
                            "</body></html>".encode("utf-8")
                        )
                        # 捕获后关闭服务器（在另一线程触发，避免阻塞 handler）
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                        return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body>DTUG IDE callback</body></html>")

            def log_message(self, *_a) -> None:  # noqa: N802
                pass

        try:
            srv = http.server.ThreadingHTTPServer((CALLBACK_HOST, CALLBACK_PORT), _Handler)
        except OSError:
            # 端口被占用（如上一次对话框未释放）：退化为仅内嵌路径可用
            return
        self._server = srv
        srv.serve_forever()
        if captured.get("token"):
            self.token_found.emit(captured["token"])

    def stop(self) -> None:
        srv = self._server
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass


class _TokenValidateWorker(QThread):
    """后台用 token 调 /me 校验并取回用户信息。

    done 信号第三参数为「最终采用的 token」——/me 滑动续签（剩余寿命不足 12h）
    会下发新令牌，必须回传给对话框，否则 24h 后必掉线。
    """

    done = pyqtSignal(bool, object, str)

    def __init__(self, client: AuthClient, token: str) -> None:
        super().__init__()
        self.client = client
        self.token = token

    def run(self) -> None:
        ok, me = self.client.get_me(self.token)
        if ok and isinstance(me, dict):
            # /me 滑动续签可能下发新 token：优先采用（见 login_dialog 同款修复）
            fresh = me.get("token")
            if isinstance(fresh, str) and fresh:
                self.token = fresh
            user = {k: v for k, v in me.items() if k not in ("token", "token_refreshed")}
            self.done.emit(True, user, self.token)
        else:
            self.done.emit(False, None, self.token)


class EmbeddedLoginDialog(QDialog):
    """内嵌主页登录页的对话框；登录成功后 emit (token, user)。"""

    login_success = pyqtSignal(str, dict)

    def __init__(self, client: AuthClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self._token: Optional[str] = None
        self.setWindowTitle("在主页登录 - DTUG IDE")
        self.resize(960, 680)
        self.setStyleSheet(dialog_qss(ui_palette()))
        self._build_ui()
        # 启动本地回调服务器：使「系统浏览器登录」也能经 redirect 自动取回 token
        self._server = _CallbackServer(self)
        self._server.token_found.connect(self._on_token)
        self._server.start()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView()
        self.page = _CallbackPage(self.view)
        self.view.setPage(self.page)
        self.page.token_found.connect(self._on_token)
        self.page.loadFinished.connect(self._on_load_finished)
        self.page.renderProcessTerminated.connect(self._on_render_terminated)
        root.addWidget(self.view, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(10, 8, 10, 8)
        self.status = QLabel("正在加载主页登录页…（若长时间无响应或卡在验证，可点右侧按钮换用浏览器登录）")
        self.status.setStyleSheet(f"color: {ui_palette()['text2']};")
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self._load)
        bar.addWidget(self.refresh_btn)

        self.browser_btn = QPushButton("🌐 在浏览器中打开")
        self.browser_btn.setObjectName("home")
        self.browser_btn.clicked.connect(self._open_in_browser)
        bar.addWidget(self.browser_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        bar.addWidget(self.cancel_btn)
        root.addLayout(bar)

    def _load(self) -> None:
        self.status.setText("正在加载主页登录页…")
        # 注意：redirect 参数必须保留 : 与 /，否则主页 appendTokenToExternalRedirect
        # 里的 new URL(redirect) 会把它误判为同域（cyx012113.top）而不附加 token，
        # 导致桌面端永远拿不到 token。这里只编码 ? # 等少数字符。
        # 关键：?redirect= 必须放在 #login 片段「之前」（查询串先于片段），
        # 否则主页从 window.location.search 读不到 redirect，token 永远不会被附加，
        # 桌面端也就无法自动获取 token。
        self.view.load(self._build_login_url())

    def _build_login_url(self) -> QUrl:
        """构造主页登录 URL（委托模块级 build_login_url）。"""
        return build_login_url()

    def _open_in_browser(self) -> None:
        # 用与内嵌相同的 redirect URL：主页登录成功后会把 token 附加到
        # http://127.0.0.1:8731/callback?token=...，本机回调服务器（_CallbackServer）
        # 即可捕获，于是「系统浏览器也能用 redirect 自动登录」。
        QDesktopServices.openUrl(self._build_login_url())
        self.status.setText("已用系统浏览器打开主页，请完成登录；成功后将自动返回本应用。")

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self.status.setText("请在上方主页完成登录；登录成功后将自动返回本应用。")
        else:
            self.status.setText("⚠️ 主页加载失败（网络/拦截）。可点「刷新」重试，或「在浏览器中打开」登录后粘贴 token。")

    def _on_render_terminated(self, _status, _code) -> None:
        self.status.setText("⚠️ 内嵌浏览器渲染进程异常。建议点「在浏览器中打开」完成登录。")

    def _on_token(self, token: str) -> None:
        if self._token is not None:
            return  # 已被内嵌/系统浏览器任一路径捕获，忽略重复信号
        self._token = token
        self.status.setText("已获取到 token，正在校验身份...")
        self.cancel_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self._bind_client_sid(token)
        self._worker = _TokenValidateWorker(self.client, token)
        self._worker.done.connect(self._on_validated)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _bind_client_sid(self, token: str) -> None:
        """登录成功后确认「本机密钥是否被注册」：是则把该会话 sid 登记到本地
        签名器（此后聊天 / 鉴权请求都带 X-Client-* 签名）；否则什么都不做
        （该令牌 sid 属于页面自己的密钥，桌面端走兼容放行，绝不能乱签 ——
        乱签会累积验签失败、触发服务端自动吊销会话）。"""

        def _cb(mode: Any) -> None:
            if str(mode) == "bound":
                sid = token_sid(token)
                if sid:
                    default_signer().bind_sid(sid)

        try:
            self.page.runJavaScript("window.__dtugClientKeyMode || ''", _cb)
        except Exception:
            pass

    def done(self, r: int) -> None:
        """对话框退出（accept / reject / 关闭）时停止本地回调服务器，释放端口。"""
        if getattr(self, "_server", None) is not None:
            self._server.stop()
        super().done(r)

    def _on_validated(self, ok: bool, user: Optional[Dict[str, Any]], token: str) -> None:
        if not ok or not isinstance(user, dict):
            self.status.setText("❌ token 校验失败，请重试或改用其他登录方式。")
            self.cancel_btn.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            self._load()  # 重新加载登录页
            return
        if token:
            self._token = token  # 采用滑动续签后的新令牌
        user.setdefault("username", "")
        self.login_success.emit(self._token or "", user)
        self.accept()
