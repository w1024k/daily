"""密码哈希与会话令牌。

只用标准库实现，避免额外依赖（部署更省事）：
- 口令：PBKDF2-HMAC-SHA256 + 随机盐，存储格式 ``pbkdf2_sha256$迭代次数$盐$摘要``，
  迭代次数随哈希一起保存，将来调高强度也不会让老密码失效。
- 令牌：``secrets.token_urlsafe``，密码学安全随机数。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 260_000
SALT_BYTES = 16
TOKEN_BYTES = 32


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    """把明文口令转成可安全落库的哈希字符串。"""
    if not isinstance(password, str) or password == "":
        raise ValueError("口令不能为空")
    if iterations < 1:
        raise ValueError("迭代次数必须为正整数")

    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """校验口令。哈希串损坏或格式不对时一律返回 False，不抛异常。"""
    if not isinstance(password, str) or not isinstance(stored, str):
        return False

    parts = stored.split("$")
    if len(parts) != 4:
        return False
    algorithm, iterations_raw, salt_raw, digest_raw = parts
    if algorithm != ALGORITHM:
        return False
    try:
        iterations = int(iterations_raw)
        salt = _b64decode(salt_raw)
        expected = _b64decode(digest_raw)
    except (ValueError, TypeError, binascii.Error):
        return False
    if iterations < 1 or not salt or not expected:
        return False

    try:
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    except (ValueError, OverflowError):
        return False
    # 定长比较，避免时序侧信道
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


#: 供「用户不存在」时做一次等价耗时的假校验，避免通过响应时间枚举用户名。
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16), iterations=DEFAULT_ITERATIONS)


def dummy_verify(password: str) -> bool:
    """恒定返回 False，但消耗与真实校验相当的时间。"""
    verify_password(password, _DUMMY_HASH)
    return False
