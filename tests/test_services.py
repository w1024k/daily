"""单元测试：业务规则层（不经过 HTTP）。"""

from __future__ import annotations

import pytest

from app import repository, security, services
from app.services import InvalidCredentials, NotFound, NotAuthenticated, UsernameTaken, ValidationFailed

ITER = 1000
PASSWORD = "secret-123"


class TestRegister:
    def test_registers_user(self, conn):
        user = services.register_user(conn, "alice", PASSWORD, iterations=ITER)
        assert user["username"] == "alice"
        assert user["id"] > 0

    def test_password_is_hashed_not_plaintext(self, conn):
        services.register_user(conn, "alice", PASSWORD, iterations=ITER)
        row = repository.get_user_by_username(conn, "alice")
        assert row["password_hash"] != PASSWORD
        assert security.verify_password(PASSWORD, row["password_hash"])

    def test_username_is_trimmed(self, conn):
        user = services.register_user(conn, "  alice  ", PASSWORD, iterations=ITER)
        assert user["username"] == "alice"

    def test_supports_chinese_username(self, conn):
        user = services.register_user(conn, "张三", PASSWORD, iterations=ITER)
        assert user["username"] == "张三"
        assert services.login(conn, "张三", PASSWORD)["id"] == user["id"]

    def test_duplicate_username_rejected(self, conn):
        services.register_user(conn, "alice", PASSWORD, iterations=ITER)
        with pytest.raises(UsernameTaken):
            services.register_user(conn, "alice", PASSWORD, iterations=ITER)

    def test_duplicate_check_ignores_case(self, conn):
        services.register_user(conn, "Alice", PASSWORD, iterations=ITER)
        with pytest.raises(UsernameTaken):
            services.register_user(conn, "alice", PASSWORD, iterations=ITER)

    @pytest.mark.parametrize("bad", ["", "   ", "a", "x" * 33, "with space", None, 123])
    def test_invalid_username_rejected(self, conn, bad):
        with pytest.raises(ValidationFailed):
            services.register_user(conn, bad, PASSWORD, iterations=ITER)

    @pytest.mark.parametrize("bad", ["", "12345", "x" * 129, None])
    def test_invalid_password_rejected(self, conn, bad):
        with pytest.raises(ValidationFailed):
            services.register_user(conn, "alice", bad, iterations=ITER)


class TestLogin:
    def test_login_success(self, conn):
        created = services.register_user(conn, "alice", PASSWORD, iterations=ITER)
        user = services.login(conn, "alice", PASSWORD)
        assert user["id"] == created["id"]

    def test_login_username_is_trimmed_and_case_insensitive(self, conn):
        services.register_user(conn, "Alice", PASSWORD, iterations=ITER)
        assert services.login(conn, "  alice  ", PASSWORD)["username"] == "Alice"

    def test_wrong_password_rejected(self, conn):
        services.register_user(conn, "alice", PASSWORD, iterations=ITER)
        with pytest.raises(InvalidCredentials):
            services.login(conn, "alice", "wrong-password")

    def test_unknown_user_rejected(self, conn):
        with pytest.raises(InvalidCredentials):
            services.login(conn, "nobody", PASSWORD)

    def test_authenticate_returns_none_instead_of_raising(self, conn):
        assert services.authenticate(conn, "nobody", PASSWORD) is None

    @pytest.mark.parametrize("username,password", [(None, PASSWORD), ("alice", None), (1, 2)])
    def test_non_string_credentials_rejected(self, conn, username, password):
        assert services.authenticate(conn, username, password) is None


class TestSessions:
    @pytest.fixture
    def user_id(self, conn) -> int:
        return services.register_user(conn, "alice", PASSWORD, iterations=ITER)["id"]

    def test_resolve_returns_owner(self, conn, user_id):
        token = services.create_session(conn, user_id, ttl_seconds=3600)
        user = services.resolve_session(conn, token)
        assert user is not None
        assert user["id"] == user_id

    def test_tokens_are_unique_per_login(self, conn, user_id):
        first = services.create_session(conn, user_id, ttl_seconds=3600)
        second = services.create_session(conn, user_id, ttl_seconds=3600)
        assert first != second
        # 两个会话同时有效，互不影响
        assert services.resolve_session(conn, first) is not None
        assert services.resolve_session(conn, second) is not None

    @pytest.mark.parametrize("token", [None, "", "forged-token", 123])
    def test_invalid_token_returns_none(self, conn, token):
        assert services.resolve_session(conn, token) is None

    def test_expired_session_returns_none(self, conn, user_id):
        token = services.create_session(conn, user_id, ttl_seconds=-1)
        assert services.resolve_session(conn, token) is None

    def test_destroy_invalidates_session(self, conn, user_id):
        token = services.create_session(conn, user_id, ttl_seconds=3600)
        services.destroy_session(conn, token)
        assert services.resolve_session(conn, token) is None

    def test_destroy_unknown_token_is_silent(self, conn):
        services.destroy_session(conn, "does-not-exist")  # 不抛异常

    def test_require_user_raises_when_anonymous(self, conn):
        with pytest.raises(NotAuthenticated):
            services.require_user(conn, None)


