"""单元测试：静态资源的指纹与缓存头（对照 app/staticfiles.py）。

核心诉求是「代码更新后浏览器自动拿到新文件」，所以除了指纹本身，
这里还专门验证：改了文件 -> 指纹变 -> 首页给出新 URL -> 旧 URL 不再被长期缓存。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.staticfiles import (
    FINGERPRINT_LENGTH,
    IMMUTABLE_CACHE,
    NO_CACHE,
    StaticAssets,
    fingerprint,
)

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<link rel="icon" href="data:image/svg+xml,%3Csvg%3E%3C/svg%3E">
<link rel="stylesheet" href="/static/style.css">
<link rel="stylesheet" href="/static/theme.css">
</head>
<body>
<script src="/static/app.js"></script>
<script src="https://cdn.example.com/lib.js"></script>
</body>
</html>
"""

CSS = "body { color: red }\n"
JS = "console.log('hi')\n"

VERSIONED_URL_RE = re.compile(r"(/static/[\w.\-]+)\?v=([0-9a-f]+)")


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """一份最小静态目录：首页 + 两个真实资源 + 一个首页引用了但并不存在的文件。"""
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "style.css").write_text(CSS, encoding="utf-8")
    (root / "app.js").write_text(JS, encoding="utf-8")
    return root


@pytest.fixture
def assets(static_dir: Path) -> StaticAssets:
    return StaticAssets(static_dir)


@pytest.fixture
def cache_client(tmp_path: Path, static_dir: Path):
    """把这个静态目录挂进完整的应用里发请求。"""
    app = create_app(
        Settings(db_path=str(tmp_path / "cache.db"), password_iterations=1000),
        static_dir=static_dir,
    )
    with TestClient(app) as client:
        yield client


def versioned_url(client: TestClient, name: str) -> str:
    """从首页里取出某个资源的带指纹 URL，顺带断言它确实带了指纹。"""
    html = client.get("/").text
    for url, version in VERSIONED_URL_RE.findall(html):
        if url.endswith(name):
            assert len(version) == FINGERPRINT_LENGTH
            return f"{url}?v={version}"
    raise AssertionError(f"首页里没找到带指纹的 {name}：{html}")


class TestFingerprint:
    def test_is_sha256_prefix(self, static_dir):
        expected = hashlib.sha256((static_dir / "style.css").read_bytes()).hexdigest()
        assert fingerprint(static_dir / "style.css") == expected[:FINGERPRINT_LENGTH]

    def test_same_content_same_fingerprint(self, static_dir):
        other = static_dir / "copy.css"
        other.write_text(CSS, encoding="utf-8")
        assert fingerprint(other) == fingerprint(static_dir / "style.css")

    def test_content_change_changes_fingerprint(self, static_dir):
        before = fingerprint(static_dir / "style.css")
        (static_dir / "style.css").write_text(CSS + "/* 加一行 */\n", encoding="utf-8")
        assert fingerprint(static_dir / "style.css") != before


class TestStaticAssets:
    def test_resolve_maps_url_to_file(self, assets, static_dir):
        assert assets.resolve("/static/style.css") == (static_dir / "style.css").resolve()

    def test_resolve_rejects_non_static_and_escaping_urls(self, assets):
        assert assets.resolve("/api/health") is None
        assert assets.resolve("/static/../secret.txt") is None
        assert assets.resolve("/static/../../etc/passwd") is None

    def test_version_is_none_for_missing_file(self, assets):
        assert assets.version(assets.directory / "nope.css") is None
        assert assets.resolve("/static/nope.css") is not None  # 路径合法，只是文件不在

    def test_version_follows_file_changes(self, assets, static_dir):
        path = static_dir / "style.css"
        first = assets.version(path)
        assert assets.version(path) == first  # 没动过
        (static_dir / "style.css").write_text(CSS + "\n", encoding="utf-8")
        assert assets.version(path) != first

    def test_versioned_index_only_touches_real_static_files(self, assets):
        html = assets.versioned_index(assets.directory / "index.html")

        assert f'/static/style.css?v={assets.version(assets.directory / "style.css")}"' in html
        assert f'/static/app.js?v={assets.version(assets.directory / "app.js")}"' in html
        # data: URI、外链不动
        assert 'href="data:image/svg+xml,%3Csvg%3E%3C/svg%3E"' in html
        assert 'src="https://cdn.example.com/lib.js"' in html
        # 首页引用了但不存在的文件保持原样，交给 404，不要把整页搞挂
        assert 'href="/static/theme.css"' in html

    def test_versioned_index_leaves_escaping_urls_alone(self, assets, static_dir):
        (static_dir / "index.html").write_text(
            '<a href="/static/../secret.txt">x</a>', encoding="utf-8"
        )
        assert assets.versioned_index(static_dir / "index.html") == '<a href="/static/../secret.txt">x</a>'


