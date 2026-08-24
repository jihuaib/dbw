#!/bin/bash
# 前台：Vite 开发服务器（:5178，/api 代理到后台）。 用法: ./scripts/frontend.sh start|stop|status|build|logs
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDF="$ROOT/frontend/.frontend.pid"
LOG="$ROOT/frontend/.frontend.log"

# Vite 6 需要 Node ≥ 18；优先用 nvm 里最新的 22
if [ -d "$HOME/.nvm/versions/node" ]; then
  LATEST="$(ls -d "$HOME"/.nvm/versions/node/v2[2-9]* 2>/dev/null | sort -V | tail -1)"
  [ -n "$LATEST" ] && export PATH="$LATEST/bin:$PATH"
fi

ensure_deps() {
  [ -d "$ROOT/frontend/node_modules" ] || ( cd "$ROOT/frontend" && npm install --no-audit --no-fund )
}

start() {
  ensure_deps
  if status >/dev/null 2>&1; then echo "前台已在运行 (pid $(cat "$PIDF"))"; return; fi
  cd "$ROOT/frontend"
  nohup npm run dev -- --host 0.0.0.0 > "$LOG" 2>&1 < /dev/null &
  echo $! > "$PIDF"; disown
  cd "$ROOT"
  sleep 2
  status && echo "日志: $LOG"
}

stop() {
  if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
    pkill -P "$(cat "$PIDF")" 2>/dev/null; kill "$(cat "$PIDF")" 2>/dev/null
    rm -f "$PIDF"; echo "前台已停止"
  else
    pkill -f "vite" 2>/dev/null && echo "前台已停止" || echo "前台未运行"
    rm -f "$PIDF"
  fi
}

status() {
  if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
    echo "前台运行中 pid $(cat "$PIDF") → http://0.0.0.0:5178（开发模式，/api 代理到后台）"; return 0
  fi
  echo "前台未运行"; return 1
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  build) ensure_deps; ( cd "$ROOT/frontend" && npm run build ) ;;
  logs) tail -f "$LOG" ;;
  *) echo "用法: $0 start|stop|restart|status|build|logs"; exit 1 ;;
esac
