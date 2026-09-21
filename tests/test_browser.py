"""浏览器端到端测试：真实渲染下的响应式布局与交互。

需要额外安装（未安装时整个文件会被跳过）：

    pip install playwright
    playwright install chromium

覆盖 prod.md 里纯前端才能验证的部分：删除确认弹窗、完成状态配色、
底部输入框，以及手机/桌面两种视口下的布局是否溢出。
"""

from __future__ import annotations

import contextlib
import re
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api", reason="需要先安装 playwright")

from playwright.sync_api import expect, sync_playwright  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import STATIC_DIR, create_app  # noqa: E402
from app.staticfiles import IMMUTABLE_CACHE  # noqa: E402

# 常见设备视口
PHONE = {"width": 390, "height": 844}   # iPhone 14
SMALL_PHONE = {"width": 320, "height": 568}  # iPhone SE 一代，最窄的常见尺寸
TABLET = {"width": 768, "height": 1024}
DESKTOP = {"width": 1440, "height": 900}

PASSWORD = "browser-test-123"

#: 窄屏上会折行的长正文，用来验证「正文变长会不会把别的元素挤走」
LONG_CONTENT = "这是一条很长的任务描述，长到在手机屏幕上要折成好几行才放得下，用来检查布局"

#: 「已完成」状态的样式类
DONE_CLASS = re.compile(r"\btask--done\b")

_counter = {"n": 0}


def unique_username(prefix: str = "u") -> str:
    _counter["n"] += 1
    return f"{prefix}{_counter['n']}"


# --------------------------------------------------------------------------
# 起一个真实服务
# --------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def _serve(app):
    """真起一个 uvicorn，返回可访问的 base_url。"""
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as response:
                if response.status == 200:
                    break
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    else:  # pragma: no cover
        raise RuntimeError("测试服务启动超时")

    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    settings = Settings(
        db_path=str(tmp_path_factory.mktemp("browser") / "browser.db"),
        password_iterations=1000,
    )
    with _serve(create_app(settings)) as base_url:
        yield base_url


@pytest.fixture(scope="module")
def mutable_static_server(tmp_path_factory):
    """静态目录是临时副本，用例里可以改文件，用来模拟「发了一版新代码」。

    返回 (base_url, 静态目录)。
    """
    root = tmp_path_factory.mktemp("mutable-static")
    static_dir = root / "static"
    shutil.copytree(STATIC_DIR, static_dir)
    settings = Settings(db_path=str(root / "mutable.db"), password_iterations=1000)
    with _serve(create_app(settings, static_dir=static_dir)) as base_url:
        yield base_url, static_dir


@pytest.fixture(scope="module")
def chromium():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as error:  # pragma: no cover - 取决于运行环境
            pytest.skip(
                "无法启动 Chromium，请先安装浏览器与系统依赖：\n"
                "  playwright install chromium\n"
                "  sudo playwright install-deps chromium\n"
                f"原始错误：{error}"
            )
        yield browser
        browser.close()


def _new_page(browser, viewport, *, touch=False):
    """建一个带独立 cookie 的上下文。"""
    context = browser.new_context(
        viewport=viewport,
        has_touch=touch,
        is_mobile=touch,
        device_scale_factor=2 if touch else 1,
        locale="zh-CN",
    )
    page = context.new_page()
    page.console_errors = []
    page.on("pageerror", lambda error: page.console_errors.append(str(error)))
    page.on(
        "console",
        lambda message: page.console_errors.append(message.text) if message.type == "error" else None,
    )
    return page


@pytest.fixture
def phone(chromium):
    page = _new_page(chromium, PHONE, touch=True)
    yield page
    page.context.close()


@pytest.fixture
def desktop(chromium):
    page = _new_page(chromium, DESKTOP)
    yield page
    page.context.close()


# --------------------------------------------------------------------------
# 操作辅助
# --------------------------------------------------------------------------


def sign_up(page, base_url, username):
    page.goto(base_url)
    expect(page.locator("#auth-view")).to_be_visible()
    page.click("#auth-switch")  # 切到注册
    page.fill("#auth-username", username)
    page.fill("#auth-password", PASSWORD)
    page.click("#auth-submit")
    expect(page.locator("#app-view")).to_be_visible()
    expect(page.locator("#auth-view")).to_be_hidden()


def add_task(page, content):
    before = page.locator(".task").count()
    page.fill("#task-input", content)
    page.click("#task-submit")
    expect(page.locator(".task")).to_have_count(before + 1)


def task_row(page, content):
    return page.locator(".task").filter(has_text=content).first


def touch_point(page, locator, *, dx=40, dy=8) -> tuple[float, float]:
    """在 locator 上取一个点，并确认落点确实在卡片上（不是按钮）。

    触摸坐标由 CDP 直接投递，不会像 page.click 那样帮忙滚动或校验；
    画布滚动过、坐标算错时，事件会落到别的元素上，所以这里先验一次。
    """
    box = locator.bounding_box()
    x, y = box["x"] + dx, box["y"] + dy
    target = page.evaluate(
        """([x, y]) => {
            const el = document.elementFromPoint(x, y);
            if (!el) return 'null';
            if (el.closest('button')) return 'button';
            return el.className.toString() || el.tagName;
        }""",
        [x, y],
    )
    assert target not in ("null", "button"), f"落点 ({x:.0f},{y:.0f}) 不在卡片正文上：{target}"
    return x, y


