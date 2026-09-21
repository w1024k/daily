"""单元测试：数据访问层（纯 SQL 行为）。"""

from __future__ import annotations

import sqlite3

import pytest

from app import repository
from app.db import Database

T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-01-01T01:00:00+00:00"


@pytest.fixture
def alice_id(make_user) -> int:
    return make_user("alice")


@pytest.fixture
def bob_id(make_user) -> int:
    return make_user("bob")


class TestUsers:
    def test_create_returns_public_fields_only(self, conn):
        user = repository.create_user(conn, "alice", "hash", T0)
        assert user["username"] == "alice"
        assert user["created_at"] == T0
        assert user["id"] > 0
        # 口令哈希绝不能出现在对外结构里
        assert "password_hash" not in user

    def test_username_lookup_is_case_insensitive(self, conn):
        repository.create_user(conn, "Alice", "hash", T0)
        assert repository.get_user_by_username(conn, "alice") is not None
        assert repository.get_user_by_username(conn, "ALICE") is not None

    def test_database_enforces_unique_username(self, conn):
        """唯一索引建在 lower(username) 上，大小写不同也算重名。"""
        repository.create_user(conn, "Alice", "hash", T0)
        with pytest.raises(sqlite3.IntegrityError):
            repository.create_user(conn, "alice", "hash2", T0)

    def test_missing_user_returns_none(self, conn):
        assert repository.get_user_by_username(conn, "nobody") is None
        assert repository.get_user_by_id(conn, 9999) is None


class TestSessions:
    def test_round_trip(self, conn, alice_id):
        repository.create_session(conn, "token-1", alice_id, T0, T1)
        row = repository.get_session_user(conn, "token-1", T0)
        assert row is not None
        assert row["id"] == alice_id
        assert row["username"] == "alice"

    def test_expired_session_is_not_returned(self, conn, alice_id):
        repository.create_session(conn, "token-1", alice_id, T0, T1)
        # now 正好等于过期时刻 -> 视为过期
        assert repository.get_session_user(conn, "token-1", T1) is None

    def test_unknown_token_returns_none(self, conn):
        assert repository.get_session_user(conn, "nope", T0) is None

    def test_delete_session(self, conn, alice_id):
        repository.create_session(conn, "token-1", alice_id, T0, T1)
        assert repository.delete_session(conn, "token-1") == 1
        assert repository.get_session_user(conn, "token-1", T0) is None
        assert repository.delete_session(conn, "token-1") == 0

    def test_purge_expired_sessions(self, conn, alice_id):
        repository.create_session(conn, "old", alice_id, T0, T0)
        repository.create_session(conn, "fresh", alice_id, T0, T1)
        assert repository.delete_expired_sessions(conn, T0) == 1
        assert repository.get_session_user(conn, "fresh", T0) is not None


class TestTaskCreation:
    def test_new_task_defaults_to_pending(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "写周报", T0)
        assert task["content"] == "写周报"
        assert task["completed"] is False
        # 未完成的任务不携带完成时间（需求 7）
        assert task["completed_at"] is None
        assert task["created_at"] == T0

    def test_list_returns_newest_first(self, conn, alice_id):
        """新加的排在前面。"""
        repository.create_task(conn, alice_id, "第一件", T0)
        repository.create_task(conn, alice_id, "第二件", T0)
        repository.create_task(conn, alice_id, "第三件", T0)
        contents = [task["content"] for task in repository.list_tasks(conn, alice_id)]
        assert contents == ["第三件", "第二件", "第一件"]

    def test_newest_is_first_even_within_same_second(self, conn, alice_id):
        """创建时间精度只到秒，同一秒内新增也要靠 id 保证顺序。"""
        for index in range(5):
            repository.create_task(conn, alice_id, f"任务{index}", T0)
        contents = [task["content"] for task in repository.list_tasks(conn, alice_id)]
        assert contents == ["任务4", "任务3", "任务2", "任务1", "任务0"]

    def test_list_is_scoped_to_owner(self, conn, alice_id, bob_id):
        repository.create_task(conn, alice_id, "alice 的任务", T0)
        repository.create_task(conn, bob_id, "bob 的任务", T0)
        assert [t["content"] for t in repository.list_tasks(conn, alice_id)] == ["alice 的任务"]
        assert [t["content"] for t in repository.list_tasks(conn, bob_id)] == ["bob 的任务"]

    def test_list_is_empty_for_new_user(self, conn, alice_id):
        assert repository.list_tasks(conn, alice_id) == []


