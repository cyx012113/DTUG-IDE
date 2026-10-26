"""客户端绑定签名（Client-Bound Token）——与主页 ``main/index.html`` 的 authHeaders() 对齐。

背景（2026-10 主页新机制）：
    auth-worker 登录接口接受客户端公钥（EC P-256 JWK），把服务端生成的 ``sid``
    写进 JWT payload；此后每个带 ``Authorization`` 的请求都要附带
    ``X-Client-Sid / X-Client-Sig / X-Client-Ts / X-Client-Nonce``，
    对 ``METHOD\\npathname\\nts\\nnonce\\nbase64url(sha256(token))`` 签名。
    令牌即便被偷，没有本机私钥也签不出签名 ⇒ 偷来的 token 在别的机器上不可用。

本模块职责：
    * 在本机生成 / 持久化一对 ECDSA P-256 密钥（私钥文件权限收紧到仅本人可读写）；
    * 导出注册用公钥 JWK（只含 kty/crv/x/y，绝不含私钥分量 d）；
    * 按服务端 ``client-sig-verify.js`` 的格式签名（注意 WebCrypto 的 ECDSA
      签名是 P1363 裸 ``r||s`` 格式，而 cryptography 默认出 DER，必须转换）；
    * 解析 JWT payload 取 ``sid``，并做「只签自己的会话」的匹配护栏。

护栏（与主页一致，抄自 index.html 的 ckTokenSid 注释）：
    * 只有 token payload 里有 sid、且与本地登记的 sid **一致**时才发签名头；
      不一致（换了密钥 / 旧令牌 / 别的客户端登录）时完全不带 ``X-Client-*``，
      服务端对无 sid 请求走兼容放行，绝不会误踢自己下线；
    * 签名构造任何一步失败 ⇒ 一律降级为不签名（与主页 Edge 隐私模式降级一致）；
    * 私钥文件丢失 / 重建 ⇒ 登记的 sid 一并失效 ⇒ 自动回到不签名，直到重新登录。

存储位置：``<AppData>/DTUG-IDE/client_key.json``（Windows 即
``%APPDATA%\\DTUG\\IDE\\client_key.json``，QSettings 同域，随用户走）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

_KEY_VERSION = 1
_P256_COORD_SIZE = 32  # P-256 坐标 / 标量各 32 字节


# ----------------------------------------------------------------------
# base64url / JWK 编解码工具
# ----------------------------------------------------------------------
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_to_int(value: str) -> int:
    s = str(value or "")
    s += "=" * (-len(s) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(s), "big")


def _int_to_b64url(n: int) -> str:
    return _b64url(int(n).to_bytes(_P256_COORD_SIZE, "big"))


def _private_to_jwk(key: ec.EllipticCurvePrivateKey) -> Dict[str, str]:
    nums = key.private_numbers()
    pub = nums.public_numbers
    return {
        "kty": "EC",
        "crv": "P-256",
        "d": _int_to_b64url(nums.private_value),
        "x": _int_to_b64url(pub.x),
        "y": _int_to_b64url(pub.y),
    }


def _jwk_to_private(jwk: Dict[str, Any]) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(_b64url_to_int(jwk["d"]), ec.SECP256R1())


def token_sid(token: str) -> Optional[str]:
    """从 JWT payload 中取出 ``sid``（没有则返回 None；解析失败同样 None）。"""
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return None
        seg = parts[1]
        seg += "=" * (-len(seg) % 4)
        payload = json.loads(base64.urlsafe_b64decode(seg).decode("utf-8"))
        sid = payload.get("sid") if isinstance(payload, dict) else None
        return str(sid) if sid else None
    except Exception:
        return None


def default_key_dir() -> Path:
    """私钥存放目录：Qt 的 AppDataLocation（``%APPDATA%\\DTUG\\DTUG IDE``），
    无 Qt 环境时按同一布局退回环境变量。"""
    try:
        from PyQt6.QtCore import QStandardPaths

        loc = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        if loc:
            return Path(loc)
    except Exception:
        pass
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "DTUG" / "DTUG IDE"


def _pathname_only(path_or_url: str) -> str:
    """取路径部分（服务端只对 pathname 签名，查询串不参与）。"""
    p = str(path_or_url or "")
    if "://" in p:
        rest = p.split("://", 1)[1]
        p = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    return p.split("?", 1)[0].split("#", 1)[0]


# ----------------------------------------------------------------------
# 签名器
# ----------------------------------------------------------------------
class ClientSigner:
    """本机 ECDSA P-256 客户端密钥 + 请求签名器（进程内可共享单例）。"""

    def __init__(self, key_path: Optional[Path] = None, enabled: bool = True) -> None:
        self._key_path = key_path or (default_key_dir() / "client_key.json")
        self._enabled = bool(enabled)
        self._priv: Optional[ec.EllipticCurvePrivateKey] = None
        self._pub_jwk: Dict[str, str] = {}
        self._sid = ""
        self._loaded = False

    # ---------- 开关 ----------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)

    # ---------- 密钥生命周期 ----------
    def load_or_create(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if self._key_path.is_file():
                data = json.loads(self._key_path.read_text(encoding="utf-8"))
                priv_jwk = data.get("private_jwk") or {}
                self._priv = _jwk_to_private(priv_jwk)
                self._pub_jwk = dict(data.get("public_jwk") or {})
                self._sid = str(data.get("sid") or "")
                if self._pub_jwk.get("x") and self._pub_jwk.get("y"):
                    return
        except Exception:
            pass  # 文件损坏 / 字段缺失：重建（旧 sid 自然失效，安全降级）
        self._generate()

    def _generate(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        self._priv = key
        pub = key.public_key().public_numbers()
        self._pub_jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": _int_to_b64url(pub.x),
            "y": _int_to_b64url(pub.y),
        }
        self._sid = ""
        self._save()

    def _save(self) -> None:
        data = {
            "version": _KEY_VERSION,
            "created_at": int(time.time()),
            "private_jwk": _private_to_jwk(self._priv),  # type: ignore[arg-type]
            "public_jwk": self._pub_jwk,
            "sid": self._sid,
        }
        try:
            self._key_path.parent.mkdir(parents=True, exist_ok=True)
            self._key_path.write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )
            try:
                # POSIX 收紧到仅本人可读写；Windows 的 os.chmod 只能切只读位、
                # 无法表达 0o600（会写成 0o666），故此处为 best-effort ——
                # Windows 下 AppData 目录本身按用户 ACL 隔离（与浏览器
                # IndexedDB/localStorage 同级），已是对等防护。
                os.chmod(self._key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            except Exception:
                pass  # 权限设置失败不致命（文件系统不支持 / 策略限制）
        except Exception:
            pass  # 存不下不致命：仅本次进程有效，重新登录后重新绑定

    def public_jwk(self) -> Dict[str, str]:
        """注册用公钥（kty/crv/x/y；绝不含 d）。"""
        self.load_or_create()
        return dict(self._pub_jwk)

    def private_jwk(self) -> Dict[str, str]:
        """私钥 JWK（含 d）。仅用于注入本机内嵌浏览器完成密钥绑定，禁止外发。"""
        self.load_or_create()
        return _private_to_jwk(self._priv)  # type: ignore[arg-type]

    def bind_sid(self, sid: str) -> None:
        """登录成功且确认本机密钥被注册后，登记该会话的 sid。"""
        self.load_or_create()
        if sid and str(sid) != self._sid:
            self._sid = str(sid)
            self._save()

    def bound_sid(self) -> str:
        self.load_or_create()
        return self._sid

    # ---------- 签名 ----------
    def headers_for(self, method: str, path_or_url: str, token: str) -> Dict[str, str]:
        """构造 ``X-Client-*`` 请求头；任何条件不满足都返回 ``{}``（降级：不签名）。"""
        if not self._enabled:
            return {}
        try:
            self.load_or_create()
            token = str(token or "")
            if not token or self._priv is None:
                return {}
            sid = token_sid(token)
            # 令牌无 sid（旧令牌 / 未绑定会话）或不是本机会话 ⇒ 完全不带头，
            # 服务端走「无 sid 不验签」的兼容放行；乱发只会撞验签失败、累积吊销。
            if not sid or sid != self._sid:
                return {}
            ts = int(time.time())
            nonce = uuid.uuid4().hex
            digest = _b64url(hashlib.sha256(token.encode("utf-8")).digest())
            msg = "\n".join(
                [
                    str(method or "GET").upper(),
                    _pathname_only(path_or_url),
                    str(ts),
                    nonce,
                    digest,
                ]
            )
            der = self._priv.sign(  # type: ignore[union-attr]
                msg.encode("utf-8"), ec.ECDSA(hashes.SHA256())
            )
            r, s = decode_dss_signature(der)
            # WebCrypto / crypto.subtle.verify 只认 P1363 裸格式（r||s），
            # cryptography 默认输出 DER，必须手工转换，否则服务端全部验不过。
            raw = r.to_bytes(_P256_COORD_SIZE, "big") + s.to_bytes(
                _P256_COORD_SIZE, "big"
            )
            sig = _b64url(raw)
            if not sig:
                return {}
            return {
                "X-Client-Sid": sid,
                "X-Client-Ts": str(ts),
                "X-Client-Nonce": nonce,
                "X-Client-Sig": sig,
            }
        except Exception:
            return {}


_default_signer: Optional[ClientSigner] = None


def default_signer() -> ClientSigner:
    """进程级共享签名器（auth / chat 两条链路共用同一把密钥）。"""
    global _default_signer
    if _default_signer is None:
        _default_signer = ClientSigner()
    return _default_signer


__all__ = ["ClientSigner", "default_signer", "token_sid", "default_key_dir"]