def dispatch_touch(session, kind, points=None):
    session.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points or []})


def no_horizontal_overflow(page) -> tuple[bool, str]:
    """页面是否出现了横向滚动（响应式最常见的翻车点）。"""
    metrics = page.evaluate(
        """() => ({
            docWidth: document.documentElement.scrollWidth,
            bodyWidth: document.body.scrollWidth,
            innerWidth: window.innerWidth,
        })"""
    )
    ok = metrics["docWidth"] <= metrics["innerWidth"] + 1 and metrics["bodyWidth"] <= metrics["innerWidth"] + 1
    return ok, str(metrics)


# --------------------------------------------------------------------------
# 响应式布局
# --------------------------------------------------------------------------


class TestResponsiveLayout:
    @pytest.mark.parametrize(
        "viewport",
        [
            SMALL_PHONE,
            PHONE,
            {"width": 360, "height": 740},
            {"width": 430, "height": 932},
            TABLET,
            {"width": 1024, "height": 768},
            DESKTOP,
            {"width": 1920, "height": 1080},
        ],
        ids=["320", "390", "360", "430", "768", "1024", "1440", "1920"],
    )
    def test_no_horizontal_overflow(self, chromium, live_server, viewport):
        """各种宽度下都不应出现横向滚动条。"""
        page = _new_page(chromium, viewport, touch=viewport["width"] < 768)
        try:
            sign_up(page, live_server, unique_username("wide"))
            # 超长任务 + 窄屏最容易撑破布局
            add_task(page, "这是一条特别特别长的任务描述" * 6)

            ok, metrics = no_horizontal_overflow(page)
            assert ok, f"视口 {viewport} 出现横向溢出: {metrics}"

            # 底部输入区始终在视口内（需求 5）
            composer_box = page.locator(".composer").bounding_box()
            assert composer_box is not None
            assert composer_box["y"] + composer_box["height"] <= viewport["height"] + 1
            assert composer_box["y"] >= 0
        finally:
            page.context.close()

    def test_composer_stays_visible_with_long_list(self, chromium, live_server):
        """任务很多时，底部输入框依然固定在视口底部，列表内部滚动。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("scroll"))
            for index in range(25):
                add_task(page, f"第 {index + 1} 条任务")

            composer_box = page.locator(".composer").bounding_box()
            assert composer_box["y"] + composer_box["height"] <= PHONE["height"] + 1
            # 列表容器自己滚动，而不是把整页撑长
            scrollable = page.evaluate(
                "() => { const b = document.getElementById('board');"
                " return b.scrollHeight > b.clientHeight; }"
            )
            assert scrollable is True
        finally:
            page.context.close()

    def test_auth_card_fits_narrow_screen(self, chromium, live_server):
        page = _new_page(chromium, SMALL_PHONE, touch=True)
        try:
            page.goto(live_server)
            card = page.locator(".auth__card").bounding_box()
            assert card is not None
            assert card["x"] >= 0
            assert card["x"] + card["width"] <= SMALL_PHONE["width"] + 1
            ok, metrics = no_horizontal_overflow(page)
            assert ok, metrics
        finally:
            page.context.close()

    def test_inputs_are_16px_to_prevent_ios_zoom(self, chromium, live_server):
        """iOS Safari 会在聚焦小于 16px 的输入框时放大页面。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            page.goto(live_server)
            size = page.evaluate(
                "() => parseFloat(getComputedStyle(document.getElementById('auth-username')).fontSize)"
            )
            assert size >= 16, f"输入框字号 {size}px，iOS 上会触发页面缩放"
        finally:
            page.context.close()

    def test_desktop_content_column_is_centered(self, chromium, live_server):
        page = _new_page(chromium, DESKTOP)
        try:
            sign_up(page, live_server, unique_username("center"))
            add_task(page, "桌面端任务")

            box = page.locator(".task").first.bounding_box()
            assert box is not None
            # 列宽被限制住，且大致居中（左右留白相差不超过 2px）
            assert box["width"] <= 800
            left_gap = box["x"]
            right_gap = DESKTOP["width"] - (box["x"] + box["width"])
            assert abs(left_gap - right_gap) < 2, f"未居中: 左 {left_gap} 右 {right_gap}"
        finally:
            page.context.close()

    @pytest.mark.parametrize("viewport", [SMALL_PHONE, PHONE])
    def test_color_dots_stay_in_the_card_corner(self, chromium, live_server, viewport):
        """窄屏上圆点固定在卡片右上角。

        正文一长，圆点就会被挤到单独一行（还压在按钮行上方），
        所以这里量的是几何关系，而不只是「元素存在」。
        """
        page = _new_page(chromium, viewport, touch=True)
        try:
            sign_up(page, live_server, unique_username("dotpos"))
            add_task(page, "短")
            add_task(page, LONG_CONTENT)

            rows = page.evaluate(
                """() => [...document.querySelectorAll('.task')].map(li => {
                    const box = el => { const r = el.getBoundingClientRect(); return {
                        top: Math.round(r.top), left: Math.round(r.left),
                        right: Math.round(r.right), bottom: Math.round(r.bottom) }; };
                    const range = document.createRange();
                    range.selectNodeContents(li.querySelector('.task__content'));
                    return {
                        card: box(li),
                        check: box(li.querySelector('.task__check')),
                        colors: box(li.querySelector('.task__colors')),
                        actions: box(li.querySelector('.task__actions')),
                        lines: [...range.getClientRects()].map(r => ({
                            top: Math.round(r.top), bottom: Math.round(r.bottom), right: Math.round(r.right) })),
                        text: li.querySelector('.task__content').textContent,
                    };
                })"""
            )
            assert len(rows) == 2, rows

            for row in rows:
                tag = f"{viewport['width']}px 「{row['text'][:6]}」"
                colors, card = row["colors"], row["card"]
                # 与勾选按钮同一行（这就是「没被挤到第二行」的样子）
                assert abs(colors["top"] - row["check"]["top"]) <= 12, f"{tag} 圆点与勾选不在同一行: {row}"
                # 贴在卡片右上角
                assert 0 < card["right"] - colors["right"] <= 24, f"{tag} 圆点没贴住右边: {row}"
                assert 0 <= colors["top"] - card["top"] <= 24, f"{tag} 圆点没贴住顶边: {row}"
                # 在按钮行上方，说明没有掉到下一行
                assert colors["bottom"] < row["actions"]["top"], f"{tag} 圆点掉到按钮行: {row}"
                # 正文的文字行不跟圆点重叠
                for line in row["lines"]:
                    overlaps = (
                        line["right"] > colors["left"]
                        and line["bottom"] > colors["top"]
                        and line["top"] < colors["bottom"]
                    )
                    assert not overlaps, f"{tag} 文字压到了圆点: 行={line} 圆点={colors}"
        finally:
            page.context.close()


