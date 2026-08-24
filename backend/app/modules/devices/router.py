"""设备维护 / 连通性 / 拓扑 / 故障场景 API。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/devices", tags=["devices"])


# ── 设备清单 ──────────────────────────────────────────────────────────
class DeviceIn(BaseModel):
    name: str
    role: str = "LEAF"
    protocol: str = "ssh"
    host: str = ""
    port: int = 22
    username: str = ""
    password: str = ""
    enable_password: str = ""
    vendor: str = ""
    model: str = ""
    pager_cmd: str = ""
    lldp_cmd: str = ""
    enabled: bool = True
    note: str = ""
    report_host: str = ""
    syslog_port: int = 0
    trap_port: int = 0
    syslog_cmd: str = ""
    trap_cmd: str = ""


@router.get("")
def list_devices() -> List[Dict[str, Any]]:
    return service.list_devices()


@router.get("/options")
def options() -> Dict[str, Any]:
    return {"roles": list(service.ROLES), "protocols": list(service.PROTOCOLS),
            "lldp_commands": service.DEFAULT_LLDP_CMDS,
            "vendor_profiles": service.VENDOR_PROFILES}


@router.post("")
def create(body: DeviceIn) -> Dict[str, Any]:
    if not body.name.strip():
        raise HTTPException(400, "设备名不能为空")
    if body.role not in service.ROLES:
        raise HTTPException(400, "角色只能是 {0}".format("/".join(service.ROLES)))
    if body.protocol not in service.PROTOCOLS:
        raise HTTPException(400, "协议只能是 ssh 或 telnet")
    from ...core.db import query_one
    if query_one("SELECT 1 FROM device WHERE name=?", (body.name.strip(),)):
        raise HTTPException(400, "设备名已存在")
    data = body.dict()
    data["name"] = data["name"].strip()
    return service.create_device(data)


@router.put("/{device_id}")
def update(device_id: int, body: DeviceIn) -> Dict[str, Any]:
    try:
        return service.update_device(device_id, body.dict())
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.delete("/{device_id}")
def delete(device_id: int) -> Dict[str, Any]:
    service.delete_device(device_id)
    return {"ok": True}


class TestIn(BaseModel):
    command: str = ""


@router.post("/{device_id}/test")
def test(device_id: int, body: TestIn) -> Dict[str, Any]:
    try:
        return service.test_device(device_id, body.command)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


# ── 拓扑 ──────────────────────────────────────────────────────────────
@router.get("/topology")
def topology() -> Dict[str, Any]:
    return service.topology()


@router.post("/topology/discover")
def discover() -> Dict[str, Any]:
    if not service.enabled_devices():
        raise HTTPException(400, "设备清单为空，先添加设备")
    return service.discover_topology()


@router.get("/topology/context")
def topology_context() -> Dict[str, Any]:
    return {"text": service.topology_context(), "hash": service.topology_hash()}




class PreviewIn(BaseModel):
    device_id: int
    command: str


@router.post("/preview")
def preview(body: PreviewIn) -> Dict[str, Any]:
    """在真机上跑一条命令，看原始回显 / 归一化结果 / 擦掉了什么。"""
    from ..collect.normalize import preview as norm_preview
    d = service.get_device(body.device_id, reveal=True)
    if not d:
        raise HTTPException(404, "设备不存在")
    from .transport import open_for
    tr = open_for(d)
    try:
        tr.connect()
        res = tr.run(body.command)
    except Exception as exc:
        return {"device": d["name"], "command": body.command, "ok": False,
                "error": "{0}: {1}".format(type(exc).__name__, exc),
                "raw": "", "normalized": "", "applied": []}
    finally:
        tr.close()
    if not res["ok"]:
        return {"device": d["name"], "command": body.command, "ok": False,
                "error": res["error"], "raw": res["text"], "normalized": "",
                "applied": []}
    return {"device": d["name"], "command": body.command, "ok": True, "error": "",
            **norm_preview(res["text"])}


@router.get("/capabilities")
def capabilities(device: str = "") -> List[Dict[str, Any]]:
    return service.capabilities(device)


@router.post("/{device_id}/probe")
def probe(device_id: int) -> Dict[str, Any]:
    """把知识库所有已启用命令在这台设备上跑一遍，记录支持与否。"""
    try:
        return service.probe_device(device_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{device_id}/push-reporting")
def push_reporting(device_id: int) -> Dict[str, Any]:
    """把这台设备的 syslog / trap 上报配置下发到设备。"""
    try:
        return service.push_reporting(device_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, "下发失败: {0}".format(exc)[:300])