class TestTaskCompletion:
    def test_complete_sets_timestamp(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "倒垃圾", T0)
        changed = repository.set_task_completed(conn, task["id"], alice_id, True, T1)
        assert changed == 1

        updated = repository.task_to_dict(repository.get_task(conn, task["id"], alice_id))
        assert updated["completed"] is True
        assert updated["completed_at"] == T1

    def test_uncomplete_clears_timestamp(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "倒垃圾", T0)
        repository.set_task_completed(conn, task["id"], alice_id, True, T1)
        repository.set_task_completed(conn, task["id"], alice_id, False, None)

        row = repository.get_task(conn, task["id"], alice_id)
        assert row["completed"] == 0
        assert row["completed_at"] is None

    def test_cannot_complete_other_users_task(self, conn, alice_id, bob_id):
        task = repository.create_task(conn, alice_id, "alice 的任务", T0)
        assert repository.set_task_completed(conn, task["id"], bob_id, True, T1) == 0
        assert repository.get_task(conn, task["id"], alice_id)["completed"] == 0

    def test_cannot_complete_deleted_task(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "待删除", T0)
        repository.soft_delete_task(conn, task["id"], alice_id, T1)
        assert repository.set_task_completed(conn, task["id"], alice_id, True, T1) == 0

    def test_completed_flag_is_constrained_to_0_or_1(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "任务", T0)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET completed = 7 WHERE id = ?", (task["id"],))


class TestSoftDelete:
    def test_delete_only_marks_timestamp(self, conn, alice_id):
        """需求 9：逻辑删除，数据行必须保留。"""
        task = repository.create_task(conn, alice_id, "要删掉的", T0)
        assert repository.soft_delete_task(conn, task["id"], alice_id, T1) == 1

        # 列表里看不见了
        assert repository.list_tasks(conn, alice_id) == []
        assert repository.get_task(conn, task["id"], alice_id) is None

        # 但数据行还在，deleted_at 已写入
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
        assert row is not None
        assert row["content"] == "要删掉的"
        assert row["deleted_at"] == T1

    def test_delete_is_not_repeatable(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "要删掉的", T0)
        assert repository.soft_delete_task(conn, task["id"], alice_id, T1) == 1
        assert repository.soft_delete_task(conn, task["id"], alice_id, T1) == 0

    def test_cannot_delete_other_users_task(self, conn, alice_id, bob_id):
        task = repository.create_task(conn, alice_id, "alice 的任务", T0)
        assert repository.soft_delete_task(conn, task["id"], bob_id, T1) == 0
        assert repository.get_task(conn, task["id"], alice_id) is not None

    def test_delete_keeps_history_of_completed_task(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "已完成再删除", T0)
        repository.set_task_completed(conn, task["id"], alice_id, True, T1)
        repository.soft_delete_task(conn, task["id"], alice_id, T1)

        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
        assert row["completed"] == 1
        assert row["completed_at"] == T1
        assert row["deleted_at"] == T1


