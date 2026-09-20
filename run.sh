#!/usr/bin/env bash
#
# 一键启动脚本：首次运行会自动建虚拟环境并安装依赖。
#
#   ./run.sh                  # 开发模式，监听 127.0.0.1:8000
#   ./run.sh --host 0.0.0.0   # 对外提供服务
#   ./run.sh --port 9000
#
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR=".venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d "$VENV_DIR" ]; then
  echo "==> 创建虚拟环境 $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip
fi

# requirements.txt 比虚拟环境新，或依赖缺失时，重新安装
if [ ! -f "$VENV_DIR/.deps-installed" ] || [ requirements.txt -nt "$VENV_DIR/.deps-installed" ]; then
  echo "==> 安装依赖"
  "$VENV_DIR/bin/pip" install --quiet -r requirements.txt
  touch "$VENV_DIR/.deps-installed"
fi

HOST="${PLAN_HOST:-127.0.0.1}"
PORT="${PLAN_PORT:-8000}"

args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --reload) args+=(--reload); shift ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

echo "==> 计划管理启动中： http://${HOST}:${PORT}"
exec "$VENV_DIR/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT" "${args[@]}"
