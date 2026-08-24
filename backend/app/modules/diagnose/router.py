"""诊断 API。"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core.db import query_one
from . import service

router = APIRouter(prefix="/api/diagnose", tags=["diagnose"])


@router.get("/summary")
def summary() -> Dict[str, Any]:
    return service.summary()


@router.get("/sessions")
def sessions() -> List[Dict[str, Any]]:
    return service.list_sessions()


class SessionIn(BaseModel):
    title: str = ""


@router.post("/sessions")
def create_session(body: SessionIn) -> Dict[str, Any]:
    return service.create_session(body.title)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int) -> Dict[str, Any]:
    service.delete_session(session_id)
    return {"ok": True}


@router.get("/sessions/{session_id}/turns")
def turns(session_id: int) -> List[Dict[str, Any]]:
    if not query_one("SELECT 1 FROM session WHERE id=?", (session_id,)):
        raise HTTPException(404, "会话不存在")
    return service.list_turns(session_id)


class AskIn(BaseModel):
    question: str
    mode: str = "agent"     # agent=多轮工具循环 / single=单轮编排


@router.post("/sessions/{session_id}/ask")
def ask(session_id: int, body: AskIn) -> Dict[str, Any]:
    """异步启动诊断，立刻返回 task_id。进度用 /tasks/{id} 轮询。"""
    if not query_one("SELECT 1 FROM session WHERE id=?", (session_id,)):
        raise HTTPException(404, "会话不存在")
    if not body.question.strip():
        raise HTTPException(400, "问题不能为空")
    return {"task_id": service.ask_async(session_id, body.question.strip(), body.mode)}


@router.get("/tasks/{task_id}")
def task(task_id: str) -> Dict[str, Any]:
    from . import progress
    t = progress.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在或已过期")
    return t


class CheckIn(BaseModel):
    question: str
    rounds: int = 5
    mode: str = "cross"
    gap_ms: int = 2000


@router.post("/consistency-check")
def consistency_check(body: CheckIn) -> Dict[str, Any]:
    if not body.question.strip():
        raise HTTPException(400, "问题不能为空")
    return service.consistency_check(body.question.strip(), body.rounds,
                                     body.mode, body.gap_ms)


@router.get("/turns/{turn_id}/prompt")
def turn_prompt(turn_id: int) -> Dict[str, Any]:
    """这一轮逐字送给模型的 system / user 内容，以及模型的原始结构化回复。"""
    r = service.turn_prompt(turn_id)
    if not r:
        raise HTTPException(404, "轮次不存在")
    return r


@router.get("/frozen")
def frozen(limit: int = 50) -> List[Dict[str, Any]]:
    return service.list_frozen(limit)


@router.get("/frozen/{fingerprint}")
def frozen_detail(fingerprint: str) -> Dict[str, Any]:
    row = service.frozen_get(fingerprint)
    if not row:
        raise HTTPException(404, "没有这个指纹的冻结答案")
    return row


class VerifyIn(BaseModel):
    verified: bool = True


@router.patch("/frozen/{fingerprint}")
def verify(fingerprint: str, body: VerifyIn) -> Dict[str, Any]:
    return service.verify_frozen(fingerprint, body.verified)


@router.delete("/frozen/{fingerprint}")
def unfreeze(fingerprint: str) -> Dict[str, Any]:
    service.unfreeze(fingerprint)
    return {"ok": True}