class TestAddTask:
    @pytest.fixture
    def user_id(self, conn) -> int:
        return services.register_user(conn, "alice", PASSWORD, iterations=ITER)["id"]

    def test_adds_pending_task(self, conn, user_id):
        task = services.add_task(conn, user_id, "写周报")
        assert task["content"] == "写周报"
        assert task["completed"] is False
        assert task["completed_at"] is None

    def test_content_is_trimmed(self, conn, user_id):
        task = services.add_task(conn, user_id, "  写周报  ")
        assert task["content"] == "写周报"

    def test_content_at_max_length_is_allowed(self, conn, user_id):
        content = "字" * services.MAX_CONTENT_LENGTH
        assert services.add_task(conn, user_id, content)["content"] == content

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", "\n\t ", None, 42, "字" * (services.MAX_CONTENT_LENGTH + 1)],
    )
    def test_invalid_content_rejected(self, conn, user_id, bad):
        with pytest.raises(ValidationFailed):
            services.add_task(conn, user_id, bad)

    def test_list_is_scoped_to_owner(self, conn, make_user):
        alice = make_user("alice")
        bob = make_user("bob")
        services.add_task(conn, alice, "alice 的任务")
        services.add_task(conn, bob, "bob 的任务")

        assert [t["content"] for t in services.list_tasks(conn, alice)] == ["alice 的任务"]
        assert [t["content"] for t in services.list_tasks(conn, bob)] == ["bob 的任务"]


class TestCompleteTask:
    @pytest.fixture
    def task(self, conn, make_user):
        user_id = make_user("alice")
        return user_id, services.add_task(conn, user_id, "倒垃圾")

    def test_complete_sets_completed_at(self, conn, task):
        user_id, item = task
        updated = services.set_task_completed(conn, user_id, item["id"], True)
        assert updated["completed"] is True
        assert updated["completed_at"] is not None

    def test_uncomplete_clears_completed_at(self, conn, task):
        """需求 7/8：取消完成后不再显示完成时间。"""
        user_id, item = task
        services.set_task_completed(conn, user_id, item["id"], True)
        reverted = services.set_task_completed(conn, user_id, item["id"], False)
        assert reverted["completed"] is False
        assert reverted["completed_at"] is None

    def test_repeated_complete_keeps_first_timestamp(self, conn, task):
        """幂等：重复标记完成不会刷新完成时间。"""
        user_id, item = task
        services.set_task_completed(conn, user_id, item["id"], True)
        conn.execute(
            "UPDATE tasks SET completed_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", item["id"]),
        )
        conn.commit()

        again = services.set_task_completed(conn, user_id, item["id"], True)
        assert again["completed_at"] == "2020-01-01T00:00:00+00:00"

    def test_toggle_round_trip_is_stable(self, conn, task):
        user_id, item = task
        for _ in range(3):
            services.set_task_completed(conn, user_id, item["id"], True)
            assert services.get_task(conn, user_id, item["id"])["completed"] is True
            services.set_task_completed(conn, user_id, item["id"], False)
            assert services.get_task(conn, user_id, item["id"])["completed"] is False

    def test_cannot_touch_other_users_task(self, conn, task, make_user):
        _, item = task
        intruder = make_user("bob")
        with pytest.raises(NotFound):
            services.set_task_completed(conn, intruder, item["id"], True)
        with pytest.raises(NotFound):
            services.get_task(conn, intruder, item["id"])

    def test_missing_task_raises(self, conn, task):
        user_id, _ = task
        with pytest.raises(NotFound):
            services.set_task_completed(conn, user_id, 999_999, True)


class TestDeleteTask:
    @pytest.fixture
    def task(self, conn, make_user):
        user_id = make_user("alice")
        return user_id, services.add_task(conn, user_id, "要删掉的")

    def test_delete_hides_task_but_keeps_row(self, conn, task):
        """需求 9：逻辑删除。"""
        user_id, item = task
        services.delete_task(conn, user_id, item["id"])

        assert services.list_tasks(conn, user_id) == []
        with pytest.raises(NotFound):
            services.get_task(conn, user_id, item["id"])

        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (item["id"],)).fetchone()
        assert row is not None and row["deleted_at"] is not None

    def test_delete_twice_raises_not_found(self, conn, task):
        user_id, item = task
        services.delete_task(conn, user_id, item["id"])
        with pytest.raises(NotFound):
            services.delete_task(conn, user_id, item["id"])

    def test_deleting_other_users_task_leaves_it_intact(self, conn, task, make_user):
        owner, item = task
        intruder = make_user("bob")
        with pytest.raises(NotFound):
            services.delete_task(conn, intruder, item["id"])
        assert [t["id"] for t in services.list_tasks(conn, owner)] == [item["id"]]

    def test_missing_task_raises(self, conn, task):
        user_id, _ = task
        with pytest.raises(NotFound):
            services.delete_task(conn, user_id, 999_999)


