"""Syslog 服务器 + SNMP Trap 接收编排 + 事件上下文。

设备侧只需两条既有配置命令（满足「设备侧少变动」）：
    syslog server <host> [port]      → dev 模块，RFC3164 UDP 上报
    snmp trap server <host> [port]   → snmp 模块，SNMPv2c trap

Syslog 在 Python 里直接收（一个 UDP socket 的事）；trap 用 pysnmp 收
（SNMPv2c，多端口，community 可配），OID 符号化解码查 mibs 模块编译出的索引。

事件同时是**诊断输入**：归一化后的事件摘要送给 Agent 作上下文，其摘要哈希
参与诊断指纹 —— 新事件出现 = 设备状态变了 = 理应重诊；事件集合不变则指纹
不变，一致性不受影响。归一化去掉逐条时间戳、按内容去重计数分桶，避免
「同一状态因为时间戳而变成不同输入」。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import socket
import socketserver
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from ...core.canon import sha256_of
from ...core.db import execute, loads, query, query_one
from ..mibs import service as mibs
from . import models  # noqa: F401  —— 注册建表语句

SYSLOG_PORT_DEFAULT = 5514
TRAP_PORT_DEFAULT = 1162
MAX_EVENTS_KEPT = 5000
CONTEXT_MAX_KINDS = 40          # 进模型的去重事件类型上限

_LOCK = threading.Lock()
_SYSLOG: Dict[int, "socketserver.UDPServer"] = {}
_TRAP_ENGINE = None
_TRAP_THREAD: Optional[threading.Thread] = None
_TRAP_PORTS: List[int] = []
COMMUNITY_KEY = "trap_communities"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ── 源 IP → 设备名 ────────────────────────────────────────────────────
def source_map() -> Dict[str, str]:
    return {r["source_ip"]: r["device"] for r in query("SELECT * FROM event_source")}


def set_source(source_ip: str, device: str, origin: str = "manual") -> None:
    execute("INSERT OR REPLACE INTO event_source(source_ip, device, origin,"
            " created_at) VALUES (?,?,?,?)", (source_ip, device, origin, _now()))


def discover_sources() -> Dict[str, str]:
    """尽力而为的自动映射。

    1) 设备表 host 直接当源地址（远程真机场景天然成立）
    2) 本机 docker 里 hostname 与设备名相同的容器，取其网络 IP
       （实验环境容器的 hostname 与设备名一致时可用）
    """
    from ..devices import service as device_service
    devices = device_service.enabled_devices()
    for d in devices:
        host = d.get("host") or ""
        if host and host not in ("127.0.0.1", "localhost"):
            set_source(host, d["name"], "host-match")

    try:
        names = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10).stdout.split()
        want = {d["name"] for d in devices}
        for cname in names:
            info = subprocess.run(
                ["docker", "inspect", cname, "--format",
                 "{{.Config.Hostname}} {{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
                capture_output=True, text=True, timeout=10).stdout.split()
            if info and info[0] in want:
                for ip in info[1:]:
                    if ip:
                        set_source(ip, info[0], "auto-docker")
    except Exception:
        pass
    return source_map()


def _device_ports(kind: str) -> Dict[int, str]:
    """设备配置里的接收端口 → 设备名。端口是设备身份（NAT 改不了端口）。"""
    col = "syslog_port" if kind == "syslog" else "trap_port"
    return {int(r[col]): r["name"] for r in query(
        "SELECT name, {0} FROM device WHERE enabled=1 AND {0} > 0".format(col))}


def _device_of(source_ip: str, listen_port: int = 0, kind: str = "") -> str:
    """归属顺序：设备独立端口（NAT/实验环境）→ 源 IP = 设备管理地址（真实设备的
    常态：都用默认端口即可）→ 手动维护的源 IP 映射。"""
    if listen_port:
        hit = _device_ports(kind).get(listen_port)
        if hit:
            return hit
    if source_ip:
        row = query_one("SELECT name FROM device WHERE enabled=1 AND host=?", (source_ip,))
        if row:
            return row["name"]
    return source_map().get(source_ip, "")


def listen_defaults() -> Dict[str, int]:
    from ..settings import service as settings
    try:
        sp = int(settings.get("syslog_listen_port") or SYSLOG_PORT_DEFAULT)
        tp = int(settings.get("trap_listen_port") or TRAP_PORT_DEFAULT)
    except ValueError:
        sp, tp = SYSLOG_PORT_DEFAULT, TRAP_PORT_DEFAULT
    return {"syslog": sp, "trap": tp}


def set_listen_defaults(syslog_port: int = 0, trap_port: int = 0) -> None:
    from ..settings import service as settings
    if syslog_port:
        settings.put("syslog_listen_port", str(int(syslog_port)))
    if trap_port:
        settings.put("trap_listen_port", str(int(trap_port)))


# ── 入库 ─────────────────────────────────────────────────────────────
def store_event(kind: str, source_ip: str, *, severity: str = "", module: str = "",
                event: str = "", message: str = "", trap_oid: str = "",
                varbinds: Optional[List[Dict[str, str]]] = None,
                raw: str = "", listen_port: int = 0) -> int:
    eid = execute(
        "INSERT INTO event(kind, source_ip, device, severity, module, event,"
        " message, trap_oid, varbinds, raw, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (kind, source_ip, _device_of(source_ip, listen_port, kind),
         severity, module, event,
         message, trap_oid, json.dumps(varbinds or [], ensure_ascii=False),
         raw[:2000], _now()))
    execute("DELETE FROM event WHERE id <= (SELECT MAX(id) FROM event) - ?",
            (MAX_EVENTS_KEPT,))
    return eid


def ingest_trap(payload: Dict[str, Any]) -> int:
    """已解码的 trap 事件入库（varbinds 已符号化）。"""
    return store_event(
        "trap", payload.get("source_ip", ""),
        listen_port=int(payload.get("listen_port") or 0),
        event=payload.get("trap_name") or payload.get("trap_oid", ""),
        message="; ".join("{0}={1}".format(v.get("name", v.get("oid", "")),
                                           v.get("value", ""))
                          for v in payload.get("varbinds", [])),
        trap_oid=payload.get("trap_oid", ""),
        varbinds=payload.get("varbinds"),
        raw=json.dumps(payload, ensure_ascii=False))


# ── Syslog 服务器（RFC3164：<PRI>时间戳 ident 模块/事件: 消息）────────
# 两种主流线上格式都认：
#   RFC5424  <PRI>1 时间戳 主机 应用 进程 消息ID [结构化数据] 消息
#   RFC3164  <PRI>时间戳 [主机] 标签[: ]消息
# 消息体若以「模块/事件:」开头（不少设备的事件日志都这么写），再细拆一层。
RFC5424_RE = re.compile(
    r"^<(?P<pri>\d{1,3})>1\s+(?P<ts>\S+)\s+(?P<host>\S+)\s+(?P<app>\S+)\s+"
    r"(?P<proc>\S+)\s+(?P<msgid>\S+)\s+(?P<sd>-|\[.*?\])\s*(?P<msg>.*)$", re.S)
RFC3164_RE = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<ts>\w{3}\s+\d{1,2}\s[\d:]{8})\s+(?P<rest>.*)$", re.S)
MODULE_EVENT_RE = re.compile(r"^(?P<module>[A-Za-z0-9_\-]+)/(?P<event>[^:\s]+):\s*(?P<msg>.*)$", re.S)
SEVERITIES = ["emerg", "alert", "crit", "error", "warning", "notice", "info", "debug"]


def parse_syslog(raw: str, source_ip: str) -> Dict[str, Any]:
    text = raw.strip()
    out = {"kind": "syslog", "source_ip": source_ip, "severity": "",
           "module": "", "event": "", "message": text[:500], "raw": raw}
    m = RFC5424_RE.match(text)
    body, tag = "", ""
    if m:
        body, tag = m.group("msg"), (m.group("app") if m.group("app") != "-" else "")
    else:
        m = RFC3164_RE.match(text)
        if not m:
            return out
        rest = m.group("rest")
        # rest = [主机 ]标签[: ]消息；标签形如 ident / ident[pid] / 模块/事件
        words = rest.split(None, 2)
        if len(words) >= 2 and "/" not in words[0] and not words[0].endswith(":"):
            words = words[1:] if len(words) > 1 else words       # 去掉主机名
            rest = " ".join(words)
        body = rest
        head = rest.split(None, 1)
        if head and not MODULE_EVENT_RE.match(rest):
            tag = head[0].rstrip(":")
            body = head[1] if len(head) > 1 else ""
    out["severity"] = SEVERITIES[int(m.group("pri")) % 8]
    me = MODULE_EVENT_RE.match(body)
    if me:
        out.update(module=me.group("module"), event=me.group("event"),
                   message=me.group("msg")[:500])
    else:
        out.update(module=tag, message=body[:500])
    return out


class _SyslogHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request[0]
        try:
            text = data.decode("utf-8", "replace")
        except Exception:
            return
        p = parse_syslog(text, self.client_address[0])
        store_event("syslog", p["source_ip"], severity=p["severity"],
                    module=p["module"], event=p["event"],
                    message=p["message"], raw=p["raw"],
                    listen_port=self.server.server_address[1])


def _syslog_ports() -> List[int]:
    """监听 = 默认端口 ∪ 各设备配置的 syslog 端口。"""
    return sorted({listen_defaults()["syslog"], *_device_ports("syslog")})


def start_syslog(ports: Optional[List[int]] = None) -> Dict[str, Any]:
    global _SYSLOG
    want = sorted(set(ports or _syslog_ports()))
    with _LOCK:
        stop_syslog()
        for port in want:
            server = socketserver.ThreadingUDPServer(("0.0.0.0", port),
                                                     _SyslogHandler)
            threading.Thread(target=server.serve_forever, daemon=True,
                             name="syslog-{0}".format(port)).start()
            _SYSLOG[port] = server
    return {"running": True, "ports": want}


def stop_syslog() -> None:
    global _SYSLOG
    for server in _SYSLOG.values():
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
    _SYSLOG = {}


# ── Trap 接收（pysnmp，多端口，community 可配）────────────────────────
SNMP_TRAP_OID = "1.3.6.1.6.3.1.1.4.1.0"
SYSUPTIME_OID = "1.3.6.1.2.1.1.3.0"


def communities() -> List[str]:
    from ..settings import service as settings
    raw = settings.get(COMMUNITY_KEY) or ""
    return [c.strip() for c in raw.split(",") if c.strip()] or ["public"]


def set_communities(values: List[str]) -> None:
    from ..settings import service as settings
    settings.put(COMMUNITY_KEY, ",".join(v.strip() for v in values if v.strip()))


def _trap_ports() -> List[int]:
    return sorted({listen_defaults()["trap"], *_device_ports("trap")})


def _pretty(value: Any) -> str:
    try:
        return value.prettyPrint()
    except Exception:
        return str(value)


def _on_notification(snmp_engine, state_ref, _ctx_eid, _ctx_name, var_binds, _cb):
    try:
        md = getattr(snmp_engine, "message_dispatcher", None) or snmp_engine.msgAndPduDsp
        get_info = getattr(md, "get_transport_info", None) or md.getTransportInfo
        domain, address = get_info(state_ref)
        listen_port = _TRAP_PORTS[domain[-1]] if domain and _TRAP_PORTS else 0
        source_ip = str(address[0]) if address else ""
    except Exception:
        listen_port, source_ip = 0, ""
    trap_oid, uptime, varbinds = "", "", []
    for name, val in var_binds:
        oid = str(name)
        if oid == SNMP_TRAP_OID:
            trap_oid = str(val)
            continue
        if oid == SYSUPTIME_OID:
            uptime = _pretty(val)
            continue
        varbinds.append({"oid": oid, "name": mibs.translate(oid), "value": _pretty(val)})
    ingest_trap({"source_ip": source_ip, "listen_port": listen_port,
                 "trap_oid": trap_oid,
                 "trap_name": mibs.translate(trap_oid) if trap_oid else "",
                 "uptime": uptime, "varbinds": varbinds})


_TRAP_LOOP = None
_TRAP_ERROR = ""


def start_trap(ports: Optional[List[int]] = None) -> Dict[str, Any]:
    """pysnmp 7（asyncio）接收器：专用线程里跑自己的事件循环，
    一个引擎挂多个 UDP transport，transport 序号 ↔ 端口。"""
    global _TRAP_ENGINE, _TRAP_THREAD, _TRAP_PORTS, _TRAP_LOOP, _TRAP_ERROR
    import asyncio

    want = sorted(set(ports or _trap_ports()))
    with _LOCK:
        if _TRAP_ENGINE is not None and _TRAP_PORTS == want:
            return {"running": True, "ports": want}
        stop_trap()
        ready = threading.Event()
        errors: List[str] = []

        def _run() -> None:
            global _TRAP_ENGINE, _TRAP_LOOP
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                from pysnmp.carrier.asyncio.dgram import udp
                from pysnmp.entity import config, engine
                from pysnmp.entity.rfc3413 import ntfrcv
                eng = engine.SnmpEngine()
                for i, port in enumerate(want):
                    config.add_transport(
                        eng, udp.DOMAIN_NAME + (i,),
                        udp.UdpTransport().open_server_mode(("0.0.0.0", port)))
                for i, comm in enumerate(communities()):
                    config.add_v1_system(eng, "detops-{0}".format(i), comm)
                ntfrcv.NotificationReceiver(eng, _on_notification)
                _TRAP_ENGINE, _TRAP_LOOP = eng, loop
            except Exception as exc:
                errors.append("{0}: {1}".format(type(exc).__name__, exc))
                ready.set()
                return
            ready.set()
            try:
                eng.transport_dispatcher.job_started(1)
                eng.transport_dispatcher.run_dispatcher()
            except Exception:
                pass
            finally:
                try:
                    eng.transport_dispatcher.close_dispatcher()
                except Exception:
                    pass

        _TRAP_PORTS = want
        _TRAP_THREAD = threading.Thread(target=_run, daemon=True, name="trap-receiver")
        _TRAP_THREAD.start()
        ready.wait(10)
        if errors:
            _TRAP_ENGINE, _TRAP_LOOP, _TRAP_PORTS = None, None, []
            _TRAP_ERROR = errors[0]
            raise RuntimeError("trap 接收器启动失败: " + errors[0])
        _TRAP_ERROR = ""
    return {"running": True, "ports": want}


def stop_trap() -> None:
    global _TRAP_ENGINE, _TRAP_THREAD, _TRAP_PORTS, _TRAP_LOOP
    eng, loop, thread = _TRAP_ENGINE, _TRAP_LOOP, _TRAP_THREAD
    _TRAP_ENGINE, _TRAP_LOOP, _TRAP_PORTS, _TRAP_THREAD = None, None, [], None
    if eng is not None and loop is not None:
        def _shutdown():
            try:
                eng.transport_dispatcher.job_finished(1)
                eng.transport_dispatcher.close_dispatcher()
            except Exception:
                pass
            loop.stop()
        try:
            loop.call_soon_threadsafe(_shutdown)
        except Exception:
            pass
    if thread is not None:
        thread.join(timeout=3)


def receivers_status() -> Dict[str, Any]:
    return {
        "syslog": {"running": bool(_SYSLOG), "ports": sorted(_SYSLOG)},
        "trap": {"running": _TRAP_ENGINE is not None, "ports": _TRAP_PORTS,
                 "communities": communities(), "error": _TRAP_ERROR},
        "mib": mibs.status(),
        "sources": source_map(),
        "defaults": listen_defaults(),
        "device_ports": {"syslog": _device_ports("syslog"),
                         "trap": _device_ports("trap")},
    }


# ── 设备侧一键配置 ────────────────────────────────────────────────────
def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _candidate_hosts() -> List[str]:
    """设备（容器）视角可能到达宿主机的地址，按经验顺序。"""
    cands: List[str] = []
    try:
        gw = subprocess.run(
            ["docker", "network", "inspect", "nn-mgmt", "--format",
             "{{(index .IPAM.Config 0).Gateway}}"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        if gw:
            cands.append(gw)
    except Exception:
        pass
    cands.append(_lan_ip())
    cands.append("host.docker.internal")
    return [c for i, c in enumerate(cands) if c and c not in cands[:i]]


_PROBED_HOST = ""


def _lab_network_present() -> bool:
    """仅当本机 docker 里有实验环境的 nn-mgmt 网络时才认为是实验环境。"""
    try:
        r = subprocess.run(["docker", "network", "inspect", "nn-mgmt"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def suggest_target_host(force: bool = False) -> str:
    """实测选址，不按平台猜。

    原生 Docker 里网桥网关直达宿主机；Docker Desktop（Mac / Linux）容器在虚拟机里，
    网关是虚拟机的，host.docker.internal 的 UDP 转发又因版本而异。所以：后台临时
    开一个 UDP 端口，从设备所在网段起容器向每个候选地址发探测包，谁先到用谁。
    结果缓存；docker 不可用时退回宿主机局域网 IP。"""
    global _PROBED_HOST
    if _PROBED_HOST and not force:
        return _PROBED_HOST
    if not _lab_network_present():
        # 真实设备：设备直接把日志/trap 发到本服务器的 IP，没有容器那层，不探测
        _PROBED_HOST = _lan_ip()
        return _PROBED_HOST
    cands = _candidate_hosts()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", 0))
        port = sock.getsockname()[1]
        sock.settimeout(0.2)
        for cand in cands:
            token = "detops-probe-{0}".format(cand)
            try:
                subprocess.run(
                    ["docker", "run", "--rm", "--network", "nn-mgmt", "alpine", "sh", "-c",
                     "echo '{0}' | nc -u -w1 {1} {2}".format(token, cand, port)],
                    capture_output=True, text=True, timeout=25)
            except Exception:
                continue
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    data, _addr = sock.recvfrom(256)
                except socket.timeout:
                    continue
                if token.encode() in data:
                    _PROBED_HOST = cand
                    return cand
    except Exception:
        pass
    finally:
        sock.close()
    _PROBED_HOST = ""
    return _lan_ip()


# ── 查询 ─────────────────────────────────────────────────────────────
def list_events(kind: str = "", device: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    sql, params = "SELECT * FROM event", []
    conds = []
    if kind:
        conds.append("kind=?")
        params.append(kind)
    if device:
        conds.append("device=?")
        params.append(device)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(limit, 1000)))
    rows = query(sql, params)
    for r in rows:
        r["varbinds"] = loads(r["varbinds"], [])
    return rows


def clear_events() -> None:
    execute("DELETE FROM event")


# ── 诊断上下文（归一化，参与指纹）─────────────────────────────────────
_NUM_RE = re.compile(r"\b\d+\b")


def context_for(devices: List[str]) -> Tuple[str, str]:
    """近期事件 → (归一化文本, 摘要哈希)。

    只带内容不带时间戳，且**只记类型存在性、不记次数** —— 计数会在
    反复 flap 中途从「1次」翻到「多次」，让检查途中的输入悄悄变化；
    存在性只在**新类型事件**出现时变化，那正是「设备状态变了」的时刻。
    """
    # cli 审计在 SQL 层就排除 —— 它不但是观测回声，还会把状态事件
    # 挤出「最近 N 条」窗口（一次诊断就下发几十条命令）。
    rows = query("SELECT device, kind, severity, module, event, message"
                 " FROM event WHERE module != 'cli'"
                 " ORDER BY id DESC LIMIT 500")
    want = set(devices)
    kinds = set()
    for r in rows:
        if r["module"] == "cli":
            # CLI 审计是「观测行为自身的回声」：诊断采集每下发一条命令都会
            # 产生一条，它若进上下文，采集就会改变输入、指纹永不稳定。
            continue
        if r["device"] and want and r["device"] not in want:
            continue
        kinds.add((r["device"] or "?", r["kind"], r["severity"], r["module"],
                   r["event"], _NUM_RE.sub("<n>", r["message"])[:160]))
    if not kinds:
        return "", ""
    lines = ["- [{0}][{1}] {2} {3}/{4}: {5}".format(*key)
             for key in sorted(kinds)]
    text = "\n".join(lines[:CONTEXT_MAX_KINDS])
    return text, sha256_of(text)