# --------------------------------------------------------------------------
# 触摸 / 鼠标下的操作按钮
# --------------------------------------------------------------------------


class TestActionVisibility:
    def test_actions_visible_without_hover_on_touch(self, chromium, live_server):
        """触摸设备没有 hover，操作按钮必须常显，否则用户根本点不到。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("touch"))
            add_task(page, "触摸设备任务")

            opacity = page.evaluate(
                "() => getComputedStyle(document.querySelector('.task__actions')).opacity"
            )
            assert float(opacity) == 1, f"触摸设备上操作按钮不可见 (opacity={opacity})"

            # 按钮真的能点
            expect(page.locator(".task__action").first).to_be_visible()
        finally:
            page.context.close()

    def test_actions_revealed_on_hover_on_desktop(self, chromium, live_server):
        page = _new_page(chromium, DESKTOP)
        try:
            sign_up(page, live_server, unique_username("hover"))
            add_task(page, "桌面端任务")

            actions = page.locator(".task__actions").first
            assert float(actions.evaluate("el => getComputedStyle(el).opacity")) == 0

            page.locator(".task").first.hover()
            expect(actions).to_have_css("opacity", "1")
        finally:
            page.context.close()

    def test_touch_targets_are_large_enough(self, chromium, live_server):
        """可点区域太小是移动端最常见的可用性问题。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("target"))
            add_task(page, "任务")

            # 勾选按钮视觉上 22px，但通过 ::after 撑大了热区
            box = page.locator(".task__check").first.bounding_box()
            assert box["height"] >= 22 and box["width"] >= 22

            for selector in [".task__action", ".composer__submit"]:
                box = page.locator(selector).first.bounding_box()
                assert box["height"] >= 30, f"{selector} 高度只有 {box['height']}px"
        finally:
            page.context.close()


# --------------------------------------------------------------------------
# 交互流程
# --------------------------------------------------------------------------


