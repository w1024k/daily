"""应用配置。

所有配置项都有合理的默认值，并且可以通过环境变量覆盖，
这样部署时不需要改动任何代码，也方便用 systemd / Docker 注入配置。
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 默认数据库位置：项目根目录下的 data/plan.db
DEFAULT_DB_PATH = BASE_DIR / "data" / "plan.db"

DAY = 24 * 60 * 60


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Settings:
    """运行时配置。

    构造参数用于测试等场景显式覆盖；不传则读取环境变量 / 默认值。
    """

    def __init__(
        self,
        *,
        db_path: str | None = None,
        session_ttl_seconds: int | None = None,
        password_iterations: int | None = None,
        cookie_name: str | None = None,
        cookie_secure: bool | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.db_path = str(db_path) if db_path is not None else _env_str("PLAN_DB_PATH", str(DEFAULT_DB_PATH))
        self.session_ttl_seconds = (
            session_ttl_seconds if session_ttl_seconds is not None else _env_int("PLAN_SESSION_TTL", 30 * DAY)
        )
        self.password_iterations = (
            password_iterations if password_iterations is not None else _env_int("PLAN_PBKDF2_ITERATIONS", 260_000)
        )
        self.cookie_name = cookie_name if cookie_name is not None else _env_str("PLAN_COOKIE_NAME", "plan_session")
        if cookie_secure is not None:
            self.cookie_secure = cookie_secure
        else:
            # 通过 HTTPS 反向代理部署时，把 PLAN_COOKIE_SECURE 设为 1
            self.cookie_secure = _env_str("PLAN_COOKIE_SECURE", "0") == "1"
        self.host = host if host is not None else _env_str("PLAN_HOST", "127.0.0.1")
        self.port = port if port is not None else _env_int("PLAN_PORT", 8000)

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return (
            f"Settings(db_path={self.db_path!r}, session_ttl_seconds={self.session_ttl_seconds}, "
            f"password_iterations={self.password_iterations}, cookie_name={self.cookie_name!r}, "
            f"cookie_secure={self.cookie_secure}, host={self.host!r}, port={self.port})"
        )
