"""功能测试：通过 HTTP 接口走完整业务流程。

对照 prod.md 的 9 条功能需求逐条覆盖，另外包含用户隔离、
输入校验和逻辑删除的落库校验。
"""

from __future__ import annotations

import hashlib
import re

import pytest

from app import repository
from app.main import STATIC_DIR
from app.staticfiles import IMMUTABLE_CACHE

PASSWORD = "secret-123"


class TestStaticPages:
    """页面能正常打开（前端资源的挂载没被改坏）。"""

    def test_index_page_is_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # 需求 5：底部输入框 + 添加按钮
        assert 'id="task-input"' in response.text
        assert 'id="task-submit"' in response.text
        # 需求 3：删除确认弹窗
        assert 'id="modal"' in response.text

    @pytest.mark.parametrize("asset", ["/static/style.css", "/static/app.js"])
    def test_static_assets_are_served(self, client, asset):
        response = client.get(asset)
        assert response.status_code == 200
        assert len(response.content) > 0

    def test_index_references_assets_with_content_fingerprint(self, client):
        """首页里引用的静态资源都带内容指纹，改了文件浏览器才会自动取新的。"""
        html = client.get("/").text
        referenced = dict(re.findall(r'(?:href|src)="(/static/[^"?]+)\?v=([0-9a-f]+)"', html))
        assert set(referenced) == {"/static/style.css", "/static/app.js"}
        for url, version in referenced.items():
            path = STATIC_DIR / url.removeprefix("/static/")
            expected = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
            assert version == expected, f"{url} 的指纹和文件内容对不上"

    def test_cache_headers_follow_the_fingerprint(self, client):
        index = client.get("/")
        assert index.headers["cache-control"] == "no-cache"

        versioned = re.search(r"/static/style\.css\?v=[0-9a-f]+", index.text).group(0)
        assert client.get(versioned).headers["cache-control"] == IMMUTABLE_CACHE
        # 没带指纹 / 指纹是旧的：不长缓存，免得旧 URL 一直拿到老内容
        assert client.get("/static/style.css").headers["cache-control"] == "no-cache"
        assert client.get("/static/style.css", params={"v": "0" * 8}).headers["cache-control"] == "no-cache"

    def test_health(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestRegister:
    def test_register_returns_user_and_logs_in(self, client):
        response = client.post("/api/auth/register", json={"username": "alice", "password": PASSWORD})
        assert response.status_code == 201

        body = response.json()
        assert body["username"] == "alice"
        assert body["id"] > 0
        assert "password" not in body and "password_hash" not in body

        # 注册后直接进入登录态
        assert client.get("/api/auth/me").json()["username"] == "alice"

    def test_session_cookie_is_hardened(self, client):
        response = client.post("/api/auth/register", json={"username": "alice", "password": PASSWORD})
        cookie = response.headers["set-cookie"].lower()
        # 前端 JS 读不到，降低 XSS 影响
        assert "httponly" in cookie
        # 跨站请求不带 cookie，降低 CSRF 风险
        assert "samesite=lax" in cookie

    def test_duplicate_username_conflicts(self, client, register):
        register("alice")
        response = client.post("/api/auth/register", json={"username": "alice", "password": PASSWORD})
        assert response.status_code == 409
        assert "已被注册" in response.json()["detail"]

    def test_duplicate_username_ignores_case(self, client, register):
        register("Alice")
        response = client.post("/api/auth/register", json={"username": "alice", "password": PASSWORD})
        assert response.status_code == 409

    @pytest.mark.parametrize(
        "payload",
        [
            {"username": "a", "password": PASSWORD},
            {"username": "", "password": PASSWORD},
            {"username": "with space", "password": PASSWORD},
            {"username": "x" * 33, "password": PASSWORD},
            {"username": "alice", "password": "12345"},
            {"username": "alice", "password": ""},
            {"username": "alice"},
            {},
        ],
    )
    def test_invalid_payload_rejected(self, client, payload):
        assert client.post("/api/auth/register", json=payload).status_code == 422

    def test_whitespace_username_is_trimmed(self, client):
        response = client.post("/api/auth/register", json={"username": "  alice  ", "password": PASSWORD})
        assert response.status_code == 201
        assert response.json()["username"] == "alice"


class TestLogin:
    def test_login_after_logout(self, register, make_client):
        register("alice")
        fresh = make_client()

        assert fresh.get("/api/auth/me").status_code == 401

        response = fresh.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        assert response.status_code == 200
        assert response.json()["username"] == "alice"
        assert fresh.get("/api/auth/me").json()["username"] == "alice"

    def test_wrong_password_is_401(self, register, make_client):
        register("alice")
        fresh = make_client()
        response = fresh.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"})
        assert response.status_code == 401

    def test_unknown_user_is_401(self, client):
        response = client.post("/api/auth/login", json={"username": "nobody", "password": PASSWORD})
        assert response.status_code == 401

    def test_login_is_case_insensitive_on_username(self, register, make_client):
        register("Alice")
        fresh = make_client()
        assert fresh.post("/api/auth/login", json={"username": "ALICE", "password": PASSWORD}).status_code == 200


class TestSession:
    def test_anonymous_requests_are_401(self, client):
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/tasks").status_code == 401
        assert client.post("/api/tasks", json={"content": "任务"}).status_code == 401
        assert client.patch("/api/tasks/1", json={"completed": True}).status_code == 401
        assert client.delete("/api/tasks/1").status_code == 401

    def test_forged_cookie_is_rejected(self, client):
        client.cookies.set("plan_session", "forged-token-value")
        assert client.get("/api/tasks").status_code == 401

    def test_logout_invalidates_session(self, alice):
        assert alice.get("/api/auth/me").status_code == 200
        assert alice.post("/api/auth/logout").status_code == 204
        assert alice.get("/api/auth/me").status_code == 401
        assert alice.get("/api/tasks").status_code == 401

    def test_logout_twice_is_safe(self, alice):
        alice.post("/api/auth/logout")
        assert alice.post("/api/auth/logout").status_code == 204

    def test_expired_session_is_rejected(self, register, settings, database):
        """把会话改成已过期，接口应当拒绝。"""
        client = register("alice")
        assert client.get("/api/tasks").status_code == 200

        conn = database.connect()
        try:
            conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
            conn.commit()
        finally:
            conn.close()

        assert client.get("/api/tasks").status_code == 401


class TestTaskListAndCreate:
    """需求 1、5：任务列表与新增。"""

    def test_new_user_has_empty_list(self, alice):
        response = alice.get("/api/tasks")
        assert response.status_code == 200
        assert response.json() == {"tasks": []}

    def test_create_task(self, alice):
        response = alice.post("/api/tasks", json={"content": "写周报"})
        assert response.status_code == 201

        task = response.json()
        assert task["content"] == "写周报"
        assert task["completed"] is False
        # 需求 7：未完成的任务没有完成时间
        assert task["completed_at"] is None
        assert task["created_at"]

    def test_new_task_goes_to_front(self, alice):
        """新加的任务排在列表最前面。"""
        alice.post("/api/tasks", json={"content": "第一件"})
        alice.post("/api/tasks", json={"content": "第二件"})

        tasks = alice.get("/api/tasks").json()["tasks"]
        assert [task["content"] for task in tasks] == ["第二件", "第一件"]

    @pytest.mark.parametrize("content", ["", "   ", "\n", "字" * 201, None])
    def test_invalid_content_rejected(self, alice, content):
        assert alice.post("/api/tasks", json={"content": content}).status_code == 422

    def test_content_at_max_length_accepted(self, alice):
        content = "字" * 200
        response = alice.post("/api/tasks", json={"content": content})
        assert response.status_code == 201
        assert response.json()["content"] == content

    def test_content_is_trimmed(self, alice):
        response = alice.post("/api/tasks", json={"content": "  写周报  "})
        assert response.json()["content"] == "写周报"

    def test_supports_emoji_and_unicode(self, alice):
        content = "给妈妈打电话 ☎️ 🎂"
        response = alice.post("/api/tasks", json={"content": content})
        assert response.status_code == 201
        assert response.json()["content"] == content
        assert alice.get("/api/tasks").json()["tasks"][0]["content"] == content


class TestTaskCompletion:
    """需求 4、7、8：完成 / 取消完成与完成时间。"""

    def test_complete_sets_completed_at(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "倒垃圾"}).json()["id"]

        response = alice.patch(f"/api/tasks/{task_id}", json={"completed": True})
        assert response.status_code == 200

        task = response.json()
        assert task["completed"] is True
        assert task["completed_at"] is not None
        assert task["created_at"] is not None

    def test_uncomplete_clears_completed_at(self, alice):
        """需求 8：已完成的任务支持取消完成。"""
        task_id = alice.post("/api/tasks", json={"content": "倒垃圾"}).json()["id"]
        alice.patch(f"/api/tasks/{task_id}", json={"completed": True})

        response = alice.patch(f"/api/tasks/{task_id}", json={"completed": False})
        assert response.status_code == 200
        assert response.json()["completed"] is False
        # 需求 7：取消完成后不再显示完成时间
        assert response.json()["completed_at"] is None

    def test_completion_state_persists_in_list(self, alice):
        first = alice.post("/api/tasks", json={"content": "已完成"}).json()["id"]
        alice.post("/api/tasks", json={"content": "未完成"})
        alice.patch(f"/api/tasks/{first}", json={"completed": True})

        tasks = alice.get("/api/tasks").json()["tasks"]
        # 最新在前：第二条「未完成」排首位
        assert [task["content"] for task in tasks] == ["未完成", "已完成"]
        assert tasks[0]["completed_at"] is None
        assert tasks[1]["completed_at"] is not None

    def test_repeated_complete_is_idempotent(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "任务"}).json()["id"]
        first = alice.patch(f"/api/tasks/{task_id}", json={"completed": True}).json()
        second = alice.patch(f"/api/tasks/{task_id}", json={"completed": True}).json()
        assert first["completed_at"] == second["completed_at"]

    def test_toggle_many_times_stays_consistent(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "任务"}).json()["id"]
        for expected in [True, False, True, False, True]:
            body = alice.patch(f"/api/tasks/{task_id}", json={"completed": expected}).json()
            assert body["completed"] is expected
            assert (body["completed_at"] is not None) is expected

    def test_missing_task_is_404(self, alice):
        assert alice.patch("/api/tasks/999999", json={"completed": True}).status_code == 404

    @pytest.mark.parametrize("bad", ["yes", "true", "false", 1, 0, None, [], {}])
    def test_invalid_completed_value_rejected(self, alice, bad):
        """只接受真正的布尔值，不做宽松类型转换。"""
        task_id = alice.post("/api/tasks", json={"content": "任务"}).json()["id"]
        assert alice.patch(f"/api/tasks/{task_id}", json={"completed": bad}).status_code == 422

    def test_missing_completed_field_rejected(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "任务"}).json()["id"]
        assert alice.patch(f"/api/tasks/{task_id}", json={}).status_code == 422


