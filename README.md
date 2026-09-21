# 计划管理

一个轻量的个人计划管理应用：写下要做的事，点一下完成，不想要了就删掉。
按用户隔离数据，手机和电脑浏览器都开箱即用。

前后端一体、单进程部署，一个 SQLite 文件就是全部数据。

---

## 功能

| # | 功能 | 说明 |
|---|------|------|
| 1 | 任务列表 | 每条任务是一句话描述 |
| 2 | 完成 / 删除 | 每条任务带两个操作按钮 |
| 3 | 删除确认 | 点删除会先弹窗确认，避免误触 |
| 4 | 完成状态 | 已完成的任务换一种配色显示（绿色底 + 删除线） |
| 5 | 快速添加 | 页面底部输入框，点「添加」或按回车即可新建 |
| 6 | 用户隔离 | 每个用户只能看到和管理自己的任务，无角色/权限体系 |
| 7 | 时间显示 | 每条任务小字显示创建时间；只有已完成的任务才显示完成时间 |
| 8 | 可逆操作 | 已完成的任务支持「取消完成」和删除 |
| 9 | 逻辑删除 | 删除只打 `deleted_at` 标记，数据行保留 |

其他：

- **拖动排序**：按住卡片左侧把手上下拖即可调整顺序，排序持久化；手机上长按卡片任意位置也能拖
  （把手只有 14px 宽，手指按不准），长按前手指一动就当作滚动列表，拖动中按 `Esc` 取消。
- **颜色标记**：每张卡片有三个红/黄/绿小圆点，点一下标记颜色（左侧色条 + 淡底色），再点同色圆点取消；
  手机上圆点固定在卡片右上角，正文再长也不会把它挤走。
- **更新即时生效**：静态资源 URL 带内容指纹，改完代码重启服务，浏览器自己就会去取新文件，不用让用户清缓存。
- 深浅色自动跟随系统、手机/平板/电脑自适应、键盘无障碍操作。

---

## 技术选型

选型的核心原则是**部署省事**——不引入前端构建，不需要额外的数据库服务。

| 层 | 选择 | 理由 |
|----|------|------|
| 后端 | FastAPI + Uvicorn | 自带 OpenAPI 文档与参数校验，异步性能好，依赖少 |
| 数据库 | SQLite（标准库 `sqlite3`） | 零运维、单文件、够用；不引 ORM，SQL 一目了然 |
| 前端 | 原生 HTML / CSS / JS | 无 npm、无打包步骤，改完刷新即可；页面简单，框架反而是负担 |
| 口令 | PBKDF2-HMAC-SHA256（标准库） | 不依赖 bcrypt 等需要编译的包，部署不掉坑 |
| 会话 | 服务端 Session + HttpOnly Cookie | 可随时失效，比 JWT 更适合单体应用 |

运行时只有两个依赖：`fastapi`、`uvicorn`。

---

## 快速开始

### 环境要求

- Python 3.10 或更高版本（3.10 / 3.11 / 3.12 均可）
- 不需要 Node.js，不需要额外的数据库

### 启动

```bash
git clone <你的仓库地址> plan-manager
cd plan-manager

# 方式一：一键脚本（自动建虚拟环境、装依赖、启动）
chmod +x run.sh
./run.sh

# 方式二：手动
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000> ，首次使用点「立即注册」创建账号即可。

接口文档在 <http://127.0.0.1:8000/api/docs>。

### 让局域网内其他设备访问

```bash
./run.sh --host 0.0.0.0 --port 8000
# 手机浏览器打开 http://<这台机器的内网IP>:8000
```

---

## 部署

### 方式一：systemd（推荐，适合自有服务器）

```bash
# 1. 放置代码
sudo mkdir -p /opt/plan-manager /var/lib/plan-manager
sudo cp -r app requirements.txt /opt/plan-manager/
cd /opt/plan-manager

# 2. 建虚拟环境并装依赖
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt

# 3. 数据目录交给服务账号
sudo chown -R www-data:www-data /opt/plan-manager /var/lib/plan-manager

