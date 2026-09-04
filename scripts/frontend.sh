#!/bin/bash
# 前台：Vite 开发服务器（:5178，/api 代理到后台）。 用法: ./scripts/frontend.sh start|stop|status|build|logs
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDF="$ROOT/frontend/.frontend.pid"
LOG="$ROOT/frontend/.frontend.log"

ensure_runtime() {
  command -v node >/dev/null 2>&1 || { echo "未找到 Node.js（需要 16.20.2 或更高版本）"; exit 1; }
  node -e 'const [major, minor, patch] = process.versions.node.split(".").map(Number); process.exit(major > 16 || (major === 16 && (minor > 20 || (minor === 20 && patch >= 2))) ? 0 : 1)' || {
    echo "Node.js 版本过低：$(node --version)，需要 16.20.2 或更高版本"
    exit 1
  }
  command -v npm >/dev/null 2>&1 || { echo "未找到 npm（需要 8.19.0 或更高版本）"; exit 1; }
  NPM_VERSION="$(npm --version)"
  node -e 'const [major, minor] = process.argv[1].split(".").map(Number); process.exit(major > 8 || (major === 8 && minor >= 19) ? 0 : 1)' "$NPM_VERSION" || {
    echo "npm 版本过低：$NPM_VERSION，需要 8.19.0 或更高版本"
    exit 1
  }
}

ensure_deps() {
  ensure_runtime
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
