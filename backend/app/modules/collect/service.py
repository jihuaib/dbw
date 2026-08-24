"""采集执行 + 证据快照。

确定性要点：
  · 计划已按 (设备, 命令) 全序排好，这里原样执行，并发也不改顺序
  · 确定性重试：固定次数、固定退避，**不加随机 jitter**
  · 缺失显式化：采不到就写进快照，缺失本身是确定的事实
  · 快照 = 归一化回显按序拼接 → snapshot_hash
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from typing import Any, Dict, List, Optional

from ...core.canon import canonical_json, sha256_of
from ...core.config import NORMALIZE_VERSION, RETRY_BACKOFF_MS, RETRY_TIMES
from ...core.db import execute, loads, query, query_one
from ..devices import service as device_service
from ..devices.transport import open_for
from . import models  # noqa: F401  建表注册
from .normalize import calibrate, normalize_output


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class LiveTransport:
    """真机传输：按设备清单逐台建连（SSH 或 Telnet），全程只读命令。"""

    name = "live"

    def __init__(self) -> None:
        self.devices = {d["name"]: d for d in device_service.enabled_devices(reveal=True)}
        self._conns: Dict[str, Any] = {}

    def device_names(self) -> List[str]:
        return sorted(self.devices)

    def _conn(self, device: str):
        if device not in self._conns:
            tr = open_for(self.devices[device])
            tr.connect()
            self._conns[device] = tr
        return self._conns[device]

    def run(self, device: str, command: str) -> Dict[str, Any]:
        if device not in self.devices:
            return {"ok": False, "text": "", "error": "设备 {0} 不在清单中".format(device)}
        try:
            return self._conn(device).run(command)
        except Exception as exc:
            return {"ok": False, "text": "",
                    "error": "{0}: {1}".format(type(exc).__name__, exc)}

    def close(self) -> None:
        for tr in self._conns.values():
            try:
                tr.close()
            except Exception:
                pass
        self._conns = {}


def _run_with_retry(transport, device: str, command: str) -> Dict[str, Any]:
    res = {"ok": False, "text": "", "error": "未执行"}
    for attempt in range(RETRY_TIMES + 1):
        res = transport.run(device, command)
        if res["ok"]:
            return res
        if attempt < RETRY_TIMES:
            time.sleep(RETRY_BACKOFF_MS / 1000.0)   # 固定退避，无 jitter
    return res


def collect(steps: List[Dict[str, Any]], plan_hash: str, plan_engine: str,
            task_id: str = "", epoch_id: Optional[int] = None) -> Dict[str, Any]:
    """执行采集。传 epoch_id 则追加到既有纪元 ——
    多轮 Agent 循环的各轮属于同一次诊断，必须落在同一个证据快照里。"""
    from ..diagnose import progress
    transport = LiveTransport()
    if epoch_id is None:
        epoch_id = execute(
            "INSERT INTO epoch(devices, plan, plan_hash, plan_engine, created_at)"
            " VALUES (?,?,?,?,?)",
            (json.dumps(transport.device_names(), ensure_ascii=False),
             json.dumps(steps, ensure_ascii=False), plan_hash, plan_engine, _now()))
    else:
        prev = query_one("SELECT plan FROM epoch WHERE id=?", (epoch_id,))
        merged = (loads(prev["plan"], []) if prev else []) + list(steps)
        execute("UPDATE epoch SET plan=? WHERE id=?",
                (json.dumps(merged, ensure_ascii=False), epoch_id))

    profiles = load_profiles()
    ok_count = fail_count = 0
    blocks: List[Dict[str, Any]] = []
    total = len(steps)
    for idx, s in enumerate(steps, 1):
        progress.update_last(task_id, "{0} · {1}".format(s["device"], s["command"]),
                             idx, total)
        res = _run_with_retry(transport, s["device"], s["command"])
        if res.get("unsupported"):
            # 手册说有、这台设备没有 —— 记下来，编排层以后不再提议它
            device_service.mark_command(s["device"], s["command"], False,
                                        res.get("error", ""))
        norm = (normalize_output(res["text"], profiles.get((s["device"], s["command"])))
                if res["ok"] else {"text": "", "applied": []})
        execute(
            "INSERT INTO capture(epoch_id, device, command, ok, error, raw_text, raw_sha,"
            " norm_text, norm_sha, applied, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (epoch_id, s["device"], s["command"], 1 if res["ok"] else 0,
             res.get("error", ""), res["text"], sha256_of(res["text"]),
             norm["text"], sha256_of(norm["text"]),
             json.dumps(norm["applied"], ensure_ascii=False), _now()))
        if res["ok"]:
            ok_count += 1
            blocks.append({"device": s["device"], "command": s["command"],
                           "output": norm["text"]})
        else:
            fail_count += 1
            # 缺失显式化：采不到也进快照，参与哈希
            blocks.append({"device": s["device"], "command": s["command"],
                           "output": "<未采到：{0}>".format(res.get("error") or "失败")})

    if hasattr(transport, "close"):
        transport.close()
    # 追加模式下，快照要覆盖这个纪元里已有的全部采集，不只是这一轮
    all_blocks = blocks if len(steps) == _capture_count(epoch_id) else _all_blocks(epoch_id)
    all_blocks.sort(key=lambda b: (b["device"], b["command"]))
    blocks = all_blocks
    snapshot = render_snapshot(transport.device_names(), blocks)
    snap_hash = sha256_of(canonical_json(
        {"norm": NORMALIZE_VERSION, "blocks": blocks}))
    execute("UPDATE epoch SET snapshot=?, snapshot_hash=?, ok_count=?, fail_count=?"
            " WHERE id=?", (snapshot, snap_hash, ok_count, fail_count, epoch_id))
    return {"epoch_id": epoch_id, "snapshot": snapshot, "snapshot_hash": snap_hash,
            "ok": ok_count, "failed": fail_count,
            "devices": transport.device_names(), "blocks": blocks}


def render_snapshot(devices: List[str], blocks: List[Dict[str, Any]]) -> str:
    """证据快照的文本形态 —— 它会**逐字**成为送进 AI 的那段内容。

    因此这里的每一个字节都必须是确定的：不含时间戳、不含会话信息、
    块按 (设备, 命令) 全序排列。
    """
    lines = ["# 证据快照", "归一化版本: {0}".format(NORMALIZE_VERSION),
             "设备: {0}".format(", ".join(devices)), ""]
    topo = device_service.topology_context()
    if topo:
        lines.append(topo)
        lines.append("")
    for b in blocks:
        lines.append("## {0} · {1}".format(b["device"], b["command"]))
        lines.append("```")
        lines.append(b["output"])
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def _capture_count(epoch_id: int) -> int:
    row = query_one("SELECT COUNT(*) n FROM capture WHERE epoch_id=?", (epoch_id,))
    return row["n"] if row else 0


def _all_blocks(epoch_id: int) -> List[Dict[str, Any]]:
    """纪元里已采到的全部内容 —— 多轮循环拼快照时要用全量。"""
    out: List[Dict[str, Any]] = []
    for c in captures(epoch_id):
        out.append({"device": c["device"], "command": c["command"],
                    "output": c["norm_text"] if c["ok"]
                    else "<未采到：{0}>".format(c["error"] or "失败")})
    return out


def epoch(epoch_id: int) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM epoch WHERE id=?", (epoch_id,))
    if not row:
        return None
    row["devices"] = loads(row["devices"], [])
    row["plan"] = loads(row["plan"], [])
    return row


def captures(epoch_id: int) -> List[Dict[str, Any]]:
    rows = query("SELECT * FROM capture WHERE epoch_id=? ORDER BY device, command",
                 (epoch_id,))
    for r in rows:
        r["applied"] = loads(r["applied"], [])
        r["ok"] = bool(r["ok"])
    return rows


def drift(epoch_a: int, epoch_b: int) -> Dict[str, Any]:
    """比对两次采集的原始回显 —— 证明「输入确实变了」。

    没有这一步，「快照哈希相同」可能只是因为输入压根没动，说明不了任何问题。
    """
    a = {(c["device"], c["command"]): c for c in captures(epoch_a)}
    b = {(c["device"], c["command"]): c for c in captures(epoch_b)}
    rows, changed = [], 0
    for key in sorted(set(a) | set(b)):
        ca, cb = a.get(key), b.get(key)
        diff = bool(ca and cb and ca["raw_sha"] != cb["raw_sha"])
        norm_diff = bool(ca and cb and ca["norm_sha"] != cb["norm_sha"])
        changed += 1 if diff else 0
        rows.append({"device": key[0], "command": key[1],
                     "raw_a": (ca["raw_sha"][:10] if ca else "—"),
                     "raw_b": (cb["raw_sha"][:10] if cb else "—"),
                     "raw_changed": diff, "norm_changed": norm_diff})
    return {"total": len(rows), "changed": changed,
            "norm_changed": sum(1 for r in rows if r["norm_changed"]), "rows": rows}


# ── 易变位置标定 ──────────────────────────────────────────────────────
def load_profiles() -> Dict[Any, List[Any]]:
    out: Dict[Any, List[Any]] = {}
    for r in query("SELECT device, command, positions FROM volatility_profile"):
        out[(r["device"], r["command"])] = loads(r["positions"], [])
    return out


def calibrate_device(device_name: str, commands: List[str], rounds: int = 3,
                     gap_ms: int = 1500, task_id: str = "") -> Dict[str, Any]:
    """对每条命令多采几次，实测哪些 token 在变，冻结成 profile。

    这一步是「用测量代替猜测」：不再靠人去想哪一列是计数器。
    """
    from ..devices import service as device_service
    from ..diagnose import progress
    d = [x for x in device_service.enabled_devices(reveal=True)
         if x["name"] == device_name]
    if not d:
        raise ValueError("设备不存在或未启用")
    tr = open_for(d[0])
    learned = 0
    total_pos = 0
    try:
        tr.connect()
        for idx, cmd in enumerate(commands, 1):
            progress.update_last(task_id, "{0} · {1}".format(device_name, cmd),
                                 idx, len(commands))
            samples: List[str] = []
            for k in range(rounds):
                if k:
                    time.sleep(gap_ms / 1000.0)
                r = tr.run(cmd)
                if not r["ok"]:
                    samples = []
                    break
                # 先过一遍规则，再标定 —— 规则已经擦掉的不必重复记录
                samples.append(normalize_output(r["text"])["text"])
            if len(samples) < 2:
                continue
            positions = calibrate(samples)
            # 与既有 profile 取并集：一个位置只要**曾经**被观测到在变，就永久按易变处理。
            # 覆盖式写入会让「这次恰好没动」的计数器丢掉擦除，一致性随状态回退。
            prev = query_one("SELECT positions FROM volatility_profile"
                             " WHERE device=? AND command=?", (device_name, cmd))
            if prev:
                merged = {tuple(x) for x in loads(prev["positions"], [])}
                merged.update(tuple(x) for x in positions)
                positions = sorted(merged)
            execute("INSERT OR REPLACE INTO volatility_profile(device, command,"
                    " positions, samples, calibrated_at) VALUES (?,?,?,?,?)",
                    (device_name, cmd, json.dumps(positions), len(samples), _now()))
            learned += 1
            total_pos += len(positions)
    finally:
        tr.close()
    return {"device": device_name, "commands": learned, "positions": total_pos}


def profiles(device: str = "") -> List[Dict[str, Any]]:
    sql = "SELECT * FROM volatility_profile"
    params: List[Any] = []
    if device:
        sql += " WHERE device=?"
        params.append(device)
    sql += " ORDER BY device, command"
    rows = query(sql, params)
    for r in rows:
        r["positions"] = loads(r["positions"], [])
        r["count"] = len(r["positions"])
    return rows