class TestTaskDeletion:
    """需求 2、3、8、9：删除（逻辑删除）。"""

    def test_delete_removes_from_list(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "要删掉的"}).json()["id"]
        assert alice.delete(f"/api/tasks/{task_id}").status_code == 204
        assert alice.get("/api/tasks").json() == {"tasks": []}

    def test_delete_is_logical_row_is_kept(self, alice, database):
        """需求 9：数据行必须保留，只写 deleted_at。"""
        task_id = alice.post("/api/tasks", json={"content": "要删掉的"}).json()["id"]
        alice.delete(f"/api/tasks/{task_id}")

        conn = database.connect()
        try:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row["content"] == "要删掉的"
        assert row["deleted_at"] is not None

    def test_delete_completed_task(self, alice):
        """需求 8：已完成的任务也支持删除。"""
        task_id = alice.post("/api/tasks", json={"content": "完成后删除"}).json()["id"]
        alice.patch(f"/api/tasks/{task_id}", json={"completed": True})
        assert alice.delete(f"/api/tasks/{task_id}").status_code == 204
        assert alice.get("/api/tasks").json() == {"tasks": []}

    def test_delete_twice_is_404(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "任务"}).json()["id"]
        assert alice.delete(f"/api/tasks/{task_id}").status_code == 204
        # 需求 3：删除是明确的动作，重复删除应当报错而不是静默成功
        assert alice.delete(f"/api/tasks/{task_id}").status_code == 404

    def test_deleted_task_is_gone_from_every_endpoint(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "任务"}).json()["id"]
        alice.delete(f"/api/tasks/{task_id}")

        assert alice.get("/api/tasks").json()["tasks"] == []
        assert alice.patch(f"/api/tasks/{task_id}", json={"completed": True}).status_code == 404
        assert alice.delete(f"/api/tasks/{task_id}").status_code == 404

    def test_delete_missing_task_is_404(self, alice):
        assert alice.delete("/api/tasks/999999").status_code == 404

    def test_delete_does_not_touch_other_tasks(self, alice):
        keep = alice.post("/api/tasks", json={"content": "保留"}).json()["id"]
        drop = alice.post("/api/tasks", json={"content": "删除"}).json()["id"]
        alice.delete(f"/api/tasks/{drop}")

        tasks = alice.get("/api/tasks").json()["tasks"]
        assert [task["id"] for task in tasks] == [keep]


