"""SQLite 连接与建表。

直接用标准库 ``sqlite3``，不引入 ORM：依赖少、部署简单，SQL 也一目了然。
每个请求开一条连接（SQLite 连接开销极低），避免多线程共享连接的问题。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_STATEMENTS: tuple[str, ...] = (
    # 用户：只需要账号密码，不做角色/权限
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT    NOT NULL,
        password_hash TEXT    NOT NULL,
        created_at    TEXT    NOT NULL
    )
    """,
    # 用户名大小写不敏感（"Alice" 和 "alice" 视为同一个账号）
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_nocase ON users(lower(username))",
    # 会话：服务端保存，可随时失效
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token      TEXT    PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT    NOT NULL,
        expires_at TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
    # 任务：deleted_at 非空即逻辑删除
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content      TEXT    NOT NULL,
        completed    INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
        created_at   TEXT    NOT NULL,
        completed_at TEXT,
        deleted_at   TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_user_active ON tasks(user_id, deleted_at, id)",
)


class Database:
    """一个 SQLite 文件的薄封装。"""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        # WAL 让读写并发更友好（:memory: 下会被忽略）
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        """建表。幂等，可重复调用。"""
        conn = self.connect()
        try:
            with conn:
                for statement in SCHEMA_STATEMENTS:
                    conn.execute(statement)
        finally:
            conn.close()
