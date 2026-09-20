"""时间工具。

所有落库时间统一使用 UTC 的 ISO-8601 字符串（形如 ``2026-09-20T13:48:00+00:00``），
固定宽度 ⇒ 可以直接用字符串比较大小，省去解析开销。
前端负责转换成用户本地时间展示。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """转成秒级精度的 UTC ISO-8601 字符串。"""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def now_iso() -> str:
    return to_iso(utcnow())


def iso_after(seconds: int) -> str:
    return to_iso(utcnow() + timedelta(seconds=seconds))
