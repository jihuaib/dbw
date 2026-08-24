"""设备维护 + 连通性测试 + LLDP 拓扑发现。

拓扑解析刻意**不写死任何厂商格式**：`show lldp neighbors` 的回显长什么样，
交给 AI 抽成结构化邻居；结果按回显哈希缓存，同一份回显只解析一次。
AI 不可用时退到一组通用正则 —— 覆盖面窄一些，但完全确定。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ...core import llm
from ...core.canon import sha256_of
from ...core.db import execute, loads, query, query_one
from . import models  # noqa: F401  建表注册
from .transport import open_for

ROLES = ("SPINE", "LEAF", "BORDER", "ACCESS", "CORE", "OTHER")
PROTOCOLS = ("ssh", "telnet")
DEFAULT_LLDP_CMDS = ["show lldp neighbors", "display lldp neighbor-information list",
                     "show lldp neighbor brief"]

# 厂商预设：填好接入方式与关键命令，加设备时少填几项、少踩坑
# 厂商预设是数据不是代码：backend/vendors.json（DETOPS_VENDORS 可指向别的文件）。
# 接入新厂商 = 往 JSON 里加一条：接入协议/端口、关分屏与 LLDP 命令、
# syslog / trap 上报命令模板（{host} {port} 占位）。第一条是页面默认选中项。
def _load_vendor_profiles() -> List[Dict[str, Any]]:
    import json as _json
    import os as _os
    from ...core.config import BASE_DIR
    path = _os.environ.get("DETOPS_VENDORS") or str(BASE_DIR / "vendors.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = _json.load(fh)
        return data if isinstance(data, list) and data else []
    except (OSError, ValueError):
        return []


VENDOR_PROFILES: List[Dict[str, Any]] = _load_vendor_profiles() or [
    {"id": "generic", "label": "手动填写", "protocol": "ssh", "port": 22,
     "pager_cmd": "", "lldp_cmd": "", "syslog_cmd": "", "trap_cmd": "", "note": ""},
]

SECRET_FIELDS = ("password", "enable_password")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _row(r: Dict[str, Any], reveal: bool = False) -> Dict[str, Any]:
    r["enabled"] = bool(r["enabled"])
    if not reveal:
        for f in SECRET_FIELDS:
            r[f + "_set"] = bool(r.get(f))
            r[f] = ""
    return r


def list_devices(reveal: bool = False) -> List[Dict[str, Any]]:
    """按 (role, name) 全序 —— 采集顺序确定，快照才确定。"""
    rows = [_row(r, reveal) for r in query("SELECT * FROM device")]
    return sorted(rows, key=lambda d: ((d.get("role") or ""), (d.get("name") or "")))


def get_device(device_id: int, reveal: bool = False) -> Optional[Dict[str, Any]]:
    r = query_one("SELECT * FROM device WHERE id=?", (device_id,))
    return _row(r, reveal) if r else None


def enabled_devices(reveal: bool = True) -> List[Dict[str, Any]]:
    return [d for d in list_devices(reveal) if d["enabled"]]


FIELDS = ("name", "role", "protocol", "host", "port", "username", "password",
          "enable_password", "vendor", "model", "pager_cmd", "lldp_cmd", "enabled",
          "note", "report_host", "syslog_port", "trap_port", "syslog_cmd", "trap_cmd")


def create_device(body: Dict[str, Any]) -> Dict[str, Any]:
    values = [body.get(f) if f not in ("enabled",) else (1 if body.get(f, True) else 0)
              for f in FIELDS]
    did = execute(
        "INSERT INTO device({0}, created_at) VALUES ({1}, ?)".format(
            ", ".join(FIELDS), ", ".join("?" * len(FIELDS))),
        values + [_now()])
    return get_device(did)


def update_device(device_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
    old = query_one("SELECT * FROM device WHERE id=?", (device_id,))
    if not old:
        raise ValueError("设备不存在")
    sets, params = [], []
    for f in FIELDS:
        if f not in body or body[f] is None:
            continue
        # 密码留空表示不修改
        if f in SECRET_FIELDS and body[f] == "":
            continue
        sets.append(f + "=?")
        params.append(1 if (f == "enabled" and body[f]) else
                      (0 if f == "enabled" else body[f]))
    if sets:
        params.append(device_id)
        execute("UPDATE device SET {0} WHERE id=?".format(", ".join(sets)), params)
    return get_device(device_id)


def delete_device(device_id: int) -> None:
    row = query_one("SELECT name FROM device WHERE id=?", (device_id,))
    if row:
        execute("DELETE FROM topo_link WHERE local_device=? OR remote_device=?",
                (row["name"], row["name"]))
    execute("DELETE FROM device WHERE id=?", (device_id,))


def test_device(device_id: int, command: str = "") -> Dict[str, Any]:
    """连通性测试：真连一次，跑一条命令看回显。"""
    d = get_device(device_id, reveal=True)
    if not d:
        raise ValueError("设备不存在")
    cmd = command or (d.get("lldp_cmd") or DEFAULT_LLDP_CMDS[0])
    tr = open_for(d)
    started = _now()
    try:
        tr.connect()
        res = tr.run(cmd)
    except Exception as exc:
        execute("UPDATE device SET last_status=?, last_checked=? WHERE id=?",
                ("失败：{0}".format(exc)[:120], started, device_id))
        return {"ok": False, "error": "{0}: {1}".format(type(exc).__name__, exc),
                "command": cmd, "output": ""}
    finally:
        tr.close()
    status = "正常" if res["ok"] else "命令失败"
    execute("UPDATE device SET last_status=?, last_checked=? WHERE id=?",
            (status, started, device_id))
    return {"ok": res["ok"], "error": res.get("error", ""), "command": cmd,
            "output": res["text"], "protocol": tr.name}


# ── LLDP 拓扑发现 ──────────────────────────────────────────────────────
LLDP_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "neighbors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "local_port": {"type": "string", "description": "本端接口名"},
                    "remote_device": {"type": "string", "description": "对端系统名"},
                    "remote_port": {"type": "string", "description": "对端接口名"},
                },
                "required": ["local_port", "remote_device", "remote_port"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["neighbors"],
    "additionalProperties": False,
}

LLDP_SYSTEM = (
    "你是 LLDP 邻居表解析器。把给定的 CLI 回显解析成邻居列表。"
    "只输出回显里真实存在的邻居，不得臆造。"
    "local_port 是本端接口名，remote_device 是对端设备的系统名（不是 Chassis ID），"
    "remote_port 是对端接口名。表头行、统计行、空行一律忽略。"
)

# 通用正则兜底：抓「本端口 … 对端名 … 对端口」三元组。
# 尾部允许还有列（TTL、Capability 等）—— 不同厂商列数不一样，不能写死行尾。
_IF = r"[A-Za-z][\w\-./]*\d[\w\-./]*"
_SYS = r"[A-Za-z][\w\-.]{1,40}"
_MAC = r"(?:[0-9a-fA-F]{2,4}[-:.][0-9a-fA-F\-:.]+\s+)?"
_TAIL = r"(?:\s+\S+)*\s*$"
FALLBACK_RES = [
    # 本端口 → 系统名 → [Chassis] → 对端口 → [其它列]
    re.compile(r"^\s*(?P<local>{0})\s+(?P<rdev>{1})\s+{2}(?P<rport>{0}){3}".format(
        _IF, _SYS, _MAC, _TAIL), re.M),
    # 系统名 → 本端口 → [Chassis] → 对端口 → [其它列]
    re.compile(r"^\s*(?P<rdev>{1})\s+(?P<local>{0})\s+{2}(?P<rport>{0}){3}".format(
        _IF, _SYS, _MAC, _TAIL), re.M),
]
HEADER_HINT = re.compile(r"local|remote|neighbor|chassis|port|system", re.I)


def parse_lldp(output: str, known_devices: List[str]) -> Dict[str, Any]:
    """回显 → 邻居列表。优先 AI，退到正则。"""
    if not output.strip():
        return {"neighbors": [], "engine": "none", "error": "回显为空"}

    res = llm.call_json("topo.lldp", LLDP_SYSTEM,
                        "已知设备名：{0}\n\nLLDP 回显：\n{1}".format(
                            ", ".join(sorted(known_devices)) or "（未知）", output),
                        LLDP_SCHEMA, max_tokens=4000)
    if res["ok"]:
        out = []
        for n in res["data"].get("neighbors", []):
            lp, rd, rp = (str(n.get("local_port", "")).strip(),
                          str(n.get("remote_device", "")).strip(),
                          str(n.get("remote_port", "")).strip())
            if lp and rd:
                out.append({"local_port": lp, "remote_device": rd, "remote_port": rp})
        return {"neighbors": out, "engine": "ai", "cached": res.get("cached", False),
                "error": ""}

    found: List[Dict[str, str]] = []
    for line in output.split("\n"):
        if HEADER_HINT.search(line) and not re.search(r"\d", line):
            continue
        for pattern in FALLBACK_RES:
            m = pattern.match(line)
            if m:
                found.append({"local_port": m.group("local"),
                              "remote_device": m.group("rdev"),
                              "remote_port": m.group("rport")})
                break
    uniq = {(n["local_port"], n["remote_device"], n["remote_port"]): n for n in found}
    return {"neighbors": [uniq[k] for k in sorted(uniq)], "engine": "regex",
            "error": res.get("error", "")}


def discover_topology() -> Dict[str, Any]:
    """一键拓扑发现：逐台跑 LLDP → 解析 → 双向确认成边。

    只有两端都看得见对方的链路才标记 confirmed —— 单向的标出来但不当成实边，
    因为单向 LLDP 本身就是一种故障现象。
    """
    devices = enabled_devices(reveal=True)
    names = [d["name"] for d in devices]
    log: List[Dict[str, str]] = []
    raw_links: List[Tuple[str, str, str, str]] = []
    engines: List[str] = []

    for d in sorted(devices, key=lambda x: ((x.get("role") or ""), x["name"])):
        cmds = [d["lldp_cmd"]] if d.get("lldp_cmd") else DEFAULT_LLDP_CMDS
        tr = open_for(d)
        output, used, err = "", "", ""
        try:
            tr.connect()
            for cmd in cmds:
                r = tr.run(cmd)
                if r["ok"] and r["text"].strip() and "nrecognized" not in r["text"]:
                    output, used = r["text"], cmd
                    break
                err = r.get("error") or "命令无有效回显"
        except Exception as exc:
            err = "{0}: {1}".format(type(exc).__name__, exc)
        finally:
            tr.close()

        if not output:
            log.append({"device": d["name"], "level": "error",
                        "msg": "LLDP 采集失败：{0}".format(err)})
            continue
        parsed = parse_lldp(output, names)
        engines.append(parsed["engine"])
        selfloops = 0
        for n in parsed["neighbors"]:
            # 自环过滤：设备不可能是自己的 LLDP 邻居。
            # 二层网桥把本机帧反射回来时会出现这种条目，是采集噪声不是拓扑。
            if n["remote_device"].strip().lower() == d["name"].strip().lower():
                selfloops += 1
                continue
            raw_links.append((d["name"], n["local_port"],
                              n["remote_device"], n["remote_port"]))
        if selfloops:
            log.append({"device": d["name"], "level": "warn",
                        "msg": "过滤掉 {0} 条自环邻居（二层反射，非真实拓扑）".format(selfloops)})
        log.append({"device": d["name"], "level": "info",
                    "msg": "{0} → {1} 个邻居（解析引擎 {2}）".format(
                        used, len(parsed["neighbors"]), parsed["engine"])})

    # 双向确认
    seen = {(a, b) for a, _, b, _ in raw_links}
    execute("DELETE FROM topo_link", ())
    confirmed = 0
    for local, lport, remote, rport in sorted(set(raw_links)):
        ok = (remote, local) in seen
        confirmed += 1 if ok else 0
        if not ok:
            log.append({"device": local, "level": "warn",
                        "msg": "单向邻接：{0} → {1}（对端看不到本端）".format(local, remote)})
        execute("INSERT OR REPLACE INTO topo_link(local_device, local_port,"
                " remote_device, remote_port, confirmed, discovered_at)"
                " VALUES (?,?,?,?,?,?)",
                (local, lport, remote, rport, 1 if ok else 0, _now()))

    topo_hash = sha256_of(sorted(set(raw_links)))
    engine = engines[0] if len(set(engines)) == 1 and engines else "mixed"
    run_id = execute(
        "INSERT INTO topo_run(source, devices, links, confirmed, engine, topo_hash,"
        " log, created_at) VALUES ('lldp',?,?,?,?,?,?,?)",
        (len(devices), len(set(raw_links)), confirmed, engine, topo_hash,
         json.dumps(log, ensure_ascii=False), _now()))
    return {"run_id": run_id, "devices": len(devices), "links": len(set(raw_links)),
            "confirmed": confirmed, "engine": engine, "topo_hash": topo_hash,
            "log": log}


def topology() -> Dict[str, Any]:
    """当前拓扑：节点 + 边。节点按 (role, name) 全序，边按四元组全序。"""
    devices = list_devices()
    links = query("SELECT * FROM topo_link ORDER BY local_device, local_port,"
                  " remote_device, remote_port")
    known = {d["name"] for d in devices}
    nodes = [{"name": d["name"], "role": d["role"], "vendor": d["vendor"],
              "model": d["model"], "host": d["host"], "protocol": d["protocol"],
              "enabled": d["enabled"], "status": d["last_status"], "known": True}
             for d in devices]
    # LLDP 发现的、但清单里没有的设备也画出来 —— 那本身是有用的信息
    for l in links:
        if l["remote_device"] not in known:
            known.add(l["remote_device"])
            nodes.append({"name": l["remote_device"], "role": "UNKNOWN", "vendor": "",
                          "model": "", "host": "", "protocol": "", "enabled": False,
                          "status": "LLDP 发现，清单中无此设备", "known": False})
    # 无向去重：同一条链路只保留一条
    edges, seen = [], set()
    for l in links:
        key = tuple(sorted([(l["local_device"], l["local_port"]),
                            (l["remote_device"], l["remote_port"])]))
        if key in seen:
            continue
        seen.add(key)
        edges.append({"a": l["local_device"], "a_port": l["local_port"],
                      "b": l["remote_device"], "b_port": l["remote_port"],
                      "confirmed": bool(l["confirmed"])})
    last = query_one("SELECT * FROM topo_run ORDER BY id DESC LIMIT 1")
    if last:
        last["log"] = loads(last["log"], [])
    return {"nodes": sorted(nodes, key=lambda n: (n["role"], n["name"])),
            "edges": sorted(edges, key=lambda e: (e["a"], e["a_port"], e["b"])),
            "last_run": last}


def topology_context() -> str:
    """拓扑的文本形态 —— 作为诊断上下文送给大模型。

    必须是确定性的：节点与边全序排列，不含时间戳。
    """
    t = topology()
    if not t["edges"] and not t["nodes"]:
        return ""
    lines = ["# 网络拓扑（LLDP 发现）", "", "## 设备"]
    for n in t["nodes"]:
        lines.append("- {0}（角色 {1}{2}）{3}".format(
            n["name"], n["role"],
            "，{0} {1}".format(n["vendor"], n["model"]).rstrip() if n["vendor"] else "",
            "  ← 清单中无此设备" if not n["known"] else ""))
    lines.append("")
    lines.append("## 链路")
    if not t["edges"]:
        lines.append("- （尚未发现链路）")
    for e in t["edges"]:
        lines.append("- {0} {1} <--> {2} {3}{4}".format(
            e["a"], e["a_port"], e["b"], e["b_port"],
            "" if e["confirmed"] else "   ← 单向邻接，对端看不到本端"))
    return "\n".join(lines)


def topology_hash() -> str:
    t = topology()
    return sha256_of({"nodes": [n["name"] + "/" + n["role"] for n in t["nodes"]],
                      "edges": [[e["a"], e["a_port"], e["b"], e["b_port"],
                                 e["confirmed"]] for e in t["edges"]]})


# ── 命令能力（手册 × 实机 交叉校验）────────────────────────────────────
def mark_command(device: str, command: str, supported: bool, reason: str = "") -> None:
    execute("INSERT OR REPLACE INTO device_command(device, command, supported, reason,"
            " checked_at) VALUES (?,?,?,?,?)",
            (device, command, 1 if supported else 0, reason[:200], _now()))


def unsupported_map() -> Dict[str, set]:
    """每台设备已知不支持的命令。采集时自动学习，不必人工维护。"""
    out: Dict[str, set] = {}
    for r in query("SELECT device, command FROM device_command WHERE supported=0"):
        out.setdefault(r["device"], set()).add(r["command"])
    return out


def capabilities(device: str = "") -> List[Dict[str, Any]]:
    sql = "SELECT * FROM device_command"
    params: List[Any] = []
    if device:
        sql += " WHERE device=?"
        params.append(device)
    sql += " ORDER BY device, command"
    rows = query(sql, params)
    for r in rows:
        r["supported"] = bool(r["supported"])
    return rows


def probe_device(device_id: int) -> Dict[str, Any]:
    """把知识库里所有已启用命令在这台设备上跑一遍，记录支持与否。

    这就是「手册说有，设备未必有」的正面解法：一次探测，之后编排层直接避开。
    """
    from ..kb import service as kb
    d = get_device(device_id, reveal=True)
    if not d:
        raise ValueError("设备不存在")
    # 需要必需参数的命令不探测：裸发一定被拒，那不代表设备不支持它
    all_cmds = kb.list_commands(enabled_only=True)
    cmds = [c["command"] for c in all_cmds if not (c.get("required") or [])]
    skipped = [c["command"] for c in all_cmds if c.get("required")]
    tr = open_for(d)
    ok = bad = 0
    details: List[Dict[str, Any]] = []
    try:
        tr.connect()
        for cmd in cmds:
            r = tr.run(cmd)
            supported = not r.get("unsupported")
            mark_command(d["name"], cmd, supported, r.get("error", ""))
            details.append({"command": cmd, "supported": supported,
                            "reason": r.get("error", "")})
            ok += 1 if supported else 0
            bad += 0 if supported else 1
    finally:
        tr.close()
    return {"device": d["name"], "total": len(cmds), "supported": ok,
            "unsupported": bad, "skipped": len(skipped),
            "skipped_commands": sorted(skipped), "details": details}


def push_reporting(device_id: int) -> Dict[str, Any]:
    """按设备自己的上报配置下发 syslog / trap 命令（模板来自厂商预设，可手改）。

    端口为 0 或模板为空的那一项跳过。命令进 config 视图执行；不同厂商进入
    配置视图的方式不同，模板里可自行带上（如 "system-view"）。"""
    from .transport import open_for
    d = get_device(device_id, reveal=True)
    if not d:
        raise ValueError("设备不存在")
    host = (d.get("report_host") or "").strip()
    if not host:
        raise ValueError("未配置上报目标地址")
    cmds: List[str] = []
    for key, port in (("syslog_cmd", d.get("syslog_port") or 0),
                      ("trap_cmd", d.get("trap_port") or 0)):
        tpl = (d.get(key) or "").strip()
        if tpl and port:
            cmds.append(tpl.format(host=host, port=port))
    if not cmds:
        raise ValueError("没有可下发的上报命令（端口为 0 或模板为空）")
    tr = open_for(d)
    tr.connect()
    results = []
    try:
        for cmd in ["config"] + cmds + ["end"]:
            r = tr.run(cmd)
            if cmd not in ("config", "end"):
                results.append({"command": cmd, "ok": bool(r.get("ok", True)),
                                "output": (r.get("text") or "")[:200]})
    finally:
        tr.close()
    return {"device": d["name"], "results": results}
