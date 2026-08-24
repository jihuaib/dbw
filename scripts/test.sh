#!/bin/bash
# 统一测试入口 —— 与 CI 跑同一套用例，不再手写一次性验证脚本。
#   ./scripts/test.sh          # 单元测试（离线，秒级）
#   ./scripts/test.sh live     # + 真机集成（需后端 :8099 + CNetNexus 容器 + API key）
#   ./scripts/test.sh e2e      # + 前端 Playwright 端到端
set -e
set -o pipefail   # 管道里 pytest 失败也要让脚本失败
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python"      # CI 里没有 venv，用已装好依赖的系统 python

echo "== 后端单元测试 =="
(cd "$ROOT/backend" && "$PY" -m pytest -q)

if [[ "$1" == "live" || "$1" == "all" ]]; then
  echo "== 真机集成测试（-m live）=="
  (cd "$ROOT/backend" && "$PY" -m pytest -q -m live)
fi

if [[ "$1" == "e2e" || "$1" == "all" ]]; then
  echo "== 前端端到端 =="
  (cd "$ROOT/frontend" && npm test)
fi
