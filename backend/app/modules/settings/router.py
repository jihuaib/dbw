"""设置 API。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def read() -> Dict[str, Any]:
    return service.public()


class SettingsIn(BaseModel):
    provider: Optional[str] = None
    preset: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    effort: Optional[str] = None
    vote_k: Optional[int] = None
    auto_freeze: Optional[bool] = None
    kb_exclude_commands: Optional[str] = None


@router.put("")
def update(body: SettingsIn) -> Dict[str, Any]:
    if body.kb_exclude_commands is not None:
        service.put("kb_exclude_commands", body.kb_exclude_commands.strip())
    for field in ("provider", "preset", "base_url"):
        value = getattr(body, field)
        if value is not None:
            service.put(field, value.strip())
    if body.api_key is not None:
        service.put("api_key", body.api_key.strip())
    if body.model:
        service.put("model", body.model.strip())
    if body.effort:
        service.put("effort", body.effort.strip())
    if body.vote_k is not None:
        service.put("vote_k", str(body.vote_k))
    if body.auto_freeze is not None:
        service.put("auto_freeze", "1" if body.auto_freeze else "0")
    return service.public()


@router.post("/test")
def test_key() -> Dict[str, Any]:
    from ...core import llm
    return llm.ping()