# 4. 装服务
sudo cp deploy/plan-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now plan-manager

# 5. 确认
systemctl status plan-manager
curl http://127.0.0.1:8000/api/health
```

数据库默认落在 `/var/lib/plan-manager/plan.db`（由服务单元里的 `PLAN_DB_PATH` 指定）。
**备份就是复制这个文件**（连同 `-wal` 后缀的文件一起）。

### 方式二：uv + systemd（用 uv 建环境，装依赖更快）

和方式一唯一的区别是**环境怎么建**：uv 是一个独立二进制，不依赖系统里的
pip，装依赖通常快一个数量级，需要时还能顺手帮你下载对应版本的 Python。

```bash
# 1. 全局装 uv（不需要先有 Python）
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh

# 2. 放置代码
sudo mkdir -p /opt/plan-manager /var/lib/plan-manager
sudo cp -r app requirements.txt /opt/plan-manager/
cd /opt/plan-manager

# 3. 建虚拟环境 + 装依赖
#    UV_PYTHON_INSTALL_DIR 让 uv 下载的 Python 落在 /opt 下，
#    否则默认装到 root 的家目录，服务账号 www-data 读不到
sudo env UV_PYTHON_INSTALL_DIR=/opt/plan-manager/.uv-python \
    uv venv --python 3.12 .venv
sudo env UV_PYTHON_INSTALL_DIR=/opt/plan-manager/.uv-python \
    uv pip install --python .venv/bin/python -r requirements.txt

# 4. 数据目录交给服务账号
sudo chown -R www-data:www-data /opt/plan-manager /var/lib/plan-manager

# 5. 装服务（服务单元的 ExecStart 就指向 .venv/bin/uvicorn，不用改）
sudo cp deploy/plan-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now plan-manager
```

uv 建的 `.venv` 和 venv 模块建出来的布局完全一样，所以运行时并不需要 uv——
服务单元照旧直接执行 `.venv/bin/uvicorn`。uv 只用在安装那一步。

以后 `requirements.txt` 变了，重新装一次依赖再重启即可：

```bash
sudo env UV_PYTHON_INSTALL_DIR=/opt/plan-manager/.uv-python \
    uv pip install --python /opt/plan-manager/.venv/bin/python -r requirements.txt
sudo systemctl restart plan-manager
```

### 方式三：Docker

```bash
docker build -t plan-manager .
docker run -d --name plan \
  -p 8000:8000 \
  -v plan-data:/app/data \
  --restart unless-stopped \
  plan-manager
