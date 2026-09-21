"""静态资源的缓存失效：给 URL 加内容指纹。

浏览器缓存静态资源本身是好事，麻烦的是「文件改了，用户手里还是旧的那份」。
这里的做法是内容指纹：

* 每次渲染首页时，按文件内容算出短指纹，拼成 ``/static/style.css?v=1a2b3c4d``；
* 文件一改指纹就变，URL 跟着变，浏览器只能去取新的那份 —— 不需要手工改版本号；
* 首页本身带 ``no-cache``，每次回源校验（内容没变就是 304，代价很小），
  所以用户手里那份 HTML 里的资源 URL 永远是最新的。

于是缓存策略可以很简单：**带指纹且指纹对得上的资源长缓存一年，其余一律回源校验**。
指纹对不上（比如有人直接打开不带参数的 ``/static/style.css``）时走 no-cache，
免得旧 URL 被长期缓存后拿不到新内容。
"""

from __future__ import annotations

import hashlib
import os
import re
from os import PathLike
from pathlib import Path

from fastapi import Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

#: 带内容指纹的资源：内容变了 URL 就变，可以放心让浏览器长期缓存
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"

#: 页面本身，以及不带指纹 / 指纹过期的资源：每次回源校验，没变就走 304
NO_CACHE = "no-cache"

#: 指纹取 sha256 的前 8 个十六进制字符：够用，URL 也不会太长
FINGERPRINT_LENGTH = 8

#: URL 前缀，与 main.py 里挂载静态目录的路径保持一致
STATIC_URL_PREFIX = "/static/"

#: 只给首页里引用的 /static/ 资源加指纹，外链、data: URI 一概不动
ASSET_URL_RE = re.compile(
    rf'(?P<prefix>\b(?:href|src)=")(?P<url>{re.escape(STATIC_URL_PREFIX)}[^"?#]+)(?=")'
)


def fingerprint(path: Path) -> str:
    """按文件内容算短指纹，内容变了指纹就变。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:FINGERPRINT_LENGTH]


class StaticAssets:
    """静态目录 + 内容指纹。

    指纹按 (mtime, 大小) 缓存，避免每个请求都把文件重读一遍；
    文件改动后 mtime 变了会重新计算，所以开发时不重启也能立刻生效。
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self._cache: dict[Path, tuple[int, int, str]] = {}

    def resolve(self, url: str) -> Path | None:
        """把 ``/static/xxx`` 映射成磁盘路径；越界（如带 ``..``）返回 None。"""
        if not url.startswith(STATIC_URL_PREFIX):
            return None
        try:
            path = (self.directory / url[len(STATIC_URL_PREFIX) :]).resolve()
        except OSError:  # 路径里有非法字符
            return None
        return path if path.is_relative_to(self.directory.resolve()) else None

    def version(self, path: Path | str) -> str | None:
        """文件内容的指纹；文件不存在或读不了时返回 None。"""
        path = Path(path)
        try:
            stat = path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
            cached = self._cache.get(path)
            if cached is not None and cached[:2] == key:
                return cached[2]
            value = fingerprint(path)
        except OSError:
            return None
        self._cache[path] = (*key, value)
        return value

    def versioned_index(self, index_path: Path | str) -> str:
        """读首页 HTML，把里面对静态资源的引用换成带指纹的 URL。"""

        def replace(match: re.Match[str]) -> str:
            url = match.group("url")
            path = self.resolve(url)
            value = self.version(path) if path else None
            if value is None:  # 文件不存在：原样留着交给 404，别把整页搞挂
                return match.group(0)
            return f"{match.group('prefix')}{url}?v={value}"

        return ASSET_URL_RE.sub(replace, Path(index_path).read_text(encoding="utf-8"))

    def index_response(self, request: Request, index_name: str = "index.html") -> Response:
        """首页响应：``no-cache`` + ETag，内容没变时浏览器拿到 304。"""
        html = self.versioned_index(self.directory / index_name)
        etag = '"%s"' % hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]
        headers = {"cache-control": NO_CACHE, "etag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(html, media_type="text/html; charset=utf-8", headers=headers)


class VersionedStaticFiles(StaticFiles):
    """静态目录挂载：按 URL 上的指纹决定缓存策略。

    指纹对得上 -> 可以长期缓存；对不上（没带参数、或者参数是旧的）-> 每次回源校验。
    """

    def __init__(self, assets: StaticAssets) -> None:
        super().__init__(directory=assets.directory)
        self.assets = assets

    def file_response(
        self,
        full_path: PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        request = Request(scope)
        current = self.assets.version(full_path)
        if current is not None and request.query_params.get("v") == current:
            response.headers["cache-control"] = IMMUTABLE_CACHE
        else:
            response.headers["cache-control"] = NO_CACHE
        return response