class TestTaskColor:
    @pytest.fixture
    def task(self, conn, make_user):
        user_id = make_user("alice")
        return user_id, services.add_task(conn, user_id, "需要标记的")

    def test_set_and_read_color(self, conn, task):
        user_id, item = task
        updated = services.set_task_color(conn, user_id, item["id"], "red")
        assert updated["color"] == "red"
        assert [t["color"] for t in services.list_tasks(conn, user_id)] == ["red"]

    def test_clear_color_with_none(self, conn, task):
        user_id, item = task
        services.set_task_color(conn, user_id, item["id"], "green")
        cleared = services.set_task_color(conn, user_id, item["id"], None)
        assert cleared["color"] is None

    @pytest.mark.parametrize("bad", ["blue", "RED", "", "red ", 3, True])
    def test_invalid_color_rejected(self, conn, task, bad):
        user_id, item = task
        with pytest.raises(ValidationFailed):
            services.set_task_color(conn, user_id, item["id"], bad)

    def test_color_survives_completed_toggle(self, conn, task):
        """切完成状态不能动标记色。"""
        user_id, item = task
        services.set_task_color(conn, user_id, item["id"], "yellow")
        done = services.set_task_completed(conn, user_id, item["id"], True)
        assert done["color"] == "yellow"

    def test_cannot_color_other_users_task(self, conn, task, make_user):
        _, item = task
        intruder = make_user("bob")
        with pytest.raises(NotFound):
            services.set_task_color(conn, intruder, item["id"], "red")

    def test_missing_task_raises(self, conn, task):
        user_id, _ = task
        with pytest.raises(NotFound):
            services.set_task_color(conn, user_id, 999_999, "red")


class TestReorderTasks:
    @pytest.fixture
    def three(self, conn, make_user):
        """三条任务，返回 (user_id, [第三, 第二, 第一] 按展示顺序)。"""
        user_id = make_user("alice")
        ids = [services.add_task(conn, user_id, f"第{i}件")["id"] for i in (1, 2, 3)]
        return user_id, ids

    def test_reorder_changes_list_order(self, conn, three):
        user_id, ids = three
        # 展示顺序是 [第3件, 第2件, 第1件]；把它改成 [第1件, 第3件, 第2件]
        services.reorder_tasks(conn, user_id, [ids[0], ids[2], ids[1]])
        assert [t["content"] for t in services.list_tasks(conn, user_id)] == ["第1件", "第3件", "第2件"]

    def test_reorder_persists(self, conn, three):
        """排序写入数据库并提交，之后直接查表验证。"""
        user_id, ids = three
        services.reorder_tasks(conn, user_id, [ids[1], ids[0], ids[2]])
        orders = dict(
            conn.execute("SELECT id, sort_order FROM tasks WHERE user_id = ?", (user_id,))
        )
        # 排第 0 位的 ids[1] 拿到最大的 sort_order
        assert orders[ids[1]] > orders[ids[0]] > orders[ids[2]]

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            [],
            "3,2,1",
            [True, 1],
            ["3", "2", "1"],
        ],
    )
    def test_invalid_payload_rejected(self, conn, three, bad):
        user_id, ids = three
        with pytest.raises(ValidationFailed):
            services.reorder_tasks(conn, user_id, bad)

    def test_duplicate_ids_rejected(self, conn, three):
        user_id, ids = three
        with pytest.raises(ValidationFailed):
            services.reorder_tasks(conn, user_id, [ids[0], ids[1], ids[0]])

    def test_missing_id_rejected(self, conn, three):
        user_id, ids = three
        with pytest.raises(ValidationFailed):
            services.reorder_tasks(conn, user_id, [ids[0], ids[1]])
        with pytest.raises(ValidationFailed):
            services.reorder_tasks(conn, user_id, [ids[0], ids[1], 999_999])

    def test_other_users_task_rejected(self, conn, three, make_user):
        user_id, ids = three
        bob_id = make_user("bob")
        bob_task = services.add_task(conn, bob_id, "bob 的")
        with pytest.raises(ValidationFailed):
            services.reorder_tasks(conn, user_id, [ids[0], ids[1], bob_task["id"]])

    def test_deleted_task_rejected(self, conn, three):
        user_id, ids = three
        services.delete_task(conn, user_id, ids[2])
        with pytest.raises(ValidationFailed):
            services.reorder_tasks(conn, user_id, [ids[0], ids[1], ids[2]])

    def test_single_task_is_noop(self, conn, make_user):
        user_id = make_user("alice")
        item = services.add_task(conn, user_id, "唯一")
        services.reorder_tasks(conn, user_id, [item["id"]])  # 不报错
        assert [t["id"] for t in services.list_tasks(conn, user_id)] == [item["id"]]
