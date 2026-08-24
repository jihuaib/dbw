"""监控 API：syslog / trap 事件、MIB 编译、接收器管理、设备上报配置。"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
def events(kind: str = "", device: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    return service.list_events(kind, device, limit)


@router.delete("")
def clear() -> Dict[str, Any]:
    service.clear_events()
    return {"ok": True}


@router.get("/receivers")
def receivers() -> Dict[str, Any]:
    return service.receivers_status()


class ReceiverIn(BaseModel):
    syslog_port: int = 0          # 服务器默认监听端口（设备可各自另配）
    trap_port: int = 0
    communities: List[str] = []


@router.post("/receivers/start")
def start(body: ReceiverIn) -> Dict[str, Any]:
    """保存监听配置并（重）启接收器。页面上只暴露这几项：服务器该听哪。"""
    service.set_listen_defaults(body.syslog_port, body.trap_port)
    if body.communities:
        service.set_communities(body.communities)
    service.start_syslog()
    service.start_trap()
    return service.receivers_status()


@router.post("/receivers/stop")
def stop() -> Dict[str, Any]:
    service.stop_syslog()
    service.stop_trap()
    return service.receivers_status()


@router.get("/suggest-host")
def suggest_host() -> Dict[str, str]:
    return {"host": service.suggest_target_host()}


class SourceIn(BaseModel):
    source_ip: str
    device: str


@router.post("/sources")
def set_source(body: SourceIn) -> Dict[str, Any]:
    service.set_source(body.source_ip, body.device)
    return {"sources": service.source_map()}


@router.post("/sources/discover")
def discover() -> Dict[str, Any]:
    return {"sources": service.discover_sources()}
