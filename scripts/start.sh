#!/bin/bash
# 一键起停。 用法: ./scripts/start.sh [up|prod|down|status]
#   up    开发模式：后台 :8099 + Vite 前台 :5178（都对外监听）
#   prod  生产模式：构建前端后只起后台，单端口 :8099 对外提供页面 + API
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
case "${1:-up}" in
  prod)   "$ROOT/scripts/frontend.sh" build && "$ROOT/scripts/backend.sh" restart
          echo; echo "生产模式：浏览器打开 http://<本机IP>:${DETOPS_PORT:-8099}" ;;
  up)     "$ROOT/scripts/backend.sh" start && "$ROOT/scripts/frontend.sh" start
          echo; echo "打开 http://127.0.0.1:5178  （实验环境: ./scripts/lab.sh up）" ;;
  down)   "$ROOT/scripts/frontend.sh" stop; "$ROOT/scripts/backend.sh" stop ;;
  status) "$ROOT/scripts/backend.sh" status; "$ROOT/scripts/frontend.sh" status ;;
  *) echo "用法: $0 up|prod|down|status"; exit 1 ;;
esac
