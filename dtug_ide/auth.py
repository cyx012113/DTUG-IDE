"""认证客户端：对接主页 auth-worker 登录 API（只读模仿 main / werewolf 的认证流程）。

主页认证系统基于 Cloudflare Worker：
    https://auth-worker.cyx012113.top
接口（与网站前端一致）：
    POST /login           body: {username, password, pub?}            -> {token, sid?, user}
    POST /register        body: {username, email, password}           -> {token}
    GET  /me              header: Bearer (+ X-Client-* 签名)           -> {username, email, tag, is_op, token?}
    PUT  /update-profile  header: Bearer + body: {username?, email?, currentPassword?, newPassword?}
                          -> {message, token?}（改名 / 改密会重签新 token，必须写回本地）
token 在网站端存于 localStorage 与根域 cookie；桌面端用 QSettings 持久化。

2026-10 主页新机制（本模块已对齐）：
  * **客户端绑定签名**：登录时把本机 ECDSA P-256 公钥（pub）交给服务端，服务端把
    sid 写进 JWT；之后每个带 Authorization 的请求都附带 X-Client-Sid/Sig/Ts/Nonce
    签名头（见 client_sig.py）。token  payload 里没有 sid、或 sid 与本机登记的
    不一致时自动降级为不签名（服务端兼容放行），不会误踢下线。
  * **滑动续签**：/me 在令牌剩余寿命不足 12h 时顺带下发新 token（响应含 token /
    token_refreshed 字段）——调用方**必须**写回本地凭证，否则 24h 后必掉线。
  * **改名**：update_profile 的 username 字段；服务端在用户名真变了时重签新 token。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .client_sig import default_signer

AUTH_BASE = "https://auth-worker.cyx012113.top"

# 主页地址（与 werewolf 的 HOME_URL 一致）
HOME_URL = "https://cyx012113.top"
# 模仿 werewolf 的登录跳转：打开主页并携带 #login 锚点唤起登录弹窗。
# 桌面端（localhost）读不到 .cyx012113.top 的共享 cookie，也无法从跳转 URL 拿到
# token，因此该按钮仅用于在浏览器中完成主页登录；token 需由用户粘贴回桌面端（见 LoginDialog）。
HOME_LOGIN_URL = HOME_URL + "/#login"

# Cloudflare 浏览器完整性检查会拦截默认 Python-urllib UA（Error 1010），
# 必须带上常见浏览器 UA 才能正常访问 auth-worker。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 返回类型：(ok, payload)。payload 为解析后的 JSON dict；失败时含 "error" 键。
AuthResult = Tuple[bool, Dict[str, Any]]


def _http(
    method: str,
    path: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
    timeout: float = 15.0,
) -> AuthResult:
    url = AUTH_BASE + path
    data = None
    headers = {"Accept": "application/json", "User-Agent": BROWSER_UA}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
        # 客户端绑定签名（2026-10 新机制）：token 里带 sid 且与本机私钥匹配时才发头，
        # 否则 default_signer().headers_for 返回 {}（服务端对无 sid 请求兼容放行）。
        headers.update(default_signer().headers_for(method, path, token))

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return True, payload
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        payload.setdefault("error", payload.get("error") or e.reason or f"HTTP {e.code}")
        return False, payload
    except Exception as e:  # 网络错误 / 超时 / DNS 等
        return False, {"error": str(e)}


class AuthClient:
    """主页认证 API 的轻量封装，使用标准库，无第三方依赖。"""

    def login(self, username: str, password: str, pub: Optional[Dict[str, Any]] = None) -> AuthResult:
        """登录。``pub`` 为本机客户端公钥 JWK（kty/crv/x/y）——带上即启用客户端
        绑定签名（服务端把 sid 写进 JWT）；不带你就是「未绑定会话」，服务端不验签。"""
        body: Dict[str, Any] = {"username": username, "password": password}
        if pub:
            body["pub"] = pub
        return _http("POST", "/login", json_body=body)

    def register(self, username: str, email: str, password: str) -> AuthResult:
        return _http(
            "POST",
            "/register",
            json_body={"username": username, "email": email or None, "password": password},
        )

    def get_me(self, token: str) -> AuthResult:
        """取当前用户信息。注意：响应可能含 ``token`` / ``token_refreshed``
        （滑动续签，令牌剩余不足 12h 时下发）——调用方必须把新 token 写回本地。"""
        return _http("GET", "/me", token=token)

    def update_profile(
        self,
        token: str,
        *,
        username: Optional[str] = None,
        email: Optional[str] = None,
        current_password: Optional[str] = None,
        new_password: Optional[str] = None,
    ) -> AuthResult:
        """修改资料。用户名（username）真发生变化、或修改密码时，服务端会重签
        新 token 并在响应里返回 ``token`` —— 调用方必须替换本地凭证，否则旧
        token 因 tv 递增立即失效（改密）或顶栏名字不刷新（改名）。"""
        body: Dict[str, Any] = {}
        if username is not None:
            body["username"] = username
        if email is not None:
            body["email"] = email or None
        if new_password:
            body["currentPassword"] = current_password
            body["newPassword"] = new_password
        return _http("PUT", "/update-profile", json_body=body, token=token)
