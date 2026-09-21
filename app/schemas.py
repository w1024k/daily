"""请求 / 响应模型（pydantic）。

字段级校验在这里完成，业务规则在 ``services.py``，两层都做校验是为了
让接口在非法输入时返回清晰的 422 提示。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from . import services


class RegisterRequest(BaseModel):
    username: str = Field(min_length=services.MIN_USERNAME_LENGTH, max_length=services.MAX_USERNAME_LENGTH)
    password: str = Field(min_length=services.MIN_PASSWORD_LENGTH, max_length=services.MAX_PASSWORD_LENGTH)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        value = value.strip()
        if len(value) < services.MIN_USERNAME_LENGTH:
            raise ValueError(f"用户名至少 {services.MIN_USERNAME_LENGTH} 个字符")
        if any(char.isspace() for char in value):
            raise ValueError("用户名不能包含空格")
        return value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=services.MAX_USERNAME_LENGTH)
    password: str = Field(min_length=1, max_length=services.MAX_PASSWORD_LENGTH)


class UserOut(BaseModel):
    id: int
    username: str
    created_at: str


class TaskCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=services.MAX_CONTENT_LENGTH)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("任务内容不能为空")
        return value


class TaskUpdateRequest(BaseModel):
    """PATCH 语义的部分更新：completed 切完成状态，color 设标记色。

    两个字段都可选；用 ``model_fields_set`` 区分「未提供」和「提供 null」，
    后者对 color 表示清除标记色。

    ``strict=True``：只接受真正的 JSON 布尔值，避免 "yes" / 1 / "false"
    被宽松地转换成 bool，接口契约更明确。
    """

    completed: bool | None = Field(default=None, strict=True)
    color: Literal["red", "yellow", "green"] | None = None

    @field_validator("completed")
    @classmethod
    def completed_must_be_bool(cls, value: bool | None) -> bool | None:
        # 字段缺省时不经过校验器；这里只拦住显式传 null 的请求。
        # 想表达「不修改」直接不传该字段即可。
        if value is None:
            raise ValueError("completed 必须是布尔值")
        return value


class TaskReorderRequest(BaseModel):
    """整体排序：ids 的顺序即新的展示顺序（第一项在最前面）。

    元素用 strict int：不接受 "1" / true 这类会被悄悄转换的值。
    """

    ids: list[Annotated[int, Field(strict=True)]]


class TaskOut(BaseModel):
    id: int
    content: str
    completed: bool
    created_at: str
    completed_at: str | None = None
    color: Literal["red", "yellow", "green"] | None = None


class TaskListOut(BaseModel):
    tasks: list[TaskOut]


class MessageOut(BaseModel):
    detail: str


class HealthOut(BaseModel):
    status: str
    version: str
