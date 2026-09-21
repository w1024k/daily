"""业务规则层。

夹在 HTTP 层（``main.py``）和 SQL 层（``repository.py``）之间，
不依赖 FastAPI，因此可以被单元测试直接调用。
出错时抛 :class:`ServiceError`，由 HTTP 层统一转成 JSON 响应。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import repository, security
from .util import iso_after, now_iso

MAX_USERNAME_LENGTH = 32
MIN_USERNAME_LENGTH = 2
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 128
MAX_CONTENT_LENGTH = 200
TASK_COLORS = ("red", "yellow", "green")


class ServiceError(Exception):
    """业务错误，``status_code`` 决定 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ValidationFailed(ServiceError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 422)


class UsernameTaken(ServiceError):
    def __init__(self, message: str = "该用户名已被注册") -> None:
        super().__init__(message, 409)


class InvalidCredentials(ServiceError):
    def __init__(self, message: str = "用户名或密码不正确") -> None:
        super().__init__(message, 401)


class NotFound(ServiceError):
    def __init__(self, message: str = "任务不存在") -> None:
        super().__init__(message, 404)


class NotAuthenticated(ServiceError):
    def __init__(self, message: str = "未登录或登录已过期") -> None:
        super().__init__(message, 401)


# --------------------------------------------------------------------------
# 用户 / 会话
# --------------------------------------------------------------------------


def _clean_username(username: Any) -> str:
    if not isinstance(username, str):
        raise ValidationFailed("用户名不能为空")
    username = username.strip()
    if len(username) < MIN_USERNAME_LENGTH:
        raise ValidationFailed(f"用户名至少 {MIN_USERNAME_LENGTH} 个字符")
    if len(username) > MAX_USERNAME_LENGTH:
        raise ValidationFailed(f"用户名最长 {MAX_USERNAME_LENGTH} 个字符")
    if any(char.isspace() for char in username):
        raise ValidationFailed("用户名不能包含空格")
    return username


def _check_password(password: Any) -> str:
    if not isinstance(password, str) or password == "":
        raise ValidationFailed("密码不能为空")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailed(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationFailed(f"密码最长 {MAX_PASSWORD_LENGTH} 位")
    return password


def register_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    *,
    iterations: int = security.DEFAULT_ITERATIONS,
) -> dict[str, Any]:
    """注册新用户。用户名大小写不敏感，重名返回 409。"""
    username = _clean_username(username)
    password = _check_password(password)

    if repository.get_user_by_username(conn, username) is not None:
        raise UsernameTaken()

    password_hash = security.hash_password(password, iterations=iterations)
    try:
        with conn:
            return repository.create_user(conn, username, password_hash, now_iso())
    except sqlite3.IntegrityError:
        # 并发注册同一用户名时由唯一索引兜底
        raise UsernameTaken() from None


def authenticate(conn: sqlite3.Connection, username: Any, password: Any) -> dict[str, Any] | None:
    """校验账号密码。失败返回 None（不区分「用户不存在」和「密码错误」）。"""
    if not isinstance(username, str) or not isinstance(password, str):
        security.dummy_verify("")
        return None

    row = repository.get_user_by_username(conn, username.strip())
    if row is None:
        # 走一次等价耗时的假校验，避免用响应时间枚举用户名
        security.dummy_verify(password)
        return None
    if not security.verify_password(password, row["password_hash"]):
        return None
    return repository.user_to_dict(row)


def login(conn: sqlite3.Connection, username: Any, password: Any) -> dict[str, Any]:
    user = authenticate(conn, username, password)
    if user is None:
        raise InvalidCredentials()
    return user


def create_session(conn: sqlite3.Connection, user_id: int, *, ttl_seconds: int) -> str:
    """建立登录会话，返回会话令牌。"""
    token = security.new_session_token()
    with conn:
        repository.create_session(conn, token, user_id, now_iso(), iso_after(ttl_seconds))
    return token


def resolve_session(conn: sqlite3.Connection, token: str | None) -> dict[str, Any] | None:
    """根据令牌取当前用户，令牌缺失/过期/伪造都返回 None。"""
    if not token or not isinstance(token, str):
        return None
    row = repository.get_session_user(conn, token, now_iso())
    return repository.user_to_dict(row) if row is not None else None


