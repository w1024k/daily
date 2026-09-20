# 轻量部署镜像。构建：
#   docker build -t plan-manager .
# 运行：
#   docker run -d -p 8000:8000 -v plan-data:/app/data --name plan plan-manager
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAN_DB_PATH=/app/data/plan.db \
    PLAN_HOST=0.0.0.0 \
    PLAN_PORT=8000

WORKDIR /app

# 先装依赖，改代码时不必重装
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 数据库放在卷里，容器重建数据不丢
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

# 健康检查：/api/health 无需登录
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"

# SQLite 用单进程最省心；WAL + busy_timeout 也支持多 worker，见 README
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
