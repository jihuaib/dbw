"""归一化 —— 整个方案里唯一非显然的技术点。

要解决的是最致命、也最被忽视的一类不一致：**数据本身在变**。
真机上两次采集之间，计数器在涨、uptime 在走、老化在倒数。原始回显永远不可能
逐字相同 —— 送进 AI 的 prompt 字节就永远不同 —— 缓存永远不命中 —— 答案永远不一致。
模型再确定也救不了。

做法刻意选了**在文本层擦除易变量**，而不是解析成结构化事实：
  · AI 本来就很会读 CLI 原始回显，保留原貌比拆成字段更利于诊断
  · 不需要为每条命令维护解析器，加一条命令的成本是 0

规则表在下面，逐条可评审，整体版本化为 NORMALIZE_VERSION。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# ── 易变量擦除规则：(名称, 正则, 替换) ──────────────────────────────────
# 替换成占位符而不是删掉整行 —— AI 需要知道「这里有个值，只是被擦了」
RULES: List[Tuple[str, "re.Pattern[str]", str]] = [
    ("uptime",
     re.compile(r"(uptime is )\s*[\d]+ weeks?,\s*[\d]+ days?,\s*[\d]+ hours?,\s*[\d]+ minutes?",
                re.I), r"\1<ELIDED:uptime>"),
    ("时间戳",
     re.compile(r"\b\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}\b"), "<ELIDED:timestamp>"),
    ("Up/Down 计时",
     re.compile(r"(?<=\s)\d{2}:\d{2}:\d{2}(?=\s)"), "<ELIDED:duration>"),
    ("剩余/存活秒数",
     re.compile(r"\b\d+ sec(?:onds?)? (?:remaining|age)\b"), "<ELIDED:secs>"),
    ("ARP/MAC 老化剩余秒",
     re.compile(r"(?im)^(\s*(?:\d{1,3}\.){3}\d{1,3}\s+\S+\s+\S+\s+\S+\s+)\d+(\s+\S+\s*)$"),
     r"\1<ELIDED:aging>\2"),
    ("OSPF Dead-Time",
     re.compile(r"(?im)^((?:\d{1,3}\.){3}\d{1,3}\s+(?:\d{1,3}\.){3}\d{1,3}\s+\d+\s+)\d+(\s+)"),
     r"\1<ELIDED:deadtime>\2"),
    ("BGP 报文计数",
     re.compile(r"(?im)^((?:\d{1,3}\.){3}\d{1,3}\s+\d+\s+)\d+(\s+)\d+(\s+)"),
     r"\1<ELIDED:msgrcvd>\2<ELIDED:msgsent>\3"),
    ("光模块温度",
     re.compile(r"(?im)^(\s*Temperature[^:]*:\s*)[-\d.]+\s*$"), r"\1<ELIDED:temp>"),
]

# 数值分档：连续量必须离散化，否则一点点抖动就换一个哈希
BUCKETS: List[Tuple[str, "re.Pattern[str]", Any]] = [
    ("光功率", re.compile(r"(?im)^(\s*(?:RX|TX) power[^:]*:\s*)([-\d.]+)\s*$"),
     lambda v: ("CRITICAL_LOW" if v <= -30 else "LOW" if v <= -20
                else "NORMAL" if v <= -1 else "HIGH")),
    ("光模块电压", re.compile(r"(?im)^(\s*Voltage[^:]*:\s*)([-\d.]+)\s*$"),
     lambda v: "LOW" if v < 3.0 else "NORMAL" if v <= 3.6 else "HIGH"),
    ("偏置电流", re.compile(r"(?im)^(\s*Bias[^:]*:\s*)([-\d.]+)\s*$"),
     lambda v: "LOW" if v < 1.0 else "NORMAL" if v <= 60 else "HIGH"),
]

# ── 按表头识别易变列（格式无关）──────────────────────────────────────
#
# 逐条写正则去擦 TTL / Dead / Age 是不可持续的：换个厂商、换个命令，列序就变了。
# 这里改成结构化做法：找到表头行 → 按「整列皆空白」定出列边界 →
# 表头名命中易变词的那一列，整列值替换掉。
#
# 这样新命令、新厂商的倒计时列自动被覆盖，不用再补规则。
VOLATILE_HEADERS = [
    "ttl", "dead", "age", "aging", "uptime", "up/down", "updown", "expire",
    "expires", "hold", "holdtime", "last", "elapsed", "duration", "since",
    "msgrcvd", "msgsent", "outq", "time",
]
HEADER_TOKEN_OK = re.compile(r"^[A-Za-z][A-Za-z0-9()/_.\-]*$")
SEPARATOR_LINE = re.compile(r"^[\s\-=+|]+$")


def _column_bounds(block):
    width = max(len(l) for l in block)
    padded = [l.ljust(width) for l in block]
    is_sep = [all(row[i] == " " for row in padded) for i in range(width)]
    bounds, start = [], None
    for i in range(width):
        if not is_sep[i] and start is None:
            start = i
        elif is_sep[i] and start is not None:
            bounds.append((start, i))
            start = None
    if start is not None:
        bounds.append((start, width))
    return bounds


def _is_volatile_header(cell: str) -> bool:
    low = re.sub(r"[^a-z/]", "", cell.strip().lower())
    return any(low == v.replace("(s)", "") or low.startswith(v) for v in VOLATILE_HEADERS)


def elide_volatile_columns(text: str) -> "tuple":
    """按表头把易变列整列擦掉。返回 (文本, 命中的列名)。"""
    lines = text.split("\n")
    hits = []
    for i, line in enumerate(lines):
        tokens = line.split()
        if len(tokens) < 2 or not all(HEADER_TOKEN_OK.match(t) for t in tokens):
            continue
        # 收集表头之后的连续数据行（跳过 ---- 分隔线）
        block, idxs = [line], []
        j = i + 1
        while j < len(lines) and lines[j].strip():
            if SEPARATOR_LINE.match(lines[j]):
                j += 1
                continue
            block.append(lines[j])
            idxs.append(j)
            j += 1
        if len(block) < 2:
            continue
        bounds = _column_bounds(block)
        if len(bounds) < 2:
            continue
        header_cells = [line[a:b].strip() if a < len(line) else "" for a, b in bounds]
        targets = [k for k, c in enumerate(header_cells) if c and _is_volatile_header(c)]
        if not targets:
            continue
        for k in targets:
            hits.append(header_cells[k])
        for row_idx in idxs:
            row = lines[row_idx]
            out = list(row.ljust(max(b for _a, b in bounds)))
            for k in targets:
                a, b = bounds[k]
                if a >= len(row):
                    continue
                cell = row[a:b]
                if not cell.strip():
                    continue
                repl = "<ELIDED:{0}>".format(
                    re.sub(r"[^a-z]", "", header_cells[k].lower()) or "col")
                out[a:b] = list(repl.ljust(b - a))
            lines[row_idx] = "".join(out)
        break   # 一段回显通常只有一张表，处理完就够
    return "\n".join(lines), sorted(set(hits))


TRAILING_WS = re.compile(r"[ \t]+$", re.M)
BLANK_RUN = re.compile(r"\n{3,}")
# 列宽必须归一：aging 从 1180 掉到 999 会让后面的空格少一个，
# 光这一个空格就足以改变快照哈希。所以最后统一把 2+ 空格压成 2 个。
COLUMN_WS = re.compile(r"[ \t]{2,}")


def normalize_output(text: str, profile=None) -> Dict[str, Any]:
    """擦除易变量 + 数值分档 + 空白规整。返回 {text, applied}。

    profile 是该命令实测标定出的易变 token 位置，作为规则的兜底 ——
    规则漏掉的、没人想到的易变字段，靠它擦掉。
    """
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    applied: List[str] = []

    for name, pattern, repl in RULES:
        new = pattern.sub(repl, out)
        if new != out:
            applied.append(name)
        out = new

    for name, pattern, classify in BUCKETS:
        def _sub(m, _c=classify):
            try:
                return "{0}{1}".format(m.group(1), _c(float(m.group(2))))
            except ValueError:
                return m.group(0)
        new = pattern.sub(_sub, out)
        if new != out:
            applied.append(name)
        out = new

    if profile:
        out, prof_hits = apply_profile(out, profile)
        if prof_hits:
            applied.append("标定位置 ×{0}".format(len(prof_hits)))
    out, col_hits = elide_volatile_columns(out)
    for h in col_hits:
        applied.append("表头列 {0}".format(h))
    out = COLUMN_WS.sub("  ", out)
    out = TRAILING_WS.sub("", out)
    out = BLANK_RUN.sub("\n\n", out).strip("\n")
    if "列宽归一" not in applied:
        applied.append("列宽归一")
    return {"text": out, "applied": sorted(set(applied))}


def preview(text: str) -> Dict[str, Any]:
    """归一化前后对照 —— 用来在界面上说明「擦掉了什么」。"""
    res = normalize_output(text)
    return {"raw": text, "normalized": res["text"], "applied": res["applied"]}


# ── 易变位置标定（实测，而不是猜）────────────────────────────────────
#
# 逐条写正则去猜哪一列会变，永远追不上：换厂商、换命令、换版本，列序就变了。
# 上面那些规则只是快路径，真正兜底的是这里：
#
#   对同一条命令多采几次 → token 级比对 → **实际在变的位置**记为易变 → 冻结成 profile
#
# 之后每次采集都按 profile 擦除。测量代替猜测，格式无关、厂商无关。
# profile 变了意味着归一化口径变了，相关指纹自然失效 —— 这是对的。

def token_positions(text: str):
    """把文本切成 {(行号, token 序号): token}。"""
    out = {}
    for i, line in enumerate(text.split("\n")):
        for j, tok in enumerate(line.split()):
            out[(i, j)] = tok
    return out


def calibrate(samples):
    """多个样本 → 实际发生变化的 token 位置集合。

    只认「在不同样本间取值不同」的位置。行数或 token 数对不上的样本直接放弃标定 ——
    那说明内容本身变了，不是抖动。
    """
    if len(samples) < 2:
        return []
    maps = [token_positions(s) for s in samples]
    keys = set(maps[0])
    for m in maps[1:]:
        if set(m) != keys:
            return []          # 结构都变了，没法按位置标定
    volatile = [k for k in keys if len({m[k] for m in maps}) > 1]
    return sorted(volatile)


def apply_profile(text: str, positions):
    """按标定结果擦除易变 token。位置对不上就原样返回（结构变了＝真变化）。"""
    if not positions:
        return text, []
    lines = text.split("\n")
    want = {(int(a), int(b)) for a, b in positions}
    hit = []
    for i, line in enumerate(lines):
        toks = line.split()
        if not toks:
            continue
        changed = False
        for j in range(len(toks)):
            if (i, j) in want:
                toks[j] = "<ELIDED:auto>"
                changed = True
                hit.append("{0}:{1}".format(i, j))
        if changed:
            lines[i] = "  ".join(toks)
    return "\n".join(lines), hit
