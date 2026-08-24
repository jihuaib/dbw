"""DetOps 服务入口 —— 只负责装配。"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core import llm
from .core.config import NORMALIZE_VERSION, PLAN_VERSION, PROMPT_VERSION
from .core.db import init_db
from .modules.collect.router import router as collect_router
from .modules.devices.router import router as devices_router
from .modules.devices.terminal import router as terminal_router
from .modules.diagnose.router import router as diagnose_router
from .modules.diagnose.service import FALLBACK_LADDER
from .modules.events.router import router as events_router
from .modules.mibs.router import router as mibs_router
from .modules.kb.router import router as kb_router
from .modules.kb.service import summary as kb_summary
from .modules.settings.router import router as settings_router
from .modules.settings.service import public as settings_public

app = FastAPI(title="DetOps", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

for _r in (diagnose_router, kb_router, devices_router, collect_router,
           settings_router, events_router, mibs_router, terminal_router):
    app.include_router(_r)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # 数据库被重建时从备份恢复设置（API Key 等），避免凭据随开发期重置一起丢失
    from .modules.settings.service import restore_from_backup
    restore_from_backup()
    # 监控接收器随服务自启：syslog（Python UDP）+ trap（node worker，先编译 MIB）
    from .modules.events import service as events_service
    from .modules.mibs import service as mibs_service
    try:
        if not mibs_service.status().get("ok"):
            mibs_service.compile_all()      # trap 解码依赖 OID 索引
        events_service.start_syslog()
        events_service.start_trap()
    except Exception as exc:
        import logging
        logging.getLogger("detops").warning("事件接收器启动失败: %s", exc)


@app.get("/api/meta")
def meta() -> Dict[str, Any]:
    return {
        "versions": {"normalize": NORMALIZE_VERSION, "prompt": PROMPT_VERSION,
                     "plan": PLAN_VERSION},
        "settings": settings_public(),
        "llm": llm.stats(),
        "kb": kb_summary(),
        "ladder": [{"level": a, "name": b, "desc": c} for a, b, c in FALLBACK_LADDER],
    }


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True,
            "time": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}


# 生产部署：前端 `npm run build` 的产物由后台直接托管，单端口对外（API / WebSocket 同源）。
# 没有构建产物时（开发模式）不挂载，前端走 Vite 开发服务器 + 代理。
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
