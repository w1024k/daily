"""pytest 公共夹具。

测试统一使用临时目录里的真实 SQLite 文件（而非 ``:memory:``），
这样多连接、事务、唯一索引的行为都和线上一致。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import repository, security
from app.config import Settings
from app.db import Database
from app.main import create_app

#: 测试里不需要真实强度，压低迭代次数让用例跑得快
TEST_ITERATIONS = 1000

PASSWORD = "secret-123"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "test.db"),
        password_iterations=TEST_ITERATIONS,
        session_ttl_seconds=3600,
    )


@pytest.fixture
def database(settings: Settings) -> Database:
    db = Database(settings.db_path)
    db.initialize()
    return db


@pytest.fixture
def conn(database: Database):
    connection = database.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def make_user(conn: sqlite3.Connection):
    """直接在库里造一个用户，返回其 id。"""

    def _make(username: str = "alice", password: str = PASSWORD) -> int:
        user = repository.create_user(
            conn,
            username,
            security.hash_password(password, iterations=TEST_ITERATIONS),
            "2026-01-01T00:00:00+00:00",
        )
        conn.commit()
        return user["id"]

    return _make


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def make_client(app):
    """可创建多个互相独立的客户端（各自持有独立 cookie）。"""
    clients: list[TestClient] = []

    def _make() -> TestClient:
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield _make

    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


@pytest.fixture
def register(make_client):
    """注册并返回已登录的客户端。"""

    def _register(username: str = "alice", password: str = PASSWORD) -> TestClient:
        client = make_client()
        response = client.post("/api/auth/register", json={"username": username, "password": password})
        assert response.status_code == 201, response.text
        return client

    return _register


@pytest.fixture
def alice(register) -> TestClient:
    return register("alice")
