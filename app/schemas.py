"""请求 / 响应模型（pydantic）。

字段级校验在这里完成，业务规则在 ``services.py``，两层都做校验是为了
让接口在非法输入时返回清晰的 422 提示。
"""

from __future__ import annotations

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
    """目前只支持切换完成状态。

    ``strict=True``：只接受真正的 JSON 布尔值，避免 "yes" / 1 / "false"
    被宽松地转换成 bool，接口契约更明确。
    """

    completed: bool = Field(strict=True)


class TaskOut(BaseModel):
    id: int
    content: str
    completed: bool
    created_at: str
    completed_at: str | None = None


class TaskListOut(BaseModel):
    tasks: list[TaskOut]


class MessageOut(BaseModel):
    detail: str


class HealthOut(BaseModel):
    status: str
    version: str
