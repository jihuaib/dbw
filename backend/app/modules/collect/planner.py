"""采集编排：用户提问 → 该下发哪些命令。

这一步允许 AI 参与，但被三重夹紧：
  1. **闭集**：只能从知识库里已启用的只读命令中选，不能自创命令
  2. **缓存冻结**：按 (归一化提问 + 命令清单 + 设备集 + 版本) 缓存，
     同一问题只问模型一次，之后永远命中
  3. **兜底**：AI 不可用时全量下发所有已启用命令 —— 更慢但完全确定，且不漏证据

参数（IP / 接口 / MAC）由正则确定性抽取，不让模型自由发挥。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ...core import llm
from ...core.canon import canonical_json, sha256_of
from ...core.config import PLAN_VERSION
from ..devices import service as device_service
from ..kb import service as kb
from ..kb import syntax as cli_syntax

# 计划规模上限。一次诊断挑几百条命令不是诊断，是数据倾倒 ——
# 采集慢、快照大、模型还得在噪声里找信号。
MAX_PER_DEVICE = 14
MAX_TOTAL = 64
IPV4_RE = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3})\b")
MAC_RE = re.compile(r"\b([0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})\b")
IFACE_RE = re.compile(
    r"\b((?:GigabitEthernet|Ten-GigabitEthernet|FortyGigE|HundredGigE|GE|XGE|"
    r"Vlan-interface|Vlan|LoopBack)\s?\d+(?:/\d+){0,3})\b", re.I)


def normalize_question(text: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKC", text or "").strip()
    s = re.sub(r"\s+", " ", s)
    return "".join(ch.lower() if ch.isascii() else ch for ch in s)


def extract_entities(text: str, device_names: List[str]) -> Dict[str, Any]:
    """实体抽取用正则 + 清单校验，可复现。"""
    out: Dict[str, Any] = {}
    ips = [ip for ip in IPV4_RE.findall(text)
           if all(0 <= int(p) <= 255 for p in ip.split("."))]
    if ips:
        out["ips"] = sorted(set(ips))
    macs = MAC_RE.findall(text)
    if macs:
        out["macs"] = sorted({m.lower() for m in macs})
    ifaces = IFACE_RE.findall(text)
    if ifaces:
        out["interfaces"] = sorted({re.sub(r"\s+", "", i) for i in ifaces})
    hits = [d for d in device_names if d.lower() in text.lower()]
    if hits:
        out["devices"] = sorted(hits)
    return out


PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "完整命令，必须来自给定清单，可追加参数值"},
                    "devices": {"type": "array", "items": {"type": "string"},
                                "description": "在哪些设备上执行，必须来自给定设备清单"},
                    "reason": {"type": "string", "description": "为什么要采这一条"},
                },
                "required": ["command", "devices", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["steps"],
    "additionalProperties": False,
}

PLAN_SYSTEM = (
    "你是网络诊断的采集编排器。根据用户的问题，从给定的**只读命令清单**里挑出"
    "需要下发的命令，并指定在哪些设备上执行。\n"
    "硬约束：\n"
    "1. command 必须是清单里出现过的命令；不得自创命令。\n"
    "2. **每台设备最多 {0} 条、总共最多 {1} 条**。挑判断根因真正需要的，"
    "不要把清单整个倒出来 —— 命令越多，采集越慢，噪声越大。\n"
    "3. 排查链路类问题时，把上下游证据采全："
    "接口状态、邻居协议、路由、转发表、ARP、拓扑邻接。\n"
    "4. devices 必须来自给定设备清单。问题没点名设备时，采所有设备。\n"
    "5. unsupported 里列出的「设备 → 命令」是已探明该设备不支持的，不要再提议。\n"
    "6. 标了「必须补齐参数」的命令：**参数值只能取自 entities 里给出的实体**"
    "（IP / 接口名 / MAC）。**绝对不要凭空猜参数值**（比如猜一个进程号或 tag）——"
    "猜错了设备会拒绝，错误文本会污染证据。拿不到真实值就别选这条命令。\n"
    "7. 命令大小写照抄清单，设备的接口名（如 GE-1）区分大小写。"
).format(MAX_PER_DEVICE, MAX_TOTAL)


def _catalog_index() -> Dict[str, Dict[str, Any]]:
    """完整 syntax 是身份；同一命令前缀可以有多个正式语法变体。"""
    out: Dict[str, Dict[str, Any]] = {}
    for index, command in enumerate(kb.list_commands(enabled_only=True)):
        identity = (command.get("syntax") or command["command"]).lower()
        out["{0}\x1f{1}".format(identity, command.get("id", index))] = command
    return out


def _legacy_validate(raw: str, entry: Dict[str, Any]) -> Optional[str]:
    """Compatibility for tests/old rows that do not carry a syntax grammar."""
    tokens = raw.split()
    base_tokens = str(entry.get("command", "")).split()
    if len(tokens) < len(base_tokens):
        return None
    if [x.lower() for x in tokens[:len(base_tokens)]] != \
            [x.lower() for x in base_tokens]:
        return None
    args = tokens[len(base_tokens):]
    if any(not cli_syntax.SAFE_TOKEN_RE.fullmatch(arg) for arg in args):
        return None
    if len(args) != len(entry.get("required") or []):
        return None
    return " ".join(base_tokens + args)


def _validate(command: str, catalog: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """只放行清单里存在的命令。

    · 匹配用小写，**下发用清单里的原始大小写**（设备的 GE-1 区分大小写）
    · 必需参数没填齐的一律不放行 —— 那种命令设备会回 "Incomplete command"，
      错误文本混进证据快照就是在污染诊断输入
    """
    original = str(command)
    # 必须在 split() 之前拒绝换行/控制字符，否则折叠后注入痕迹已经消失。
    if not cli_syntax.safe_command_text(original):
        return None
    raw = " ".join(original.split())
    matches = []
    for entry in catalog.values():
        grammar = entry.get("syntax")
        if grammar:
            got = cli_syntax.match(grammar, raw)
            if got:
                matches.append((
                    -got.literal_count, got.parameter_count,
                    str(grammar).lower(), got.command))
        else:
            got_command = _legacy_validate(raw, entry)
            if got_command:
                matches.append((0, len(entry.get("required") or []),
                                str(entry.get("command", "")).lower(), got_command))
    return min(matches)[-1] if matches else None


def build_plan(question: str, devices: List[str],
               entities: Dict[str, Any]) -> Dict[str, Any]:
    """返回 {steps, plan_hash, engine, error}。steps 已按 (设备, 命令) 全序排列。"""
    catalog = _catalog_index()
    if not catalog:
        return {"steps": [], "plan_hash": "", "engine": "none", "cached": False,
                "error": "知识库里没有已启用的命令，请先在「知识库」导入资料"}

    q_norm = normalize_question(question)
    # 已知这台设备不支持的命令，直接排除 —— 手册说有不代表设备有
    blocked = device_service.unsupported_map()
    listing = "\n".join(
        "- {0}{1}{2}".format(
            c.get("syntax") or c["command"],
            "  # " + c["purpose"] if c["purpose"] else "",
            ("  含必填参数，按完整语法补齐；参数候选: " +
             "/".join(c.get("params") or c["required"]) if c.get("required")
             else ("  可直接下发: " + c["command"]
                   if c.get("syntax") != c["command"] else "")))
        for c in (catalog[k] for k in sorted(catalog)))
    content = canonical_json({
        "question": q_norm,
        "entities": entities,
        "devices": sorted(devices),
        "catalog": listing,
        # 已探明这些设备不支持的命令，别再提议了
        "unsupported": {d: sorted(c) for d, c in sorted(blocked.items()) if c},
        "plan_version": PLAN_VERSION,
    })

    res = llm.call_json("collect.plan", PLAN_SYSTEM,
                        "请给出采集计划。\n\n输入（JSON）：\n" + content,
                        PLAN_SCHEMA, max_tokens=4000)
    engine, error = "ai", ""
    steps: List[Dict[str, Any]] = []
    if res["ok"]:
        seen: Set[Tuple[str, str]] = set()
        for s in res["data"].get("steps", []):
            cmd = _validate(s.get("command", ""), catalog)
            if not cmd:
                continue
            targets = [d for d in s.get("devices", []) if d in devices] or list(devices)
            for dev in sorted(set(targets)):
                if (dev, cmd) in seen or cmd in blocked.get(dev, set()):
                    continue
                seen.add((dev, cmd))
                steps.append({"device": dev, "command": cmd,
                              "reason": str(s.get("reason", ""))[:160]})
    if not steps:
        engine = "fallback"
        error = res.get("error", "") or "AI 未给出可用计划"
        # 兜底只发「不需要参数」的命令 —— 需要参数的没法凭空填
        safe_map = {c["command"].lower(): c["command"] for c in
                    (catalog[k] for k in sorted(catalog))
                    if kb.runnable_command(c)}
        safe = [safe_map[k] for k in sorted(safe_map)]
        steps = [{"device": d, "command": cmd, "reason": "兜底全量采集"}
                 for d in sorted(devices) for cmd in safe
                 if cmd not in blocked.get(d, set())]

    steps.sort(key=lambda s: (s["device"], s["command"]))
    steps, dropped = _cap(steps)
    plan_hash = sha256_of([{"device": s["device"], "command": s["command"]}
                           for s in steps] + [PLAN_VERSION])
    return {"steps": steps, "plan_hash": plan_hash, "engine": engine,
            "cached": res.get("cached", False), "dropped": dropped, "error": error}


def _cap(steps: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """硬上限兜底。截断在全序排序**之后**做，所以结果依然确定。"""
    per: Dict[str, int] = {}
    kept: List[Dict[str, Any]] = []
    for s in steps:
        if len(kept) >= MAX_TOTAL:
            break
        n = per.get(s["device"], 0)
        if n >= MAX_PER_DEVICE:
            continue
        per[s["device"]] = n + 1
        kept.append(s)
    return kept, len(steps) - len(kept)
