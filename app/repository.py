"""数据访问层：只负责 SQL，不含业务规则。

所有针对任务的读写都必须带上 ``user_id`` 条件，从数据源头保证
「每个用户只能看到和管理自己的任务」。逻辑删除统一用
``deleted_at IS NULL`` 过滤。
"""

from __future__ import annotations

import sqlite3
from typing import Any

# --------------------------------------------------------------------------
# 行 -> 字典
# --------------------------------------------------------------------------


def user_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "created_at": row["created_at"],
    }


def task_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """对外表示：completed 转成 bool，未完成时 completed_at 为 None。

    需求 7：未完成的任务不显示完成时间。
    """
    completed = bool(row["completed"])
    return {
        "id": row["id"],
        "content": row["content"],
        "completed": completed,
        "created_at": row["created_at"],
        "completed_at": row["completed_at"] if completed else None,
        "color": row["color"] if "color" in row.keys() else None,
    }


# --------------------------------------------------------------------------
# 用户
# --------------------------------------------------------------------------


def create_user(conn: sqlite3.Connection, username: str, password_hash: str, created_at: str) -> dict[str, Any]:
    cursor = conn.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, password_hash, created_at),
    )
    row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return user_to_dict(row)


def get_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    """大小写不敏感地按用户名查用户。"""
    return conn.execute(
        "SELECT * FROM users WHERE lower(username) = lower(?)",
        (username,),
    ).fetchone()


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------


def create_session(conn: sqlite3.Connection, token: str, user_id: int, created_at: str, expires_at: str) -> None:
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, created_at, expires_at),
    )


def get_session_user(conn: sqlite3.Connection, token: str, now: str) -> sqlite3.Row | None:
    """取出未过期会话对应的用户；过期或不存在都返回 None。"""
    return conn.execute(
        """
        SELECT u.*
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ?
        """,
        (token, now),
    ).fetchone()


def delete_session(conn: sqlite3.Connection, token: str) -> int:
    cursor = conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    return cursor.rowcount


def delete_expired_sessions(conn: sqlite3.Connection, now: str) -> int:
    cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
    return cursor.rowcount


# --------------------------------------------------------------------------
# 任务
# --------------------------------------------------------------------------

_TASK_COLUMNS = "id, user_id, content, completed, created_at, completed_at, deleted_at, sort_order, color"


def create_task(conn: sqlite3.Connection, user_id: int, content: str, created_at: str) -> dict[str, Any]:
    """新任务排在列表最前面：sort_order 取当前用户最大值 + 1。

    同一秒内连续新增也能保证顺序稳定（created_at 只精确到秒，靠不住）。
    """
    cursor = conn.execute(
        """
        INSERT INTO tasks (user_id, content, completed, created_at, sort_order)
        VALUES (
            ?, ?, 0, ?,
            (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM tasks WHERE user_id = ?)
        )
        """,
        (user_id, content, created_at, user_id),
    )
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return task_to_dict(row)


def list_tasks(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    """当前用户未删除的任务，按用户拖动的顺序（sort_order 越大越靠前）。

    id 兜底：sort_order 相同（理论上是旧数据回填撞值）时新的在前。
    """
    rows = conn.execute(
        f"""
        SELECT {_TASK_COLUMNS}
        FROM tasks
        WHERE user_id = ? AND deleted_at IS NULL
        ORDER BY sort_order DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    return [task_to_dict(row) for row in rows]


def get_task(conn: sqlite3.Connection, task_id: int, user_id: int) -> sqlite3.Row | None:
    """按 id 取任务，且必须是该用户自己的、未被删除的。"""
    return conn.execute(
        f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (task_id, user_id),
    ).fetchone()


def set_task_completed(
    conn: sqlite3.Connection,
    task_id: int,
    user_id: int,
    completed: bool,
    completed_at: str | None,
) -> int:
    """更新完成状态；取消完成时把完成时间一并清空。返回受影响行数。"""
    cursor = conn.execute(
        """
        UPDATE tasks
        SET completed = ?, completed_at = ?
        WHERE id = ? AND user_id = ? AND deleted_at IS NULL
        """,
        (1 if completed else 0, completed_at, task_id, user_id),
    )
    return cursor.rowcount


def soft_delete_task(conn: sqlite3.Connection, task_id: int, user_id: int, deleted_at: str) -> int:
    """逻辑删除：只写 deleted_at，数据行保留。返回受影响行数。"""
    cursor = conn.execute(
        """
        UPDATE tasks
        SET deleted_at = ?
        WHERE id = ? AND user_id = ? AND deleted_at IS NULL
        """,
        (deleted_at, task_id, user_id),
    )
    return cursor.rowcount


def set_task_color(conn: sqlite3.Connection, task_id: int, user_id: int, color: str | None) -> int:
    """设置 / 清除任务标记色。返回受影响行数。"""
    cursor = conn.execute(
        """
        UPDATE tasks
        SET color = ?
        WHERE id = ? AND user_id = ? AND deleted_at IS NULL
        """,
        (color, task_id, user_id),
    )
    return cursor.rowcount


def reorder_tasks(conn: sqlite3.Connection, user_id: int, ordered_ids: list[int]) -> bool:
    """按给定顺序重写 sort_order（必须在事务中调用，由调用方 ``with conn``）。

    列表里第 0 项排最前面，对应最大的 sort_order。任何一条更新失败
    （任务已删除 / 不属于该用户）返回 False，调用方应回滚。
    """
    total = len(ordered_ids)
    for position, task_id in enumerate(ordered_ids):
        cursor = conn.execute(
            """
            UPDATE tasks
            SET sort_order = ?
            WHERE id = ? AND user_id = ? AND deleted_at IS NULL
            """,
            (total - position, task_id, user_id),
        )
        if cursor.rowcount != 1:
            return False
    return True
