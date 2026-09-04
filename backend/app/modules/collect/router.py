"""采集 API。"""
from __future__ import annotations

from typing import Any, Dict

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/collect", tags=["collect"])


@router.get("/epochs/{epoch_id}")
def epoch(epoch_id: int) -> Dict[str, Any]:
    e = service.epoch(epoch_id)
    if not e:
        raise HTTPException(404, "采集纪元不存在")
    e["captures"] = service.captures(epoch_id)
    return e


@router.get("/drift")
def drift(a: int, b: int) -> Dict[str, Any]:
    return service.drift(a, b)


class CalibrateIn(BaseModel):
    device: str = ""
    rounds: int = 3
    gap_ms: int = 1500


@router.post("/calibrate")
def calibrate(body: CalibrateIn) -> Dict[str, Any]:
    """实测标定易变位置：对每条支持的命令多采几次，看哪些 token 真的在变。"""
    from ..devices import service as device_service
    from ..kb import service as kb
    targets = ([body.device] if body.device
               else [d["name"] for d in device_service.enabled_devices()])
    if not targets:
        raise HTTPException(400, "没有可用设备")
    blocked = device_service.unsupported_map()
    cmds_all = sorted({c["command"] for c in kb.list_commands(enabled_only=True)
                       if kb.runnable_command(c)})
    out, total = [], 0
    for name in targets:
        cmds = [c for c in cmds_all if c not in blocked.get(name, set())]
        try:
            r = service.calibrate_device(name, cmds, body.rounds, body.gap_ms)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        out.append(r)
        total += r["positions"]
    return {"devices": out, "total_positions": total}


@router.get("/profiles")
def profiles(device: str = "") -> List[Dict[str, Any]]:
    return service.profiles(device)