class TestUserIsolation:
    """需求 6：每个用户只能看到和管理自己的任务。"""

    @pytest.fixture
    def alice_with_task(self, register):
        client = register("alice")
        task = client.post("/api/tasks", json={"content": "alice 的私密任务"}).json()
        return client, task

    def test_each_user_sees_only_own_tasks(self, alice_with_task, register):
        alice, _ = alice_with_task
        bob = register("bob")
        bob.post("/api/tasks", json={"content": "bob 的任务"})

        assert [t["content"] for t in alice.get("/api/tasks").json()["tasks"]] == ["alice 的私密任务"]
        assert [t["content"] for t in bob.get("/api/tasks").json()["tasks"]] == ["bob 的任务"]

    def test_cannot_read_other_users_task(self, alice_with_task, register):
        _, task = alice_with_task
        bob = register("bob")
        assert bob.patch(f"/api/tasks/{task['id']}", json={"completed": True}).status_code == 404

    def test_cannot_complete_other_users_task(self, alice_with_task, register):
        alice, task = alice_with_task
        bob = register("bob")
        bob.patch(f"/api/tasks/{task['id']}", json={"completed": True})

        # alice 的任务没有被改动
        assert alice.get("/api/tasks").json()["tasks"][0]["completed"] is False

    def test_cannot_delete_other_users_task(self, alice_with_task, register):
        alice, task = alice_with_task
        bob = register("bob")
        assert bob.delete(f"/api/tasks/{task['id']}").status_code == 404

        assert len(alice.get("/api/tasks").json()["tasks"]) == 1

    def test_same_task_content_stays_separate(self, register):
        """两个用户写同样的任务，互不影响。"""
        alice = register("alice")
        bob = register("bob")

        alice_task = alice.post("/api/tasks", json={"content": "一样的任务"}).json()
        bob_task = bob.post("/api/tasks", json={"content": "一样的任务"}).json()
        assert alice_task["id"] != bob_task["id"]

        alice.delete(f"/api/tasks/{alice_task['id']}")
        assert alice.get("/api/tasks").json()["tasks"] == []
        assert len(bob.get("/api/tasks").json()["tasks"]) == 1