class TestInteractions:
    def test_add_task_by_button_and_enter(self, chromium, live_server):
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("add"))

            add_task(page, "点按钮添加")

            # 回车也能添加（需求 5）
            page.fill("#task-input", "按回车添加")
            page.press("#task-input", "Enter")
            expect(page.locator(".task")).to_have_count(2)
            expect(page.locator("#task-input")).to_have_value("")

            # 新加的排在最前面（需求 5）
            expect(page.locator(".task").nth(0)).to_contain_text("按回车添加")
            expect(page.locator(".task").nth(1)).to_contain_text("点按钮添加")
        finally:
            page.context.close()

    def test_empty_input_does_nothing(self, chromium, live_server):
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("empty"))
            page.fill("#task-input", "   ")
            page.click("#task-submit")
            page.wait_for_timeout(300)
            expect(page.locator(".task")).to_have_count(0)
            expect(page.locator("#empty-state")).to_be_visible()
        finally:
            page.context.close()

    def test_completed_task_changes_color(self, chromium, live_server):
        """需求 4：已完成的任务用不同颜色显示。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("color"))
            add_task(page, "会变色的任务")
            row = task_row(page, "会变色的任务")

            pending_bg = row.evaluate("el => getComputedStyle(el).backgroundColor")
            pending_decoration = row.locator(".task__content").evaluate(
                "el => getComputedStyle(el).textDecorationLine"
            )

            row.locator(".task__action[data-action='toggle']").click()
            expect(row).to_have_class(DONE_CLASS)

            done_bg = row.evaluate("el => getComputedStyle(el).backgroundColor")
            done_decoration = row.locator(".task__content").evaluate(
                "el => getComputedStyle(el).textDecorationLine"
            )

            assert done_bg != pending_bg, "完成后背景色没有变化"
            assert "line-through" in done_decoration
            assert "line-through" not in pending_decoration
        finally:
            page.context.close()

    def test_completion_timestamps_display(self, chromium, live_server):
        """需求 7：小字显示创建时间，只有已完成才显示完成时间。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("time"))
            add_task(page, "看时间")
            row = task_row(page, "看时间")

            meta = row.locator(".task__meta")
            expect(meta).to_contain_text("创建于")
            expect(meta).not_to_contain_text("完成于")

            row.locator(".task__action[data-action='toggle']").click()
            expect(row.locator(".task__meta")).to_contain_text("完成于")
        finally:
            page.context.close()

    def test_uncomplete_restores_pending_state(self, chromium, live_server):
        """需求 8：已完成的可以取消完成。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("undo"))
            add_task(page, "反复横跳")
            row = task_row(page, "反复横跳")

            toggle = row.locator(".task__action[data-action='toggle']")
            expect(toggle).to_have_text("完成")

            toggle.click()
            expect(row).to_have_class(DONE_CLASS)
            expect(toggle).to_have_text("取消完成")
            expect(row.locator(".task__meta")).to_contain_text("完成于")

            toggle.click()
            expect(row).not_to_have_class(DONE_CLASS)
            expect(toggle).to_have_text("完成")
            expect(row.locator(".task__meta")).not_to_contain_text("完成于")
        finally:
            page.context.close()

    def test_delete_requires_confirmation(self, chromium, live_server):
        """需求 3：删除前必须确认，取消则不删。"""
        page = _new_page(chromium, DESKTOP)
        try:
            sign_up(page, live_server, unique_username("confirm"))
            add_task(page, "要删除的任务")
            row = task_row(page, "要删除的任务")
            row.hover()

            # 点删除 -> 弹确认框，任务还在
            row.locator(".task__action[data-action='delete']").click()
            expect(page.locator("#modal")).to_be_visible()
            expect(page.locator("#modal-text")).to_contain_text("要删除的任务")
            expect(page.locator(".task")).to_have_count(1)

            # 取消 -> 关闭弹窗，任务保留
            page.click("#modal-cancel")
            expect(page.locator("#modal")).to_be_hidden()
            expect(page.locator(".task")).to_have_count(1)

            # 再次删除并确认 -> 任务消失
            row.hover()
            row.locator(".task__action[data-action='delete']").click()
            page.click("#modal-confirm")
            expect(page.locator(".task")).to_have_count(0)
            expect(page.locator("#empty-state")).to_be_visible()
        finally:
            page.context.close()

    def test_escape_closes_confirmation(self, chromium, live_server):
        page = _new_page(chromium, DESKTOP)
        try:
            sign_up(page, live_server, unique_username("esc"))
            add_task(page, "别删我")
            row = task_row(page, "别删我")
            row.hover()
            row.locator(".task__action[data-action='delete']").click()
            expect(page.locator("#modal")).to_be_visible()

            page.keyboard.press("Escape")
            expect(page.locator("#modal")).to_be_hidden()
            expect(page.locator(".task")).to_have_count(1)
        finally:
            page.context.close()

    def test_validation_error_is_shown(self, chromium, live_server):
        page = _new_page(chromium, PHONE, touch=True)
        try:
            page.goto(live_server)
            page.click("#auth-switch")
            page.fill("#auth-username", "ok-user")
            page.fill("#auth-password", "123")  # 太短
            page.click("#auth-submit")
            expect(page.locator("#auth-error")).to_be_visible()
            # 不该进入主界面
            expect(page.locator("#app-view")).to_be_hidden()
        finally:
            page.context.close()

    def test_wrong_password_shows_error(self, chromium, live_server):
        page = _new_page(chromium, PHONE, touch=True)
        try:
            username = unique_username("wrongpw")
            sign_up(page, live_server, username)
            page.click("#logout")
            expect(page.locator("#auth-view")).to_be_visible()

            page.fill("#auth-username", username)
            page.fill("#auth-password", "definitely-wrong")
            page.click("#auth-submit")
            expect(page.locator("#auth-error")).to_be_visible()
        finally:
            page.context.close()

    def test_page_has_no_console_errors(self, chromium, live_server):
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("console"))
            # 未登录时 /api/auth/me 的 401 是正常的登录态探测，
            # 浏览器会把它记成一条 console error，这里从登录之后开始看
            page.console_errors.clear()

            add_task(page, "任务")
            task_row(page, "任务").locator(".task__action[data-action='toggle']").click()
            page.wait_for_timeout(300)
            assert page.console_errors == [], f"页面报错: {page.console_errors}"
        finally:
            page.context.close()


class TestUserIsolationInBrowser:
    def test_two_users_do_not_see_each_others_tasks(self, chromium, live_server):
        alice_page = _new_page(chromium, DESKTOP)
        bob_page = _new_page(chromium, DESKTOP)
        try:
            sign_up(alice_page, live_server, unique_username("alice"))
            add_task(alice_page, "alice 的私密任务")

            sign_up(bob_page, live_server, unique_username("bob"))
            expect(bob_page.locator("#empty-state")).to_be_visible()
            expect(bob_page.locator(".task")).to_have_count(0)

            add_task(bob_page, "bob 的任务")
            expect(bob_page.locator(".task")).to_have_count(1)

            # alice 那边不受影响
            expect(alice_page.locator(".task")).to_have_count(1)
            expect(alice_page.locator(".task").first).to_contain_text("alice 的私密任务")
        finally:
            alice_page.context.close()
            bob_page.context.close()


class TestDragAndReorder:
    """任务卡片上下拖动排序：鼠标与触摸都要能用，顺序要持久化。"""

    def _drag_grip(self, page, source_index, target_index):
        """把第 source_index 张卡片拖到第 target_index 张卡片之后（鼠标）。"""
        source = page.locator(".task").nth(source_index)
        grip = source.locator(".task__grip")
        box = grip.bounding_box()
        target_box = page.locator(".task").nth(target_index).bounding_box()
        x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        end_y = target_box["y"] + target_box["height"] - 4  # 越过最后一张的中线

        page.mouse.move(x, start_y)
        page.mouse.down()
        page.mouse.move(x, end_y, steps=15)
        page.mouse.up()

    def test_drag_reorders_and_persists(self, chromium, live_server):
        page = _new_page(chromium, DESKTOP)
        try:
            sign_up(page, live_server, unique_username("drag"))
            # 新任务在前：添加完展示顺序是 [第三条, 第二条, 第一条]
            for content in ["第一条", "第二条", "第三条"]:
                add_task(page, content)

            # 把最上面的「第三条」拖到最下面
            with page.expect_response(
                lambda resp: resp.request.method == "PUT" and resp.url.endswith("/api/tasks/order")
            ):
                self._drag_grip(page, 0, 2)

            expect(page.locator(".task").nth(0)).to_contain_text("第二条")
            expect(page.locator(".task").nth(1)).to_contain_text("第一条")
            expect(page.locator(".task").nth(2)).to_contain_text("第三条")

            # 刷新后顺序不变（已持久化到服务端）
            page.reload()
            expect(page.locator(".task").nth(0)).to_contain_text("第二条")
            expect(page.locator(".task").nth(2)).to_contain_text("第三条")
        finally:
            page.context.close()

    def test_drag_up_puts_task_before(self, chromium, live_server):
        page = _new_page(chromium, DESKTOP)
        try:
            sign_up(page, live_server, unique_username("dragup"))
            # 展示顺序 [第三条, 第二条, 第一条]
            for content in ["第一条", "第二条", "第三条"]:
                add_task(page, content)

            with page.expect_response(
                lambda resp: resp.request.method == "PUT" and resp.url.endswith("/api/tasks/order")
            ):
                # 把最下面的「第一条」拖到最前：越过第一张的中线即可
                source = page.locator(".task").nth(2)
                grip = source.locator(".task__grip")
                box = grip.bounding_box()
                first_box = page.locator(".task").nth(0).bounding_box()
                x = box["x"] + box["width"] / 2
                page.mouse.move(x, box["y"] + box["height"] / 2)
                page.mouse.down()
                page.mouse.move(x, first_box["y"] - 6, steps=15)
                page.mouse.up()

            expect(page.locator(".task").nth(0)).to_contain_text("第一条")
            expect(page.locator(".task").nth(1)).to_contain_text("第三条")
            expect(page.locator(".task").nth(2)).to_contain_text("第二条")
        finally:
            page.context.close()

    def test_drag_with_touch(self, chromium, live_server):
        """Pointer Events 在触屏（pointerType=touch）下同样可用。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("touchdrag"))
            # 展示顺序 [第三条, 第二条, 第一条]
            for content in ["第一条", "第二条", "第三条"]:
                add_task(page, content)

            grip = page.locator(".task").nth(0).locator(".task__grip")
            box = grip.bounding_box()
            target_box = page.locator(".task").nth(2).bounding_box()
            x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2
            end_y = target_box["y"] + target_box["height"] - 4

            session = page.context.new_cdp_session(page)
            with page.expect_response(
                lambda resp: resp.request.method == "PUT" and resp.url.endswith("/api/tasks/order")
            ):
                dispatch_touch(session, "touchStart", [{"x": x, "y": start_y}])
                for step in range(1, 11):
                    y = start_y + (end_y - start_y) * step / 10
                    dispatch_touch(session, "touchMove", [{"x": x, "y": y}])
                dispatch_touch(session, "touchEnd")

            expect(page.locator(".task").nth(0)).to_contain_text("第二条")
            expect(page.locator(".task").nth(1)).to_contain_text("第一条")
            expect(page.locator(".task").nth(2)).to_contain_text("第三条")

            page.reload()
            expect(page.locator(".task").nth(0)).to_contain_text("第二条")
            expect(page.locator(".task").nth(2)).to_contain_text("第三条")
        finally:
            page.context.close()

    #: 与 app.js 里的 LONG_PRESS_MS 一致：按住这么久才算长按。
    #: 这里多等一点，避免计时器还没到点断言就失败了。
    LONG_PRESS_MS = 300

    def _wait_for_drag_start(self, page, session, x, y):
        dispatch_touch(session, "touchStart", [{"x": x, "y": y}])
        page.wait_for_timeout(self.LONG_PRESS_MS + 80)
        expect(page.locator(".task.is-dragging")).to_have_count(1)

    def test_long_press_on_card_reorders(self, chromium, live_server):
        """手机上长按卡片正文就能拖动：把手只有 14px 宽，手指很难按准。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("longpress"))
            # 展示顺序 [第三条, 第二条, 第一条]
            for content in ["第一条", "第二条", "第三条"]:
                add_task(page, content)

            session = page.context.new_cdp_session(page)
            x, start_y = touch_point(page, page.locator(".task").nth(0).locator(".task__content"), dy=4)
            target = page.locator(".task").nth(2).bounding_box()
            end_y = target["y"] + target["height"] - 4

            with page.expect_response(
                lambda resp: resp.request.method == "PUT" and resp.url.endswith("/api/tasks/order")
            ):
                self._wait_for_drag_start(page, session, x, start_y)
                for step in range(1, 11):
                    dispatch_touch(session, "touchMove", [{"x": x, "y": start_y + (end_y - start_y) * step / 10}])
                    page.wait_for_timeout(16)
                dispatch_touch(session, "touchEnd")

            expect(page.locator(".task").nth(0)).to_contain_text("第二条")
            expect(page.locator(".task").nth(1)).to_contain_text("第一条")
            expect(page.locator(".task").nth(2)).to_contain_text("第三条")

            # 长按全程不该选中文字（否则浏览器会弹出「选择 / 复制」把拖动打断）
            assert page.evaluate("() => window.getSelection().toString()") == ""
            # 松手后不留状态：卡片不再抬起，页面也退出排序模式
            assert page.evaluate("() => document.querySelectorAll('.is-dragging,.is-pressed').length") == 0
            assert page.evaluate("() => document.body.classList.contains('is-sorting')") is False

            page.reload()
            expect(page.locator(".task").nth(0)).to_contain_text("第二条")
            expect(page.locator(".task").nth(2)).to_contain_text("第三条")
        finally:
            page.context.close()

    def test_long_press_without_moving_keeps_order(self, chromium, live_server):
        """长按后原地松手：不该重排，也不该向服务端提交顺序。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("longpresshold"))
            for content in ["第一条", "第二条", "第三条"]:
                add_task(page, content)

            puts = []
            page.on(
                "request",
                lambda request: puts.append(request.url)
                if request.method == "PUT" and "/api/tasks/order" in request.url
                else None,
            )

            session = page.context.new_cdp_session(page)
            x, y = touch_point(page, page.locator(".task").nth(0).locator(".task__content"), dy=4)
            self._wait_for_drag_start(page, session, x, y)
            dispatch_touch(session, "touchEnd")
            page.wait_for_timeout(300)

            expect(page.locator(".task").nth(0)).to_contain_text("第三条")
            expect(page.locator(".task").nth(1)).to_contain_text("第二条")
            expect(page.locator(".task").nth(2)).to_contain_text("第一条")
            assert puts == [], f"原地松手不该提交排序：{puts}"
            assert page.evaluate("() => document.querySelectorAll('.is-dragging,.is-pressed').length") == 0
        finally:
            page.context.close()

    def test_quick_swipe_scrolls_instead_of_dragging(self, chromium, live_server):
        """没到长按时间就滑动 = 用户在滚列表，交还滚动手势。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("swipes"))
            for index in range(1, 15):
                add_task(page, f"第{index}件")
            page.evaluate("() => { document.querySelector('.board').scrollTop = 0; }")
            page.wait_for_timeout(150)

            session = page.context.new_cdp_session(page)
            x, y = touch_point(page, page.locator(".task").nth(3).locator(".task__content"), dy=4)
            first = page.locator(".task").nth(0).inner_text()

            dispatch_touch(session, "touchStart", [{"x": x, "y": y}])
            for step in range(1, 9):
                dispatch_touch(session, "touchMove", [{"x": x, "y": y - step * 24}])
                page.wait_for_timeout(16)
            dispatch_touch(session, "touchEnd")
            page.wait_for_timeout(300)

            scrolled = page.evaluate("() => Math.round(document.querySelector('.board').scrollTop)")
            assert scrolled > 0, "快速滑动应该滚动列表"
            # 顺序没动，也没有残留的按下 / 拖动状态
            assert page.locator(".task").nth(0).inner_text() == first
            assert page.evaluate("() => document.querySelectorAll('.is-dragging,.is-pressed').length") == 0
        finally:
            page.context.close()

    def test_list_does_not_scroll_while_dragging(self, chromium, live_server):
        """拖动期间要把列表滚动按住，否则卡片会跟着手指跑。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("dragscroll"))
            for index in range(1, 15):
                add_task(page, f"第{index}件")
            page.evaluate("() => { document.querySelector('.board').scrollTop = 160; }")
            page.wait_for_timeout(150)

            session = page.context.new_cdp_session(page)
            x, y = touch_point(page, page.locator(".task").nth(2).locator(".task__content"), dy=4)
            self._wait_for_drag_start(page, session, x, y)

            # 手指向下拖：没有拦截的话列表会跟着往上滚（scrollTop 变小）
            for step in range(1, 9):
                dispatch_touch(session, "touchMove", [{"x": x, "y": y + step * 18}])
                page.wait_for_timeout(16)
            scrolled = page.evaluate("() => Math.round(document.querySelector('.board').scrollTop)")
            dispatch_touch(session, "touchEnd")

            assert scrolled == 160, f"拖动期间列表不该滚动，scrollTop={scrolled}"
        finally:
            page.context.close()

    def test_dragging_card_is_lifted(self, chromium, live_server):
        """拖动中的卡片要有「拿起来了」的反馈，而且不能撑出横向滚动。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("lift"))
            for content in ["第一条", "第二条", "第三条"]:
                add_task(page, content)

            resting_shadow = page.evaluate(
                "() => getComputedStyle(document.querySelector('.task')).boxShadow"
            )

            session = page.context.new_cdp_session(page)
            x, y = touch_point(page, page.locator(".task").nth(0).locator(".task__content"), dy=4)
            self._wait_for_drag_start(page, session, x, y)
            for step in range(1, 6):
                dispatch_touch(session, "touchMove", [{"x": x, "y": y + step * 20}])
                page.wait_for_timeout(16)

            lifted = page.evaluate(
                """() => {
                    const el = document.querySelector('.task.is-dragging');
                    const board = document.querySelector('.board');
                    const scale = new DOMMatrix(getComputedStyle(el).transform).a;
                    return {
                        scale,
                        shadow: getComputedStyle(el).boxShadow,
                        boardScrollWidth: board.scrollWidth,
                        boardClientWidth: board.clientWidth,
                        pageScrollWidth: document.documentElement.scrollWidth,
                        innerWidth: window.innerWidth,
                    };
                }"""
            )
            dispatch_touch(session, "touchEnd")
            page.wait_for_timeout(250)

            assert lifted["scale"] > 1, f"拖动中的卡片没有放大: {lifted}"
            assert lifted["shadow"] != resting_shadow, f"拖动中的卡片阴影没变化: {lifted}"
            assert lifted["boardScrollWidth"] <= lifted["boardClientWidth"], f"拖动撑出了横向滚动: {lifted}"
            assert lifted["pageScrollWidth"] <= lifted["innerWidth"] + 1, f"页面出现横向滚动: {lifted}"
            # 松手后不留状态：放大只由 .is-dragging 这条规则给，
            # FLIP 用的行内 transform 也必须清干净，否则卡片会永久偏移
            assert page.evaluate("() => document.querySelectorAll('.task.is-dragging').length") == 0
            assert page.evaluate(
                "() => [...document.querySelectorAll('.task')].every(el => el.style.transform === '')"
            )
        finally:
            page.context.close()

    def test_escape_cancels_drag(self, chromium, live_server):
        page = _new_page(chromium, DESKTOP)
        try:
            sign_up(page, live_server, unique_username("escdrag"))
            # 登录页加载时 /api/auth/me 的 401 是预期内的，清掉再断言
            page.console_errors.clear()
            # 展示顺序 [第三条, 第二条, 第一条]
            for content in ["第一条", "第二条", "第三条"]:
                add_task(page, content)

            grip = page.locator(".task").nth(0).locator(".task__grip")
            box = grip.bounding_box()
            target_box = page.locator(".task").nth(2).bounding_box()
            x = box["x"] + box["width"] / 2
            page.mouse.move(x, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(x, target_box["y"] + target_box["height"] - 4, steps=8)
            page.keyboard.press("Escape")
            page.mouse.up()
            page.wait_for_timeout(300)

            # 顺序回到拖动前（也没有向服务端提交排序）
            expect(page.locator(".task").nth(0)).to_contain_text("第三条")
            expect(page.locator(".task").nth(1)).to_contain_text("第二条")
            expect(page.locator(".task").nth(2)).to_contain_text("第一条")

            page.reload()
            expect(page.locator(".task").nth(0)).to_contain_text("第三条")
            assert page.console_errors == [], f"页面报错: {page.console_errors}"
        finally:
            page.context.close()

    def test_tap_on_grip_does_not_move(self, chromium, live_server):
        """在把手上轻点（移动不超过阈值）不应触发排序。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("griptap"))
            # 展示顺序 [第二条, 第一条]
            for content in ["第一条", "第二条"]:
                add_task(page, content)

            grip = page.locator(".task").nth(0).locator(".task__grip")
            box = grip.bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] / 2 + 3, box["y"] + box["height"] / 2 + 3)
            page.mouse.up()
            page.wait_for_timeout(300)

            expect(page.locator(".task").nth(0)).to_contain_text("第二条")
            expect(page.locator(".task").nth(1)).to_contain_text("第一条")
        finally:
            page.context.close()


