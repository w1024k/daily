"""FastAPI 依赖：数据库连接与当前登录用户。

FastAPI 会在一次请求内缓存依赖结果，所以同一个请求里
``Depends(get_db)`` 拿到的始终是同一条连接。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterator

from fastapi import Depends, HTTPException, Request, status

from . import services
from .config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn: sqlite3.Connection = request.app.state.db.connect()
    try:
        yield conn
    finally:
        conn.close()


def get_current_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """从 cookie 解析会话，未登录直接 401。"""
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name)
    user = services.resolve_session(conn, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期",
        )
    return user