```

数据存在名为 `plan-data` 的卷里。备份：

```bash
docker run --rm -v plan-data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/plan-backup.tar.gz -C /data .
```

要暴露到公网，请在容器前面加一层 HTTPS 反向代理。

### 方式四：Nginx + HTTPS

`deploy/nginx.conf.example` 是一份可直接改的配置，包含 80 → 443 跳转、
证书配置和反向代理转发。要点：

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/plan-manager
sudo ln -s /etc/nginx/sites-available/plan-manager /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

走 HTTPS 时**务必**给后端进程设置 `PLAN_COOKIE_SECURE=1`，
让会话 Cookie 带上 `Secure` 标记。

### 静态资源与浏览器缓存

应用自己下发缓存头，语义是「**指纹对得上就长缓存，否则一律回源校验**」：

| 请求 | 响应头 | 效果 |
|------|--------|------|
| `GET /`（首页） | `Cache-Control: no-cache` + `ETag` | 每次都回源校验，内容没变只回一个没有正文的 `304`，变了立刻拿到新的 |
| `/static/app.js?v=<当前指纹>` | `Cache-Control: public, max-age=31536000, immutable` | 一年内不再询问，直接从本地缓存读 |
| `/static/app.js` 或指纹已过期 | `Cache-Control: no-cache` | 旧 URL 不会被长期缓存，不会卡在旧文件上 |

首页 HTML 里的资源地址是渲染时算出来的内容指纹（`sha256` 前 8 位），
**改了文件指纹就变、URL 就变**，所以：

- 发布新版本只需重启服务，**不必手工改版本号**，也不用教用户按 `Ctrl+F5`；
- 没改到的文件指纹不变，用户不会重复下载（首页 `ETag` 也会跟着资源变化）。

**第一次上这套机制时**，老用户手里可能还压着旧版本发的首页（那时候还没有缓存头），
他们会在一小段时间内看到旧页面，等浏览器下次回源就自动好了；之后不再有这个问题。

反向代理这一层**不要再自己设缓存**。`deploy/nginx.conf.example` 里的
`/static/` 只做转发，不加 `expires` / `add_header`——nginx 的 `expires`
会覆盖上游的 `Cache-Control`，把上面的策略打乱。若前面还挂了 CDN，
记得让 CDN 别缓存 `/`（`/static/` 带指纹，缓存是安全的）。

### 配置项

全部通过环境变量覆盖，改完重启进程即可生效。

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `PLAN_DB_PATH` | `./data/plan.db` | SQLite 数据库文件路径 |
| `PLAN_HOST` | `127.0.0.1` | 监听地址（对外服务用 `0.0.0.0`） |
| `PLAN_PORT` | `8000` | 监听端口 |
| `PLAN_SESSION_TTL` | `2592000` | 登录有效期，单位秒（默认 30 天） |
| `PLAN_COOKIE_NAME` | `plan_session` | 会话 Cookie 名字 |
| `PLAN_COOKIE_SECURE` | `0` | HTTPS 部署时设为 `1` |
| `PLAN_PBKDF2_ITERATIONS` | `260000` | 口令哈希迭代次数，调高更安全但更慢 |

### 关于多进程

默认单进程运行，SQLite 完全够用。若确实需要多 worker：

```bash
.venv/bin/uvicorn app.main:app --workers 4
```

本项目已开启 WAL 模式并设置 10 秒的 `busy_timeout`，多进程读写是安全的，
只是写入会被串行化。用户量不大时没必要开多进程。

---

## 测试

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

当前 **294 个用例全部通过**（248 个逻辑/接口用例 + 46 个真实浏览器用例）：

| 文件 | 层次 | 覆盖内容 |
|------|------|----------|
| `tests/test_security.py` | 单元 | 口令哈希、加盐、损坏哈希的容错、会话令牌随机性 |
| `tests/test_repository.py` | 单元 | SQL 层：唯一索引、会话过期、逻辑删除、跨用户不可写、排序与颜色、旧库迁移 |
| `tests/test_services.py` | 单元 | 业务规则：注册重名、登录校验、内容长度、完成状态幂等、重排与标记色校验 |
| `tests/test_staticfiles.py` | 单元 | 静态资源指纹：URL 注入、越界路径、缓存头、改文件后 URL 变化 |
| `tests/test_api.py` | 功能 | 完整 HTTP 流程，对照 9 条需求逐条覆盖，含用户隔离与落库校验 |
| `tests/test_browser.py` | 功能 | 真实浏览器：响应式布局、删除确认弹窗、完成状态配色、拖动排序、颜色圆点、静态资源缓存 |

跑单个文件或单个用例：

```bash
.venv/bin/python -m pytest tests/test_api.py -v
.venv/bin/python -m pytest tests/test_api.py::TestUserIsolation -v
```

### 浏览器测试（可选）

`tests/test_browser.py` 会用真实 Chromium，以 320 / 360 / 390 / 430 / 768 / 1024 / 1440 / 1920
八种视口打开页面，验证：布局不出现横向溢出、底部输入框始终停在视口内、
触摸设备上操作按钮不依赖 hover 就能点到、点击热区足够大、
删除确认弹窗的「取消」不删、「确认」才删、
拖动排序（鼠标拖动、CDP 触摸拖动、长按卡片正文拖动、原地松手不重排、快速滑动交还给滚动、
拖动期间列表不滚且卡片明显抬起、Esc 取消、轻点不误触）、长按不选中文字、
颜色圆点标记、窄屏下圆点与勾选按钮同一行且不压到正文，
以及「改完文件重载就拿到新样式」的缓存失效，等等。

需要额外装浏览器和它的系统库：

```bash
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
sudo .venv/bin/playwright install-deps chromium   # Chromium 依赖的系统库
```

没装的话这 46 个用例会**自动跳过**，不会让 `pytest` 整体失败。

---

## API

所有任务接口都需要登录，且只操作当前用户自己的数据。
未登录返回 `401`，访问他人的任务返回 `404`（不泄露资源是否存在）。

| 方法 | 路径 | 说明 | 成功状态码 |
|------|------|------|-----------|
| `POST` | `/api/auth/register` | 注册并自动登录 | `201` |
| `POST` | `/api/auth/login` | 登录 | `200` |
| `POST` | `/api/auth/logout` | 退出登录 | `204` |
| `GET` | `/api/auth/me` | 当前登录用户 | `200` |
| `GET` | `/api/tasks` | 任务列表（不含已删除，按拖动顺序排列） | `200` |
| `POST` | `/api/tasks` | 新增任务（排到最前） | `201` |
| `PATCH` | `/api/tasks/{id}` | 标记完成 / 取消完成 / 设置标记色 | `200` |
| `PUT` | `/api/tasks/order` | 拖动排序：`{"ids": [...]}`，顺序即展示顺序 | `204` |
| `DELETE` | `/api/tasks/{id}` | 删除任务（逻辑删除） | `204` |
| `GET` | `/api/health` | 健康检查（无需登录） | `200` |

任务对象：

```json
{
  "id": 1,
  "content": "写周报",
  "completed": false,
  "created_at": "2026-09-20T13:48:00+00:00",
  "completed_at": null,
  "color": "red"
}
```

`color` 取值 `red` / `yellow` / `green` / `null`（无标记）。
`PATCH /api/tasks/{id}` 是部分更新：传 `{"color": "red"}` 只改颜色，
传 `{"color": null}` 清除颜色；`completed` 必须是真正的 JSON 布尔值。
`PUT /api/tasks/order` 要求 `ids` 与当前任务集合完全一致（无重复、无遗漏），
否则返回 `422`——这是「客户端数据已过期，请刷新」的信号。

时间统一是 UTC ISO-8601 字符串，由前端转换成用户本地时间展示。
未完成的任务 `completed_at` 恒为 `null`。

出错时统一返回 `{"detail": "错误说明"}`。

```bash
# 命令行快速体验
curl -c /tmp/plan.txt -X POST http://127.0.0.1:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"demo123456"}'