class TestFullUserJourney:
    """一次完整的用户旅程：注册 → 添加 → 完成 → 取消 → 删除 → 退出。"""

    def test_walking_through_the_ui_flow(self, make_client, database):
        client = make_client()

        # 1. 注册
        assert client.post("/api/auth/register", json={"username": "张三", "password": PASSWORD}).status_code == 201
        assert client.get("/api/auth/me").json()["username"] == "张三"

        # 2. 空列表
        assert client.get("/api/tasks").json()["tasks"] == []

        # 3. 在底部输入框连续添加三条
        for content in ["买牛奶", "写周报", "预约体检"]:
            assert client.post("/api/tasks", json={"content": content}).status_code == 201
        tasks = client.get("/api/tasks").json()["tasks"]
        # 最新添加的排在最前面
        assert [t["content"] for t in tasks] == ["预约体检", "写周报", "买牛奶"]
        assert all(t["completed"] is False and t["completed_at"] is None for t in tasks)

        # 4. 点「完成」——任务状态变化并带上完成时间
        milk = next(t for t in tasks if t["content"] == "买牛奶")
        done = client.patch(f"/api/tasks/{milk['id']}", json={"completed": True}).json()
        assert done["completed"] is True and done["completed_at"] is not None

        # 5. 点「取消完成」——回到未完成，完成时间消失
        undone = client.patch(f"/api/tasks/{milk['id']}", json={"completed": False}).json()
        assert undone["completed"] is False and undone["completed_at"] is None

        # 6. 再完成一次并删除「写周报」
        client.patch(f"/api/tasks/{milk['id']}", json={"completed": True})
        report = next(t for t in tasks if t["content"] == "写周报")
        assert client.delete(f"/api/tasks/{report['id']}").status_code == 204

        remaining = client.get("/api/tasks").json()["tasks"]
        assert [t["content"] for t in remaining] == ["预约体检", "买牛奶"]
        assert next(t for t in remaining if t["content"] == "买牛奶")["completed"] is True

        # 7. 被删除的那条只是逻辑删除，数据仍在库里
        conn = database.connect()
        try:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (report["id"],)).fetchone()
        finally:
            conn.close()
        assert row is not None and row["deleted_at"] is not None

        # 8. 退出登录后无法再访问
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/tasks").status_code == 401

        # 9. 重新登录，数据还在
        assert client.post("/api/auth/login", json={"username": "张三", "password": PASSWORD}).status_code == 200
        assert [t["content"] for t in client.get("/api/tasks").json()["tasks"]] == ["预约体检", "买牛奶"]


