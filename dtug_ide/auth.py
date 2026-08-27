"""认证客户端：对接主页 auth-worker 登录 API（只读模仿 main / werewolf 的认证流程）。

主页认证系统基于 Cloudflare Worker：
    https://auth-worker.cyx012113.top
接口（与网站前端一致）：
    POST /login           body: {username, password}                -> {token}
    POST /register        body: {username, email, password}         -> {token}
    GET  /me              header: Authorization: Bearer <token>     -> {username, email, tag, is_op}
    PUT  /update-profile  header: Bearer + body: {email, currentPassword?, newPassword?}
token 在网站端存于 localStorage 与根域 cookie；桌面端用 QSettings 持久化。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

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

    def login(self, username: str, password: str) -> AuthResult:
        return _http("POST", "/login", json_body={"username": username, "password": password})

    def register(self, username: str, email: str, password: str) -> AuthResult:
        return _http(
            "POST",
            "/register",
            json_body={"username": username, "email": email or None, "password": password},
        )

    def get_me(self, token: str) -> AuthResult:
        return _http("GET", "/me", token=token)

    def update_profile(
        self,
        token: str,
        *,
        email: Optional[str] = None,
        current_password: Optional[str] = None,
        new_password: Optional[str] = None,
    ) -> AuthResult:
        body: Dict[str, Any] = {}
        if email is not None:
            body["email"] = email or None
        if new_password:
            body["currentPassword"] = current_password
            body["newPassword"] = new_password
        return _http("PUT", "/update-profile", json_body=body, token=token)