class TestColorDots:
    """红黄绿圆点：点击标记颜色、再点取消、刷新后保持。"""

    COLOR_CLASSES = {
        "red": re.compile(r"\btask--red\b"),
        "yellow": re.compile(r"\btask--yellow\b"),
        "green": re.compile(r"\btask--green\b"),
    }

    def test_dot_marks_card_and_persists(self, chromium, live_server):
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("color"))
            add_task(page, "需要重点跟进的事")

            row = task_row(page, "需要重点跟进的事")
            row.locator('.task__color[data-color="red"]').click()
            expect(row).to_have_class(self.COLOR_CLASSES["red"])
            expect(row.locator('.task__color[data-color="red"]')).to_have_class(re.compile(r"is-active"))

            # 刷新后颜色还在（已持久化）
            page.reload()
            row = task_row(page, "需要重点跟进的事")
            expect(row).to_have_class(self.COLOR_CLASSES["red"])
        finally:
            page.context.close()

    def test_dot_switches_color(self, chromium, live_server):
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("color2"))
            add_task(page, "先红后绿")

            row = task_row(page, "先红后绿")
            row.locator('.task__color[data-color="red"]').click()
            expect(row).to_have_class(self.COLOR_CLASSES["red"])

            row.locator('.task__color[data-color="green"]').click()
            expect(row).to_have_class(self.COLOR_CLASSES["green"])
            expect(row).not_to_have_class(self.COLOR_CLASSES["red"])
            # 只有绿色圆点处于激活态
            expect(row.locator('.task__color[data-color="green"]')).to_have_class(re.compile(r"is-active"))
            expect(row.locator('.task__color[data-color="red"]')).not_to_have_class(re.compile(r"is-active"))
        finally:
            page.context.close()

    def test_clicking_same_dot_clears_color(self, chromium, live_server):
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("color3"))
            add_task(page, "标黄再取消")

            row = task_row(page, "标黄再取消")
            yellow = row.locator('.task__color[data-color="yellow"]')
            yellow.click()
            expect(row).to_have_class(self.COLOR_CLASSES["yellow"])

            yellow.click()  # 再点一次 = 取消
            expect(row).not_to_have_class(self.COLOR_CLASSES["yellow"])

            page.reload()
            row = task_row(page, "标黄再取消")
            expect(row).not_to_have_class(self.COLOR_CLASSES["yellow"])
        finally:
            page.context.close()

    def test_color_survives_completed_toggle(self, chromium, live_server):
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("color4"))
            add_task(page, "标色后完成")

            row = task_row(page, "标色后完成")
            row.locator('.task__color[data-color="green"]').click()
            expect(row).to_have_class(self.COLOR_CLASSES["green"])

            row.locator(".task__action[data-action='toggle']").click()
            expect(row).to_have_class(DONE_CLASS)
            expect(row).to_have_class(self.COLOR_CLASSES["green"])
        finally:
            page.context.close()

    def test_dots_do_not_steal_other_clicks(self, chromium, live_server):
        """圆点扩大的热区不能挡住旁边的完成/删除按钮。"""
        page = _new_page(chromium, PHONE, touch=True)
        try:
            sign_up(page, live_server, unique_username("color5"))
            add_task(page, "按钮还能点")

            row = task_row(page, "按钮还能点")
            row.locator(".task__action[data-action='toggle']").click()
            expect(row).to_have_class(DONE_CLASS)

            row.locator(".task__action[data-action='delete']").click()
            expect(page.locator("#modal")).to_be_visible()
        finally:
            page.context.close()