def destroy_session(conn: sqlite3.Connection, token: str | None) -> None:
    if not token or not isinstance(token, str):
        return
    with conn:
        repository.delete_session(conn, token)


def require_user(conn: sqlite3.Connection, token: str | None) -> dict[str, Any]:
    user = resolve_session(conn, token)
    if user is None:
        raise NotAuthenticated()
    return user


# --------------------------------------------------------------------------
# 任务
# --------------------------------------------------------------------------


def _clean_content(content: Any) -> str:
    """任务内容统一去掉首尾空白，并做长度校验（需求 1：一句话描述）。"""
    if not isinstance(content, str):
        raise ValidationFailed("任务内容不能为空")
    content = content.strip()
    if not content:
        raise ValidationFailed("任务内容不能为空")
    if len(content) > MAX_CONTENT_LENGTH:
        raise ValidationFailed(f"任务内容最长 {MAX_CONTENT_LENGTH} 个字符")
    return content


def list_tasks(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    """当前用户未删除的任务列表。"""
    return repository.list_tasks(conn, user_id)


def add_task(conn: sqlite3.Connection, user_id: int, content: Any) -> dict[str, Any]:
    content = _clean_content(content)
    with conn:
        return repository.create_task(conn, user_id, content, now_iso())


def get_task(conn: sqlite3.Connection, user_id: int, task_id: int) -> dict[str, Any]:
    row = repository.get_task(conn, task_id, user_id)
    if row is None:
        raise NotFound()
    return repository.task_to_dict(row)


def set_task_completed(conn: sqlite3.Connection, user_id: int, task_id: int, completed: Any) -> dict[str, Any]:
    """标记完成 / 取消完成（需求 4、8）。

    幂等：状态没有变化时直接返回原记录，保留最初的完成时间。
    """
    completed = bool(completed)
    row = repository.get_task(conn, task_id, user_id)
    if row is None:
        raise NotFound()
    if bool(row["completed"]) == completed:
        return repository.task_to_dict(row)

    with conn:
        repository.set_task_completed(
            conn,
            task_id,
            user_id,
            completed,
            now_iso() if completed else None,
        )

    updated = repository.get_task(conn, task_id, user_id)
    if updated is None:  # pragma: no cover - 理论上不会发生
        raise NotFound()
    return repository.task_to_dict(updated)


def delete_task(conn: sqlite3.Connection, user_id: int, task_id: int) -> None:
    """逻辑删除（需求 9）：只标记 deleted_at，数据保留。"""
    with conn:
        changed = repository.soft_delete_task(conn, task_id, user_id, now_iso())
    if changed == 0:
        raise NotFound()


def set_task_color(conn: sqlite3.Connection, user_id: int, task_id: int, color: Any) -> dict[str, Any]:
    """设置 / 清除任务标记色，返回更新后的任务。"""
    if color is not None and color not in TASK_COLORS:
        raise ValidationFailed("无效的标记颜色")
    with conn:
        changed = repository.set_task_color(conn, task_id, user_id, color)
    if changed == 0:
        raise NotFound()
    return get_task(conn, user_id, task_id)


def reorder_tasks(conn: sqlite3.Connection, user_id: int, ordered_ids: Any) -> None:
    """按给定 id 顺序重排任务。id 集合必须与当前任务完全一致，否则视为
    客户端数据过期，返回 422 让前端刷新。
    """
    if not isinstance(ordered_ids, list) or not ordered_ids:
        raise ValidationFailed("排序列表不能为空")
    if any(not isinstance(task_id, int) or isinstance(task_id, bool) for task_id in ordered_ids):
        raise ValidationFailed("排序列表格式不正确")
    if len(set(ordered_ids)) != len(ordered_ids):
        raise ValidationFailed("排序列表中有重复任务")

    current_ids = {task["id"] for task in repository.list_tasks(conn, user_id)}
    if set(ordered_ids) != current_ids:
        raise ValidationFailed("排序与当前任务不一致，请刷新后重试")

    if len(ordered_ids) == 1:
        return  # 只有一条任务，无需写入
    with conn:
        if not repository.reorder_tasks(conn, user_id, ordered_ids):
            # 校验之后任务被并发删除等极端情况：回滚并提示刷新
            raise ValidationFailed("排序与当前任务不一致，请刷新后重试")