curl -b /tmp/plan.txt -X POST http://127.0.0.1:8000/api/tasks \
  -H 'Content-Type: application/json' -d '{"content":"写周报"}'

curl -b /tmp/plan.txt http://127.0.0.1:8000/api/tasks
```

---

## 项目结构

```
.
├── app/
│   ├── main.py          # HTTP 层：路由、Cookie、错误处理
│   ├── services.py      # 业务规则层（不依赖 FastAPI，可直接单测）
│   ├── repository.py    # 数据访问层：只写 SQL
│   ├── db.py            # SQLite 连接与建表
│   ├── security.py      # 口令哈希、会话令牌
│   ├── schemas.py       # 请求/响应模型与字段校验
│   ├── deps.py          # FastAPI 依赖注入
│   ├── config.py        # 配置（环境变量）
│   ├── util.py          # 时间工具
│   ├── staticfiles.py   # 静态资源的指纹与缓存头
│   └── static/          # 前端：index.html / style.css / app.js
├── tests/               # pytest 测试
├── deploy/              # systemd 与 nginx 配置示例
├── Dockerfile
├── run.sh               # 一键启动脚本
└── requirements.txt
```

分层是单向的：`main` → `services` → `repository` → `db`。
业务规则集中在 `services.py`，所以单元测试可以绕开 HTTP 直接测逻辑。

---

## 数据库

```sql
users     (id, username, password_hash, created_at)
sessions  (token, user_id → users.id, created_at, expires_at)
tasks     (id, user_id → users.id, content, completed,
           created_at, completed_at, deleted_at, sort_order, color)