class TestPasswordStorage:
    def test_password_is_never_stored_in_plaintext(self, register, database):
        register("alice", "very-secret-pw")
        conn = database.connect()
        try:
            row = conn.execute("SELECT password_hash FROM users WHERE username = 'alice'").fetchone()
        finally:
            conn.close()

        assert row["password_hash"] != "very-secret-pw"
        assert "very-secret-pw" not in row["password_hash"]

    def test_each_user_gets_a_unique_salt(self, register, database):
        register("alice", "same-password")
        register("bob", "same-password")

        conn = database.connect()
        try:
            rows = conn.execute("SELECT password_hash FROM users ORDER BY id").fetchall()
        finally:
            conn.close()

        assert rows[0]["password_hash"] != rows[1]["password_hash"]


class TestSchemaIntegrity:
    def test_deleting_user_cascades_to_tasks(self, register, database):
        """删号时任务一并清理（本项目没有删号入口，仅验证外键约束生效）。"""
        client = register("alice")
        client.post("/api/tasks", json={"content": "任务"})

        conn = database.connect()
        try:
            user_id = repository.get_user_by_username(conn, "alice")["id"]
            with conn:
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            remaining = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        finally:
            conn.close()

        assert remaining == 0


class TestTaskColor:
    def test_set_color_and_see_it_in_list(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "需要重点跟进"}).json()["id"]
        body = alice.patch(f"/api/tasks/{task_id}", json={"color": "red"}).json()
        assert body["color"] == "red"

        tasks = alice.get("/api/tasks").json()["tasks"]
        assert tasks[0]["id"] == task_id
        assert tasks[0]["color"] == "red"

    def test_clear_color_with_null(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "先标绿再清除"}).json()["id"]
        alice.patch(f"/api/tasks/{task_id}", json={"color": "green"})
        body = alice.patch(f"/api/tasks/{task_id}", json={"color": None}).json()
        assert body["color"] is None

    def test_new_task_has_no_color(self, alice):
        body = alice.post("/api/tasks", json={"content": "无标记"}).json()
        assert body["color"] is None

    @pytest.mark.parametrize("bad", ["blue", "RED", "", 3, True, ["red"]])
    def test_invalid_color_rejected(self, alice, bad):
        task_id = alice.post("/api/tasks", json={"content": "任务"}).json()["id"]
        assert alice.patch(f"/api/tasks/{task_id}", json={"color": bad}).status_code == 422

    def test_color_survives_completed_toggle(self, alice):
        """PATCH 只传 completed 时不能动标记色。"""
        task_id = alice.post("/api/tasks", json={"content": "标黄后完成"}).json()["id"]
        alice.patch(f"/api/tasks/{task_id}", json={"color": "yellow"})
        done = alice.patch(f"/api/tasks/{task_id}", json={"completed": True}).json()
        assert done["completed"] is True
        assert done["color"] == "yellow"

    def test_completed_and_color_can_update_together(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "一次改两样"}).json()["id"]
        body = alice.patch(f"/api/tasks/{task_id}", json={"completed": True, "color": "green"}).json()
        assert body["completed"] is True
        assert body["color"] == "green"

    def test_empty_patch_rejected(self, alice):
        task_id = alice.post("/api/tasks", json={"content": "任务"}).json()["id"]
        assert alice.patch(f"/api/tasks/{task_id}", json={}).status_code == 422

    def test_null_completed_still_rejected(self, alice):
        """completed 传 null 没有意义，仍然 422（与 color 不同，后者 null = 清除）。"""
        task_id = alice.post("/api/tasks", json={"content": "任务"}).json()["id"]
        assert alice.patch(f"/api/tasks/{task_id}", json={"completed": None}).status_code == 422

    def test_missing_task_is_404(self, alice):
        assert alice.patch("/api/tasks/999999", json={"color": "red"}).status_code == 404


