"""HTTP 层：路由定义。

用应用工厂 ``create_app()``，方便测试时注入临时数据库配置。
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, services
from .config import Settings
from .db import Database
from .deps import get_current_user, get_db, get_settings
from .schemas import (
    HealthOut,
    LoginRequest,
    MessageOut,
    RegisterRequest,
    TaskCreateRequest,
    TaskListOut,
    TaskOut,
    TaskReorderRequest,
    TaskUpdateRequest,
    UserOut,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    """写登录 cookie：HttpOnly，防 XSS 读取；SameSite=Lax，防 CSRF。"""
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(key=settings.cookie_name, path="/")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    database = Database(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.initialize()
        yield

    app = FastAPI(
        title="计划管理",
        description="一个轻量的个人计划管理应用：任务增删、完成状态切换、按用户隔离。",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.db = database

    # ------------------------------------------------------------------
    # 统一错误响应：业务异常 -> {"detail": "..."}
    # ------------------------------------------------------------------
    @app.exception_handler(services.ServiceError)
    async def handle_service_error(request: Request, exc: services.ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    # ------------------------------------------------------------------
    # 健康检查（给部署/反向代理探活用）
    # ------------------------------------------------------------------
    @app.get("/api/health", response_model=HealthOut, tags=["系统"], summary="健康检查")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # ------------------------------------------------------------------
    # 用户与会话
    # ------------------------------------------------------------------
    @app.post(
        "/api/auth/register",
        response_model=UserOut,
        status_code=status.HTTP_201_CREATED,
        tags=["用户"],
        summary="注册并自动登录",
    )
    def register(
        payload: RegisterRequest,
        response: Response,
        conn: sqlite3.Connection = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        user = services.register_user(
            conn,
            payload.username,
            payload.password,
            iterations=settings.password_iterations,
        )
        token = services.create_session(conn, user["id"], ttl_seconds=settings.session_ttl_seconds)
        _set_session_cookie(response, settings, token)
        return user

    @app.post("/api/auth/login", response_model=UserOut, tags=["用户"], summary="登录")
    def login(
        payload: LoginRequest,
        response: Response,
        conn: sqlite3.Connection = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        user = services.login(conn, payload.username, payload.password)
        token = services.create_session(conn, user["id"], ttl_seconds=settings.session_ttl_seconds)
        _set_session_cookie(response, settings, token)
        return user

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["用户"], summary="退出登录")
    def logout(
        request: Request,
        response: Response,
        conn: sqlite3.Connection = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        services.destroy_session(conn, request.cookies.get(settings.cookie_name))
        _clear_session_cookie(response, settings)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @app.get("/api/auth/me", response_model=UserOut, tags=["用户"], summary="当前登录用户")
    def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        return user

    # ------------------------------------------------------------------
    # 任务（全部按当前登录用户隔离）
    # ------------------------------------------------------------------
    @app.get("/api/tasks", response_model=TaskListOut, tags=["任务"], summary="任务列表")
    def list_tasks(
        user: dict[str, Any] = Depends(get_current_user),
        conn: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return {"tasks": services.list_tasks(conn, user["id"])}

    @app.post(
        "/api/tasks",
        response_model=TaskOut,
        status_code=status.HTTP_201_CREATED,
        tags=["任务"],
        summary="新增任务",
    )
    def create_task(
        payload: TaskCreateRequest,
        user: dict[str, Any] = Depends(get_current_user),
        conn: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return services.add_task(conn, user["id"], payload.content)

    @app.put(
        "/api/tasks/order",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["任务"],
        summary="拖动排序（整体重排）",
    )
    def reorder_tasks(
        payload: TaskReorderRequest,
        user: dict[str, Any] = Depends(get_current_user),
        conn: sqlite3.Connection = Depends(get_db),
    ) -> Response:
        services.reorder_tasks(conn, user["id"], payload.ids)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.patch(
        "/api/tasks/{task_id}",
        response_model=TaskOut,
        tags=["任务"],
        summary="标记完成 / 取消完成 / 设置标记色",
    )
    def update_task(
        task_id: int,
        payload: TaskUpdateRequest,
        user: dict[str, Any] = Depends(get_current_user),
        conn: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        if not payload.model_fields_set:
            raise services.ValidationFailed("至少需要提供一个要修改的字段")
        if "completed" in payload.model_fields_set and payload.completed is not None:
            services.set_task_completed(conn, user["id"], task_id, payload.completed)
        if "color" in payload.model_fields_set:
            services.set_task_color(conn, user["id"], task_id, payload.color)
        return services.get_task(conn, user["id"], task_id)

    @app.delete(
        "/api/tasks/{task_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["任务"],
        summary="删除任务（逻辑删除）",
    )
    def delete_task(
        task_id: int,
        user: dict[str, Any] = Depends(get_current_user),
        conn: sqlite3.Connection = Depends(get_db),
    ) -> Response:
        services.delete_task(conn, user["id"], task_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # 静态页面
    # ------------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()

if __name__ == "__main__":  # pragma: no cover - 方便 `python -m app.main`
    import uvicorn

    _settings = Settings()
    uvicorn.run(app, host=_settings.host, port=_settings.port)