# --------------------------------------------------------------------------
# 静态资源缓存：更新后浏览器要自动取新文件
# --------------------------------------------------------------------------


class TestStaticCacheBusting:
    """资源 URL 带内容指纹，所以可以放心长缓存，改代码后又不会卡在旧文件上。"""

    def test_page_asks_for_versioned_assets(self, chromium, live_server):
        page = _new_page(chromium, DESKTOP)
        try:
            with page.expect_response(lambda r: "/static/style.css" in r.url) as info:
                page.goto(live_server)
            assert re.search(r"/static/style\.css\?v=[0-9a-f]{8}$", info.value.url)
            # 带指纹的资源才允许被长期缓存
            assert info.value.headers.get("cache-control") == IMMUTABLE_CACHE

            assert re.search(
                r"/static/app\.js\?v=[0-9a-f]{8}$",
                page.get_attribute('script[src^="/static/"]', "src"),
            )
        finally:
            page.context.close()

    def test_versioned_stylesheet_is_really_applied(self, chromium, live_server):
        """URL 变了不能把 CSS 请求搞坏（404/错误 MIME 都会让页面没样式）。"""
        page = _new_page(chromium, DESKTOP)
        try:
            page.goto(live_server)
            expect(page.locator("#auth-view")).to_be_visible()
            rules = page.evaluate(
                """() => [...document.styleSheets]
                    .filter(sheet => (sheet.href || '').includes('style.css'))
                    .map(sheet => sheet.cssRules.length)"""
            )
            assert rules and rules[0] > 0
        finally:
            page.context.close()

    def test_updated_file_is_picked_up_without_clearing_cache(self, chromium, mutable_static_server):
        """改一次 style.css：同一个浏览器上下文重载就能拿到新样式。"""
        base_url, static_dir = mutable_static_server
        page = _new_page(chromium, DESKTOP)
        requested: list[str] = []
        page.on("request", lambda r: requested.append(r.url) if "style.css" in r.url else None)
        try:
            page.goto(base_url)
            expect(page.locator("#auth-view")).to_be_visible()
            first_url = requested[-1]

            css = static_dir / "style.css"
            css.write_text(
                css.read_text(encoding="utf-8") + "\n.auth__card { background-color: rgb(1, 2, 3) !important; }\n",
                encoding="utf-8",
            )

            page.reload()
            expect(page.locator("#auth-view")).to_be_visible()
            assert requested[-1] != first_url, "浏览器还在用旧 URL，等于拿了缓存里的旧文件"
            background = page.evaluate(
                "() => getComputedStyle(document.querySelector('.auth__card')).backgroundColor"
            )
            assert background == "rgb(1, 2, 3)"
        finally:
            page.context.close()