class TestTaskReorder:
    def _make(self, client, contents):
        return [client.post("/api/tasks", json={"content": c}).json()["id"] for c in contents]

    def test_reorder_and_persist(self, alice):
        ids = self._make(alice, ["第一件", "第二件", "第三件"])
        # 当前展示顺序 [第三件, 第二件, 第一件] -> 改成 [第一件, 第三件, 第二件]
        response = alice.put("/api/tasks/order", json={"ids": [ids[0], ids[2], ids[1]]})
        assert response.status_code == 204
        tasks = alice.get("/api/tasks").json()["tasks"]
        assert [t["id"] for t in tasks] == [ids[0], ids[2], ids[1]]

    def test_reorder_requires_exact_set(self, alice):
        ids = self._make(alice, ["第一件", "第二件"])
        # 少一个
        assert alice.put("/api/tasks/order", json={"ids": [ids[0]]}).status_code == 422
        # 多一个不存在的
        assert alice.put("/api/tasks/order", json={"ids": [ids[0], ids[1], 9999]}).status_code == 422
        # 失败后顺序不变
        tasks = alice.get("/api/tasks").json()["tasks"]
        assert [t["content"] for t in tasks] == ["第二件", "第一件"]

    def test_reorder_rejects_duplicates(self, alice):
        ids = self._make(alice, ["第一件", "第二件"])
        assert alice.put("/api/tasks/order", json={"ids": [ids[0], ids[0]]}).status_code == 422

    def test_reorder_rejects_other_users_task(self, alice, register):
        bob = register("bob")
        mine = self._make(alice, ["我的任务"])[0]
        theirs = self._make(bob, ["他的任务"])[0]
        assert alice.put("/api/tasks/order", json={"ids": [mine, theirs]}).status_code == 422

    def test_reorder_rejects_deleted_task(self, alice):
        ids = self._make(alice, ["第一件", "第二件"])
        alice.delete(f"/api/tasks/{ids[1]}")
        assert alice.put("/api/tasks/order", json={"ids": [ids[0], ids[1]]}).status_code == 422

    def test_reorder_rejects_empty_list(self, alice):
        self._make(alice, ["第一件"])
        assert alice.put("/api/tasks/order", json={"ids": []}).status_code == 422

    def test_reorder_single_task_is_noop(self, alice):
        ids = self._make(alice, ["唯一"])
        assert alice.put("/api/tasks/order", json={"ids": ids}).status_code == 204
        assert [t["id"] for t in alice.get("/api/tasks").json()["tasks"]] == ids

    def test_reorder_requires_auth(self, client):
        assert client.put("/api/tasks/order", json={"ids": [1]}).status_code == 401

    def test_reorder_rejects_non_int_ids(self, alice):
        ids = self._make(alice, ["第一件", "第二件"])
        assert alice.put("/api/tasks/order", json={"ids": ["1", "2"]}).status_code == 422
