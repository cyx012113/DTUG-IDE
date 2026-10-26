"""客户端绑定签名（Client-Bound Token）回归测试。

覆盖：
  * ClientSigner：密钥生成 / 持久化 / JWK 导出 / P1363 裸格式签名 / sid 护栏；
  * auth.AuthClient：签名头三态（无 sid / sid 不匹配 / 绑定后带签）；
  * chat.ChatClient：发送与轮询带签名、401 ⇒ auth_expired 且轮询自停；
  * embedded_login：注入脚本可生成且语法正确（node --check）。

说明：签名格式必须与主页 main/index.html 的 authHeaders()、
workers/client-sig-verify.js 逐字符一致（METHOD\\npath\\nts\\nnonce\\ndigest），
改动任一端都要同步本测试。
"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

# 建 QApplication 前必须设置（否则导入 QtWebEngine 相关模块会 ImportError）
from PyQt6.QtCore import Qt, QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

import dtug_ide.auth as auth_mod
import dtug_ide.chat_client as chat_mod
import dtug_ide.embedded_login as emb_mod
from dtug_ide.auth import AuthClient
from dtug_ide.chat_client import ChatClient
from dtug_ide.client_sig import ClientSigner, _b64url, token_sid

# ----------------------------------------------------------------------
# 假 chat-worker / auth-worker：记录请求头，可切换 401
# ----------------------------------------------------------------------
_RECORDED: list = []
_STATE = {"poll401": False}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _RECORDED.append(("GET", self.path, dict(self.headers)))
        if self.path.startswith("/poll"):
            if _STATE["poll401"]:
                self._json({"error": "未授权"}, 401)
            else:
                self._json({"success": True, "messages": [
                    {"id": 7, "room": "dm:a~b", "sender": "peer", "text": "hello", "created_at": 1}
                ]})
        else:
            self._json({"username": "tester", "is_op": False})

    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(ln)
        _RECORDED.append(("POST", self.path, dict(self.headers)))
        self._json({"success": True, "id": 1})


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def signer(tmp_path):
    s = ClientSigner(key_path=tmp_path / "client_key.json")
    # 三个模块各自持有 default_signer 引用，统一打桩到测试密钥
    auth_mod.default_signer = lambda: s
    chat_mod.default_signer = lambda: s
    emb_mod.default_signer = lambda: s
    _RECORDED.clear()
    _STATE["poll401"] = False
    yield s


def _make_jwt(sid):
    hdr = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    pl = _b64url(json.dumps({"uid": 1, "username": "tester", "sid": sid, "exp": 9999999999}).encode())
    return f"{hdr}.{pl}.{_b64url(b'sig')}"


TOK_BOUND = _make_jwt("sid-bound")
TOK_OTHER = _make_jwt("sid-other")
TOK_NOSID = ".".join([
    _b64url(json.dumps({"alg": "x"}).encode()),
    _b64url(json.dumps({"uid": 1}).encode()),
    "sig",
])


def _verify_sig(method, path, headers, token, pub):
    """服务端视角：公钥 + 消息重建 + P1363→DER 验签。"""
    digest = _b64url(hashlib.sha256(token.encode()).digest())
    msg = "\n".join([method, path, headers["X-Client-Ts"], headers["X-Client-Nonce"], digest]).encode()
    raw = base64.urlsafe_b64decode(headers["X-Client-Sig"] + "=" * (-len(headers["X-Client-Sig"]) % 4))
    assert len(raw) == 64, "ECDSA P-256 签名必须是 64 字节 P1363 裸格式"
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pubkey = ec.EllipticCurvePublicNumbers(
        int.from_bytes(base64.urlsafe_b64decode(pub["x"] + "=="), "big"),
        int.from_bytes(base64.urlsafe_b64decode(pub["y"] + "=="), "big"),
        ec.SECP256R1(),
    ).public_key()
    pubkey.verify(der, msg, ec.ECDSA(hashes.SHA256()))


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


# ----------------------------------------------------------------------
# ClientSigner 单元
# ----------------------------------------------------------------------
def test_signer_keygen_and_persist(signer):
    pub = signer.public_jwk()
    assert pub["kty"] == "EC" and pub["crv"] == "P-256" and "d" not in pub
    priv = signer.private_jwk()
    assert "d" in priv
    # 重载后一致
    again = ClientSigner(key_path=signer._key_path)
    assert again.public_jwk() == pub
    if os.name == "posix":
        assert (signer._key_path.stat().st_mode & 0o777) == 0o600


def test_signer_sid_guard(signer):
    tok = _make_jwt("sid-bound")
    assert signer.headers_for("GET", "/me", tok) == {}  # 未绑定 ⇒ 不签
    signer.bind_sid("sid-bound")
    h = signer.headers_for("POST", "https://chat-worker.cyx012113.top/send", tok)
    assert h["X-Client-Sid"] == "sid-bound" and h["X-Client-Sig"]
    # 别的 sid ⇒ 不签（防止误踢 + 防吊销）
    assert signer.headers_for("GET", "/me", _make_jwt("other")) == {}


def test_signer_signature_verifies(signer):
    pub = signer.public_jwk()
    signer.bind_sid("sid-bound")
    h = signer.headers_for("GET", "/poll?room=dm:a~b", TOK_BOUND)
    _verify_sig("GET", "/poll", h, TOK_BOUND, pub)  # 查询串不参与签名


def test_token_sid_edges():
    assert token_sid(TOK_BOUND) == "sid-bound"
    assert token_sid(TOK_NOSID) is None
    assert token_sid("") is None
    assert token_sid("a.b.c.d") is None


# ----------------------------------------------------------------------
# auth：签名头三态
# ----------------------------------------------------------------------
def test_auth_signature_states(signer, server, monkeypatch):
    monkeypatch.setattr(auth_mod, "AUTH_BASE", server)
    client = AuthClient()

    ok, _ = client.get_me(TOK_NOSID)
    assert ok and "X-Client-Sid" not in _RECORDED[-1][2]

    ok, _ = client.get_me(TOK_OTHER)
    assert ok and "X-Client-Sid" not in _RECORDED[-1][2]

    signer.bind_sid("sid-bound")
    ok, _ = client.get_me(TOK_BOUND)
    headers = _RECORDED[-1][2]
    assert ok and headers.get("X-Client-Sid") == "sid-bound"
    _verify_sig("GET", "/me", headers, TOK_BOUND, signer.public_jwk())


# ----------------------------------------------------------------------
# chat：带签发送 / 收消息 / 401 自停 / 降级
# ----------------------------------------------------------------------
def test_chat_signed_and_receive(signer, server, qapp):
    signer.bind_sid("sid-bound")  # 模拟「内嵌登录成功且确认本机密钥被注册」
    chat = ChatClient(base_url=server)
    chat.set_credentials(TOK_BOUND, "tester", False)
    chat.open_room("dm:a~b")
    got = []
    chat.message_received.connect(got.append)
    chat.send_text("你好")
    chat.start()
    _wait(2200)
    chat.stop()

    sends = [r for r in _RECORDED if r[0] == "POST" and r[1] == "/send"]
    polls = [r for r in _RECORDED if r[0] == "GET" and r[1].startswith("/poll")]
    assert sends and polls
    for _m, _p, h in sends + polls:
        assert h.get("X-Client-Sid") == "sid-bound"
    _verify_sig("GET", "/poll", polls[0][2], TOK_BOUND, signer.public_jwk())
    assert got and got[0]["text"] == "hello"


def test_chat_401_stops_polling(signer, server, qapp):
    signer.bind_sid("sid-bound")
    _STATE["poll401"] = True
    _RECORDED.clear()
    chat = ChatClient(base_url=server)
    chat.set_credentials(TOK_BOUND, "tester", False)
    chat.open_room("dm:a~b")
    expired = []
    chat.auth_expired.connect(lambda: expired.append(True))
    chat.start()
    _wait(2500)
    chat.stop()
    _STATE["poll401"] = False

    assert expired, "401 必须触发 auth_expired"
    n = len([r for r in _RECORDED if r[0] == "GET" and r[1].startswith("/poll")])
    assert n <= 2, f"失效后应停止轮询（实际 {n} 次）"


def test_chat_sid_mismatch_degrades(signer, server, qapp):
    _RECORDED.clear()
    chat = ChatClient(base_url=server)
    chat.set_credentials(TOK_OTHER, "tester", False)  # sid 不属于本机
    chat.open_room("dm:a~b")
    chat.start()
    _wait(2200)
    chat.stop()
    polls = [r for r in _RECORDED if r[0] == "GET" and r[1].startswith("/poll")]
    assert polls and "X-Client-Sid" not in polls[0][2]


# ----------------------------------------------------------------------
# embedded_login：注入脚本
# ----------------------------------------------------------------------
def test_client_key_hook_script(signer):
    js = emb_mod.build_client_key_hook_source()
    assert "__PRIV_JWK__" not in js and "__PUB_JWK__" not in js
    pub = signer.public_jwk()
    assert pub["x"] in js and pub["y"] in js
    assert "generateKey" in js
    node = shutil.which(os.environ.get("NODE", "node"))
    if node:  # 有 node 就做语法校验（CI / 无 node 环境跳过）
        f = Path(tempfile.mkdtemp()) / "hook.js"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"注入脚本语法错误：{r.stderr}"
