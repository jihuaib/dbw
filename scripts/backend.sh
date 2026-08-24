#!/bin/bash
# 后台服务：FastAPI（:8099）。 用法: ./scripts/backend.sh start|stop|restart|status|logs
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
PIDF="$ROOT/backend/data/backend.pid"
LOG="$ROOT/backend/data/backend.log"
PORT="${DETOPS_PORT:-8099}"
HOST="${DETOPS_HOST:-0.0.0.0}"     # 部署到 Linux 后从其它机器访问，必须对外监听

ensure_venv() {
  if [ ! -x "$PY" ]; then
    echo "创建虚拟环境并安装依赖…"
    python3 -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
  fi
}

start() {
  ensure_venv
  mkdir -p "$ROOT/backend/data"
  if status >/dev/null 2>&1; then echo "后台已在运行 (pid $(cat "$PIDF"))"; return; fi
  cd "$ROOT/backend"
  nohup "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" > "$LOG" 2>&1 < /dev/null &
  echo $! > "$PIDF"; disown
  cd "$ROOT"
  sleep 2
  status && echo "日志: $LOG"
}

stop() {
  if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
    kill "$(cat "$PIDF")" && rm -f "$PIDF" && echo "后台已停止"
  else
    pkill -f "uvicorn app.main:app" 2>/dev/null && echo "后台已停止" || echo "后台未运行"
    rm -f "$PIDF"
  fi
}

status() {
  if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
    echo "后台运行中 pid $(cat "$PIDF") → http://${HOST}:${PORT}  （本机 http://127.0.0.1:${PORT}）"; return 0
  fi
  echo "后台未运行"; return 1
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) tail -f "$LOG" ;;
  *) echo "用法: $0 start|stop|restart|status|logs"; exit 1 ;;
esac