class TestTaskOrdering:
    def test_new_task_gets_front_position(self, conn, alice_id):
        """sort_order 递增，新任务列表里排最前。"""
        first = repository.create_task(conn, alice_id, "第一件", T0)
        second = repository.create_task(conn, alice_id, "第二件", T0)
        assert [t["id"] for t in repository.list_tasks(conn, alice_id)] == [second["id"], first["id"]]

    def test_sort_order_is_scoped_per_user(self, conn, alice_id, bob_id):
        """两个用户的 sort_order 各自计数，互不影响。"""
        repository.create_task(conn, alice_id, "a1", T0)
        repository.create_task(conn, bob_id, "b1", T0)
        repository.create_task(conn, alice_id, "a2", T0)
        assert [t["content"] for t in repository.list_tasks(conn, alice_id)] == ["a2", "a1"]
        assert [t["content"] for t in repository.list_tasks(conn, bob_id)] == ["b1"]

    def test_reorder_rewrites_positions(self, conn, alice_id):
        a = repository.create_task(conn, alice_id, "a", T0)
        b = repository.create_task(conn, alice_id, "b", T0)
        c = repository.create_task(conn, alice_id, "c", T0)
        assert repository.reorder_tasks(conn, alice_id, [a["id"], c["id"], b["id"]]) is True
        assert [t["content"] for t in repository.list_tasks(conn, alice_id)] == ["a", "c", "b"]

    def test_reorder_fails_on_missing_task(self, conn, alice_id):
        a = repository.create_task(conn, alice_id, "a", T0)
        assert repository.reorder_tasks(conn, alice_id, [a["id"], 9999]) is False

    def test_reorder_ignores_other_users_task(self, conn, alice_id, bob_id):
        a = repository.create_task(conn, alice_id, "a", T0)
        b = repository.create_task(conn, bob_id, "b", T0)
        assert repository.reorder_tasks(conn, alice_id, [a["id"], b["id"]]) is False

    def test_reorder_ignores_deleted_task(self, conn, alice_id):
        a = repository.create_task(conn, alice_id, "a", T0)
        b = repository.create_task(conn, alice_id, "b", T0)
        repository.soft_delete_task(conn, b["id"], alice_id, T1)
        assert repository.reorder_tasks(conn, alice_id, [a["id"], b["id"]]) is False

    def test_legacy_database_is_upgraded_in_place(self, tmp_path):
        """旧库（无 sort_order / color 列）升级后：列补上，原展示顺序不变。"""
        path = str(tmp_path / "legacy.db")
        raw = sqlite3.connect(path)
        raw.execute(
            """
            CREATE TABLE tasks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                content      TEXT    NOT NULL,
                completed    INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT    NOT NULL,
                completed_at TEXT,
                deleted_at   TEXT
            )
            """
        )
        raw.executemany(
            "INSERT INTO tasks (user_id, content, completed, created_at) VALUES (1, ?, 0, ?)",
            [("旧任务一", T0), ("旧任务二", T0), ("旧任务三", T0)],
        )
        raw.commit()
        raw.close()

        Database(path).initialize()
        Database(path).initialize()  # 幂等：重复初始化不报错、不改变结果

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
            assert {"sort_order", "color"} <= columns
            rows = conn.execute("SELECT id, sort_order, color FROM tasks ORDER BY id").fetchall()
            # 回填 sort_order = id：此前按 id DESC 展示，升级后顺序不变
            assert all(row["sort_order"] == row["id"] for row in rows)
            assert all(row["color"] is None for row in rows)
        finally:
            conn.close()


class TestTaskColor:
    def test_new_task_has_no_color(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "无颜色", T0)
        assert task["color"] is None

    def test_set_color(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "买牛奶", T0)
        assert repository.set_task_color(conn, task["id"], alice_id, "red") == 1
        updated = repository.task_to_dict(repository.get_task(conn, task["id"], alice_id))
        assert updated["color"] == "red"

    def test_clear_color(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "买牛奶", T0)
        repository.set_task_color(conn, task["id"], alice_id, "green")
        assert repository.set_task_color(conn, task["id"], alice_id, None) == 1
        updated = repository.task_to_dict(repository.get_task(conn, task["id"], alice_id))
        assert updated["color"] is None

    def test_cannot_color_other_users_task(self, conn, alice_id, bob_id):
        task = repository.create_task(conn, alice_id, "alice 的任务", T0)
        assert repository.set_task_color(conn, task["id"], bob_id, "red") == 0
        assert repository.get_task(conn, task["id"], alice_id)["color"] is None

    def test_cannot_color_deleted_task(self, conn, alice_id):
        task = repository.create_task(conn, alice_id, "待删除", T0)
        repository.soft_delete_task(conn, task["id"], alice_id, T1)
        assert repository.set_task_color(conn, task["id"], alice_id, "red") == 0