class TestIndexResponse:
    def test_index_has_etag_and_no_cache(self, cache_client):
        response = cache_client.get("/")
        assert response.status_code == 200
        assert response.headers["cache-control"] == NO_CACHE
        assert response.headers["etag"]

    def test_matching_etag_gets_304(self, cache_client):
        etag = cache_client.get("/").headers["etag"]
        response = cache_client.get("/", headers={"if-none-match": etag})
        assert response.status_code == 304
        assert response.headers["cache-control"] == NO_CACHE
        assert response.content == b""

    def test_etag_changes_when_an_asset_changes(self, cache_client, static_dir):
        """首页的 ETag 覆盖了资源指纹，资源一变首页的校验值也跟着变。"""
        before = cache_client.get("/").headers["etag"]
        (static_dir / "style.css").write_text(CSS + "/* 改一下 */\n", encoding="utf-8")
        assert cache_client.get("/").headers["etag"] != before


class TestCacheHeaders:
    def test_versioned_asset_is_immutable(self, cache_client):
        response = cache_client.get(versioned_url(cache_client, "style.css"))
        assert response.status_code == 200
        assert response.headers["cache-control"] == IMMUTABLE_CACHE
        assert response.text == CSS

    def test_versioned_304_keeps_immutable(self, cache_client):
        url = versioned_url(cache_client, "app.js")
        etag = cache_client.get(url).headers["etag"]
        response = cache_client.get(url, headers={"if-none-match": etag})
        assert response.status_code == 304
        assert response.headers["cache-control"] == IMMUTABLE_CACHE

    @pytest.mark.parametrize("url", ["/static/style.css", "/static/style.css?v=deadbeef"])
    def test_unversioned_or_stale_asset_revalidates(self, cache_client, url):
        response = cache_client.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == NO_CACHE

    def test_missing_asset_is_still_404(self, cache_client):
        assert cache_client.get("/static/nope.css").status_code == 404


class TestUpdateInvalidatesCache:
    """需求：更新之后浏览器要自动取到新文件（不需要用户清缓存）。"""

    def test_url_changes_after_update_and_old_url_stops_being_cached(self, cache_client, static_dir):
        stale_url = versioned_url(cache_client, "style.css")
        assert cache_client.get(stale_url).headers["cache-control"] == IMMUTABLE_CACHE

        (static_dir / "style.css").write_text(CSS + "body { color: blue }\n", encoding="utf-8")

        fresh_url = versioned_url(cache_client, "style.css")
        assert fresh_url != stale_url
        # 新 URL 立刻可用，且被允许长期缓存
        fresh = cache_client.get(fresh_url)
        assert fresh.status_code == 200
        assert "color: blue" in fresh.text
        assert fresh.headers["cache-control"] == IMMUTABLE_CACHE
        # 旧 URL 不再长缓存，万一还被人用着也不会一直拿到老内容
        assert cache_client.get(stale_url).headers["cache-control"] == NO_CACHE

    def test_untouched_assets_keep_their_url(self, cache_client, static_dir):
        """只改了 CSS，JS 的 URL 不该跟着变（用户不必重复下载）。"""
        before = versioned_url(cache_client, "app.js")
        (static_dir / "style.css").write_text(CSS + "/* 无关改动 */\n", encoding="utf-8")
        assert versioned_url(cache_client, "app.js") == before
