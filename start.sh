#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未找到 Python 3。请先安装 Python 3.10+。"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "[1/5] 创建独立 Python 环境..."
  python3 -m venv .venv
fi

PY="$(pwd)/.venv/bin/python"
echo "[2/5] 安装/检查依赖..."
"$PY" -m pip install --disable-pip-version-check -r requirements.txt

echo "[3/5] 检查配置..."
if [ ! -f .env ]; then cp .env.example .env; fi

echo "[4/5] 启动 ALPHA-X..."
"$PY" api_server.py > alpha_x_server.log 2>&1 &
PID=$!

for i in $(seq 1 30); do
  if "$PY" - <<'PY' >/dev/null 2>&1
import urllib.request
r=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)
raise SystemExit(0 if r.status == 200 else 1)
PY
  then
    echo "[5/5] ALPHA-X API 已启动。"
    if command -v open >/dev/null 2>&1; then open http://127.0.0.1:8000/ || true
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open http://127.0.0.1:8000/ >/dev/null 2>&1 || true
    fi
    echo "ALPHA-X: http://127.0.0.1:8000/"
    echo "PID: $PID"
    exit 0
  fi
  sleep 1
done

echo "[错误] 服务未在规定时间内启动。日志：alpha_x_server.log"
exit 1
