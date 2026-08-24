"""诊断进度总线 —— 让界面看得见「正在做什么」。

诊断要连 N 台设备、跑几十条命令、再等模型出结论，几十秒起步。
点完发送干等着是不能接受的，所以每一步都往这里发一条，前端轮询实时渲染。

进度只是展示，不参与指纹计算 —— 它带时间戳，天然不确定。
"""
from __future__ import annotations

import datetime as _dt
import threading
import uuid
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_TASKS: Dict[str, Dict[str, Any]] = {}
_MAX_TASKS = 50


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def create() -> str:
    task_id = uuid.uuid4().hex[:16]
    with _LOCK:
        _TASKS[task_id] = {"id": task_id, "status": "running", "steps": [],
                           "events": [], "turn": None, "error": "",
                           "created_at": _now()}
        # 只留最近的若干个任务，避免长跑进程内存无限增长
        if len(_TASKS) > _MAX_TASKS:
            for k in sorted(_TASKS, key=lambda x: _TASKS[x]["created_at"])[:-_MAX_TASKS]:
                _TASKS.pop(k, None)
    return task_id


def step(task_id: str, stage: str, detail: str = "",
         done: Optional[int] = None, total: Optional[int] = None) -> None:
    if not task_id:
        return
    with _LOCK:
        t = _TASKS.get(task_id)
        if not t:
            return
        t["steps"].append({"stage": stage, "detail": detail, "done": done,
                           "total": total, "at": _now()})


def event(task_id: str, kind: str, payload: dict) -> None:
    """结构化事件：tool_start / tool_end（含回显）/ delta（token 增量）/ answer。

    steps 是给人看的阶段行；events 是对话流里的实时渲染素材 ——
    工具调用卡片、命令回显、逐字出现的结论都从这里来。
    """
    if not task_id:
        return
    with _LOCK:
        t = _TASKS.get(task_id)
        if not t:
            return
        events = t.setdefault("events", [])
        # token 增量合并进最后一条，避免事件数爆炸
        if kind == "delta" and events and events[-1]["kind"] == "delta":
            events[-1]["payload"]["text"] += payload.get("text", "")
            return
        events.append({"kind": kind, "payload": payload, "at": _now()})
        if len(events) > 800:
            del events[:len(events) - 800]


def update_last(task_id: str, detail: str, done: int, total: int) -> None:
    """刷新最后一步的进度（比如「已采 7/23 条」），不新增条目。"""
    if not task_id:
        return
    with _LOCK:
        t = _TASKS.get(task_id)
        if not t or not t["steps"]:
            return
        t["steps"][-1].update({"detail": detail, "done": done, "total": total})


def finish(task_id: str, turn: Optional[Dict[str, Any]] = None,
           error: str = "") -> None:
    with _LOCK:
        t = _TASKS.get(task_id)
        if not t:
            return
        t["status"] = "failed" if error else "done"
        t["turn"] = turn
        t["error"] = error


def get(task_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        t = _TASKS.get(task_id)
        if not t:
            return None
        return dict(t, steps=list(t["steps"]), events=list(t.get("events", [])))