```

几个约束值得留意：

- `users` 上建了 `lower(username)` 的唯一索引，用户名大小写不敏感（`Alice` 和 `alice` 是同一个账号）
- `tasks.completed` 有 `CHECK (completed IN (0,1))` 约束；`color` 只允许 `red/yellow/green/NULL`
- `tasks.sort_order` 决定展示顺序，越大越靠前；旧库启动时会自动补列并回填（见设计说明）
- 外键均带 `ON DELETE CASCADE`，删号时任务和会话自动清理

查看数据：

```bash
sqlite3 data/plan.db "SELECT id, user_id, content, completed, sort_order, color, deleted_at FROM tasks;"
```

---

## 设计说明

几个实现上的取舍，写在这里免得日后困惑：

**删除是逻辑删除，但接口返回 404。** 需求要求逻辑删除，所以数据行永远保留；
但对使用者来说删掉就是删掉了，因此列表、修改、再次删除都按「不存在」处理。

**排序靠 `sort_order` 列，而不是 `created_at`。** 新任务取当前用户
`MAX(sort_order) + 1` 排到最前，列表按 `sort_order DESC, id DESC` 展示。
用自增值而不是时间，是因为创建时间只精确到秒，同一秒内连续添加几条时
会串序。完成状态只换配色、不改变位置，避免点一下「完成」条目就跳走。
拖动排序一次性地重写整组 `sort_order`（`PUT /api/tasks/order`），
接口要求 `ids` 集合与当前任务完全一致——不一致返回 `422`，前端据此知道
自己手里的列表过期了，会重新拉取。

**旧库升级是自动的。** `tasks` 表后来加过 `sort_order` / `color` 两列，
`Database.initialize()` 在启动时会检查缺哪些列并 `ALTER TABLE` 补上；
`sort_order` 回填为 `id`，恰好保持旧版本 `id DESC` 的展示顺序不变。

**拖动是手写的 Pointer Events，没有引库。** HTML5 的 Drag and Drop 在
触屏上不可用，而这款应用要在手机上能用，所以拖动走 pointer 事件：
从卡片左侧把手开始（`touch-action: none`，不与列表滚动抢手势），
超过 8px 阈值才进入拖动态，跨过相邻卡片中线即插入，其余卡片用 FLIP
动画让位，松手后整体提交。几个容易踩的坑记一下：一是 Chromium 的触摸
命中测试会把不可点击元素上的触摸重定向到附近最近的可点击元素，所以把手
必须是 `<button>` 而不是 `<div>`；二是拖动中卡片移动会释放 pointer
capture，所以 move/up/cancel 监听挂在 `document` 上。

**手机上长按卡片任意位置都能拖，是权衡后的选择。** 鼠标用户瞄得准 14px 的把手，
手指不行，所以触摸设备上整张卡片都是拖动入口：按下先进入「预备态」（卡片微微抬起，
给用户反馈），按住 300ms 才真正开始拖。300ms 这个数字比浏览器自己的长按选词
（约 500ms）短，抢在前面就不会弹出「选择 / 复制」菜单把拖动打断；同时卡片文字
在触摸设备上一律 `user-select: none`，拖动期间还会拦掉 `contextmenu`。
预备态里手指只要动超过 8px 就取消——那说明用户其实是在滚列表，
所以「快速滑动 = 滚动、按住不动再拖 = 排序」，两者不打架。
拖动期间列表必须停住，否则卡片会跟着手指乱跑：这靠一个非 passive 的
`touchmove` 监听 `preventDefault`，而且它必须在手势开始**之前**就注册好——
浏览器据此决定要不要等这段 JS，临时挂上去的监听器已经晚了。

**静态资源用内容指纹做缓存失效，而不是手工版本号。** 页面引用的是
`/static/app.js?v=4b5d8e2d`，这个 8 位十六进制数是文件内容的 `sha256` 前 8 位，
渲染首页时现算（按 mtime/大小缓存，不会每个请求都重读文件）。选内容指纹而不是
`__version__` 之类的版本号，是因为版本号要靠人记得改——忘了改就白搭；
指纹是算出来的，改了就一定变，没改就不变。首页本身 `no-cache` + `ETag`，
保证用户手里的资源 URL 永远是最新的；指纹对不上的资源（有人直接开旧链接）
退回 `no-cache`，不会因为「URL 没变」而一直拿到老文件。整套逻辑在
`app/staticfiles.py`，只有几十行，没有引构建工具或哈希插件。

**颜色存的是枚举字符串，前后端各自兜底。** 后端 pydantic 用 `Literal`
限定取值，前端渲染类名前再查一次白名单，杜绝把不可信内容拼进 class。

**「完成」是幂等的。** 重复标记完成不会刷新完成时间，保留第一次完成的时间点；
取消完成会清空 `completed_at`，这样列表里「完成于」的信息始终真实。

**前后端校验重复了一遍。** 前端校验是为了即时反馈，后端校验才是安全边界，
两边都做，前端的错误文案可以直接用后端的 `detail`。

**用 `textContent` 而不是 `innerHTML` 渲染任务。** 任务内容是用户输入，
前端全程用 DOM API 写入文本，从根上避免 XSS。

**响应式：一套代码适配手机和电脑。** 布局用 flex 纵向三段式（顶栏 / 可滚动列表 /
底部输入区），容器高度用 `100dvh` 让手机地址栏收放时自动适配。断点只有三个：

| 断点 | 变化 |
|------|------|
| `hover: none` | 触摸设备上操作按钮常显（没有 hover 可用），并把勾选按钮的点击热区撑大 |
| `≤ 560px` | 操作按钮换到独立一行、内容区拿到完整宽度、圆点固定到卡片右上角、留白收窄 |
| `≤ 360px` | 顶栏只留图标，把宽度让给用户名 |
| `≥ 1024px` | 内容列加宽到 760px 并居中，留白放大 |

窄屏上圆点是**绝对定位**到卡片右上角的，不是随手写的：卡片在窄屏用了 `flex-wrap: wrap`
把按钮换行，而 flex 换行是按项目的**基础宽度**算的——正文一长就占满一整行，
圆点会被挤到第三行去（实测在 390px 下掉了 116px）。绝对定位 + 给正文留 70px 右内边距
之后，圆点的位置就与正文长度无关了。

另外两处容易踩的坑也处理了：输入框字号固定 `16px`（iOS Safari 聚焦小于 16px 的输入框
会放大整个页面），以及用 `margin: auto` 而不是 `align-items: center` 居中登录卡片
（软键盘弹出、屏幕变矮时不会把卡片顶部裁掉）。

**已做的安全处理：** 口令 PBKDF2 加盐哈希、会话令牌 `secrets` 随机生成、
Cookie 带 `HttpOnly` + `SameSite=Lax`、登录失败不区分「用户不存在」和「密码错误」、
用户不存在时也走一次等价耗时的假校验（防用户名枚举）、所有任务查询都带 `user_id` 条件。

**尚未处理、上线前需要考虑的：** 没有登录失败次数限制（建议在反向代理层加限流）、
没有注册开关（如需限制可加邀请码）、没有密码找回（需要邮件服务）。

---

## 常见问题

**忘记密码怎么办？**
目前没有找回功能。可以直接改库：

```bash
.venv/bin/python -c "
from app.db import Database
from app import security, repository
conn = Database('data/plan.db').connect()
with conn:
    conn.execute('UPDATE users SET password_hash = ? WHERE username = ?',
                 (security.hash_password('新密码'), '用户名'))
"
```

**数据存在哪？怎么备份？**
默认是项目下的 `data/plan.db`。热备份用 SQLite 自带命令最稳妥：

```bash
sqlite3 data/plan.db ".backup 'backup-$(date +%F).db'"
```

**端口被占用？**
`./run.sh --port 9000`，或者设 `PLAN_PORT=9000`。

**任务写错了能改吗？**
当前版本只支持完成/取消完成/删除，不支持编辑。删掉重新添加即可。

---

## 许可

MIT
