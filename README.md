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

其他：深浅色自动跟随系统、手机/平板/电脑自适应、键盘无障碍操作。

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

### 方式二：Docker

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

### 方式三：Nginx + HTTPS

`deploy/nginx.conf.example` 是一份可直接改的配置，包含 80 → 443 跳转、
证书配置和反向代理转发。要点：

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/plan-manager
sudo ln -s /etc/nginx/sites-available/plan-manager /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

走 HTTPS 时**务必**给后端进程设置 `PLAN_COOKIE_SECURE=1`，
让会话 Cookie 带上 `Secure` 标记。

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

当前 **194 个用例全部通过**（168 个逻辑/接口用例 + 26 个真实浏览器用例）：

| 文件 | 层次 | 覆盖内容 |
|------|------|----------|
| `tests/test_security.py` | 单元 | 口令哈希、加盐、损坏哈希的容错、会话令牌随机性 |
| `tests/test_repository.py` | 单元 | SQL 层：唯一索引、会话过期、逻辑删除、跨用户不可写 |
| `tests/test_services.py` | 单元 | 业务规则：注册重名、登录校验、内容长度、完成状态幂等 |
| `tests/test_api.py` | 功能 | 完整 HTTP 流程，对照 9 条需求逐条覆盖，含用户隔离与落库校验 |
| `tests/test_browser.py` | 功能 | 真实浏览器：响应式布局、删除确认弹窗、完成状态配色与交互 |

跑单个文件或单个用例：

```bash
.venv/bin/python -m pytest tests/test_api.py -v
.venv/bin/python -m pytest tests/test_api.py::TestUserIsolation -v
```

### 浏览器测试（可选）

`tests/test_browser.py` 会用真实 Chromium，以 320 / 360 / 390 / 430 / 768 / 1024 / 1440 / 1920
八种视口打开页面，验证：布局不出现横向溢出、底部输入框始终停在视口内、
触摸设备上操作按钮不依赖 hover 就能点到、点击热区足够大、
删除确认弹窗的「取消」不删、「确认」才删，等等。

需要额外装浏览器和它的系统库：

```bash
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
sudo .venv/bin/playwright install-deps chromium   # Chromium 依赖的系统库
```

没装的话这 26 个用例会**自动跳过**，不会让 `pytest` 整体失败。

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
| `GET` | `/api/tasks` | 任务列表（不含已删除，最新的在最前） | `200` |
| `POST` | `/api/tasks` | 新增任务 | `201` |
| `PATCH` | `/api/tasks/{id}` | 标记完成 / 取消完成 | `200` |
| `DELETE` | `/api/tasks/{id}` | 删除任务（逻辑删除） | `204` |
| `GET` | `/api/health` | 健康检查（无需登录） | `200` |

任务对象：

```json
{
  "id": 1,
  "content": "写周报",
  "completed": false,
  "created_at": "2026-09-20T13:48:00+00:00",
  "completed_at": null
}
```

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
           created_at, completed_at, deleted_at)
```

几个约束值得留意：

- `users` 上建了 `lower(username)` 的唯一索引，用户名大小写不敏感（`Alice` 和 `alice` 是同一个账号）
- `tasks.completed` 有 `CHECK (completed IN (0,1))` 约束
- 外键均带 `ON DELETE CASCADE`，删号时任务和会话自动清理

查看数据：

```bash
sqlite3 data/plan.db "SELECT id, user_id, content, completed, deleted_at FROM tasks;"
```

---

## 设计说明

几个实现上的取舍，写在这里免得日后困惑：

**删除是逻辑删除，但接口返回 404。** 需求要求逻辑删除，所以数据行永远保留；
但对使用者来说删掉就是删掉了，因此列表、修改、再次删除都按「不存在」处理。

**新加的任务排在最前面。** 添加成功后列表回到顶部，刚写下的那件事立刻
看得见，任务变多后也不用往下翻找。列表按 `id DESC` 排序而不是按
`created_at`，因为创建时间只精确到秒，同一秒内连续添加几条时只有自增
主键能保证先后不串。完成状态只换配色，不改变位置，避免点一下「完成」
条目就跳走。

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
| `≤ 560px` | 操作按钮换到独立一行、内容区拿到完整宽度、留白收窄 |
| `≤ 360px` | 顶栏只留图标，把宽度让给用户名 |
| `≥ 1024px` | 内容列加宽到 760px 并居中，留白放大 |

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
