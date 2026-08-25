"""资料导入：Word / Markdown / 纯文本 → 命令清单。

刻意做得很轻：知识库只需要回答一件事 ——
**「这台设备上有哪些只读命令可用，各自看什么」**。
命令回显长什么样、怎么解析成结构化字段，全部交给 AI；
我们不再自建解析器体系。

两条提取路径：
  rule —— 正则扫 display/show 命令行，零依赖、可复现
  ai   —— 让模型通读资料后给出命令清单（结果按文档哈希缓存冻结）
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# 只收只读命令。写命令永不进清单 —— 诊断不该改设备状态。
READ_VERBS = ("display", "show", "dis")
DANGEROUS = ("reset", "undo", "reboot", "delete", "erase", "save", "shutdown",
             "format", "install", "upgrade", "restore", "copy", "rename")

CMD_RE = re.compile(
    r"^\s*(?:<[^>]{1,40}>|\[[^\]]{1,40}\])?\s*"
    r"((?:display|show|dis)\s+[a-z0-9][a-z0-9\-\s]{0,80}?)\s*$",
    re.I | re.M)
# 手册里命令行前面常见的「装饰」：标题井号、列表符、编号、标签（【命令】/命令格式：/语法：/Syntax:）
LINE_PREFIX_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|[-*•]\s+|\d+(?:\.\d+)*\.?\s+)?"
    r"(?:【[^】]{1,12}】|(?:命令格式|命令|语法|用法|格式|Syntax|Command|Usage)\s*[:：])?\s*")
# 厂商语法行：display/show 开头，允许 [ ] { } | < > 与大小写参数名，整行取到末尾
SYNTAX_LINE_RE = re.compile(
    r"^(?:<[^>]{1,40}>|\[[^\]]{1,40}\])?\s*"
    r"((?:display|show|dis)\s+[A-Za-z0-9][A-Za-z0-9\-_./:{}\[\]|<>\s,*]{0,160}?)\s*[。；;]?\s*$")
# H3C/华为风格的必填参数不带 <>，靠命名习惯识别：vlan-id / interface-number / ip-address …
PARAM_WORD_RE = re.compile(
    r"^[a-z]+(?:-[a-z]+)*-(?:id|number|name|type|address|list|value|index|prefix|"
    r"mask|length|string|text|count|time|interval|ip|port)$")
PLACEHOLDER_RE = re.compile(r"[<\[]?\b([a-z]+(?:-[a-z]+)+)\b[>\]]?")
# Markdown 表格行： | `show lldp neighbors` | global | 显示 LLDP 邻居概要 |
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$", re.M)
BACKTICK_RE = re.compile(r"`([^`]+)`")
# 语法里的参数占位：<name> / [optional] / {a|b}
ANGLE_RE = re.compile(r"<([^<>|]+)>")
OPTIONAL_RE = re.compile(r"\[[^\[\]]*\]")
CHOICE_RE = re.compile(r"\{[^{}]*\}")


def read_text(filename: str, raw: bytes) -> str:
    """Word / Markdown / 纯文本 → 统一纯文本。提取必须确定性。"""
    low = filename.lower()
    if low.endswith(".docx"):
        from io import BytesIO
        import docx
        doc = docx.Document(BytesIO(raw))
        parts: List[str] = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append("\t".join(c.text.strip() for c in row.cells))
        return "\n".join(parts)
    if low.endswith(".doc"):
        raise ValueError("旧版 .doc 不支持，请另存为 .docx")
    return raw.decode("utf-8", errors="replace")


def _clean(cmd: str) -> str:
    """只规整空白，**保留大小写** —— 不少设备的接口名是区分大小写的，
    统一转小写会让命令直接被拒。"""
    return re.sub(r"\s+", " ", cmd.strip())


def _key(cmd: str) -> str:
    """去重与匹配用的小写键。发送时仍用原始大小写。"""
    return _clean(cmd).lower()


def _excluded_commands() -> set:
    """观测者自照镜子的命令（CLI 会话 / 审计类）：输出必然带着采集自身的痕迹，
    进了证据快照就永不一致。清单是设置项（按厂商增删），不写死在代码里。"""
    from ..settings import service as settings
    raw = settings.get("kb_exclude_commands") or ""
    return {_key(x) for x in raw.split(",") if x.strip()}


def _is_read_only(cmd: str) -> bool:
    tokens = _key(cmd).split()
    if not tokens or tokens[0] not in READ_VERBS:
        return False
    if _key(cmd) in _excluded_commands():
        return False
    return not any(d in tokens for d in DANGEROUS)


def split_syntax(syntax: str) -> Dict[str, Any]:
    """把语法串拆成「可下发基串 + 必需参数」。

    `[可选]` 和 `{选择}` 可以剥掉，但 **`<param>` 如果不在方括号里就是必需的** ——
    剥掉它得到的基串设备会回 "Incomplete command"。这类命令必须带参数才能下发。
    """
    line = syntax.strip()
    prev = None
    while prev != line:
        prev = line
        line = OPTIONAL_RE.sub(" ", line)   # 可选段整段去掉
        line = CHOICE_RE.sub(" ", line)     # 选择段暂不展开
    required: List[str] = ANGLE_RE.findall(line)
    # 基串 = 第一个必需参数之前的固定 token
    head: List[str] = []
    for tok in line.split():
        if tok.startswith("<") or tok.startswith("["):
            break
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-]*", tok):
            head.append(tok)
        else:
            break
    return {"base": " ".join(head),
            "required": [r.strip() for r in required if r.strip()]}


def base_of_syntax(syntax: str) -> str:
    return split_syntax(syntax)["base"]


def extract_by_markdown_table(text: str) -> List[Dict[str, Any]]:
    """解析 Markdown 表格式 CLI 文档。

    形如「| 命令 | 视图 | 说明 |」的 Markdown 表格手册：
        | 命令 | 视图 | 说明 |
        | `show lldp neighbors` | global | 显示 LLDP 邻居概要 |

    这类文档只给命令与说明、不给样例回显 —— 正好，回显怎么读交给 AI。
    """
    found: Dict[str, Dict[str, Any]] = {}
    section = ""
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip()
            continue
        m = TABLE_ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        marks = BACKTICK_RE.findall(cells[0])
        syntax = (marks[0] if marks else cells[0]).strip()
        if not syntax or syntax.startswith("命令"):
            continue
        parsed = split_syntax(syntax)
        base = parsed["base"]
        if not _is_read_only(base) or len(base.split()) < 2:
            continue
        view = cells[1] if len(cells) > 1 else ""
        purpose = cells[2] if len(cells) > 2 else ""
        params = sorted({p.strip() for p in ANGLE_RE.findall(syntax) if p.strip()})
        prev = found.get(_key(base))
        # 同一基串出现多次时，保留语法最短的那条 —— 参数最少、最容易下发
        if prev and len(prev["syntax"]) <= len(syntax):
            continue
        found[_key(base)] = {
            "command": base,
            "syntax": syntax,
            "required": parsed["required"],
            "purpose": (purpose or section or "")[:200],
            "keywords": sorted({t.lower() for t in base.split()
                                if t.lower() not in READ_VERBS}
                               | ({view.lower()} if view else set())),
            "params": params,
            "sample": "",
            "read_only": True,
        }
    return [found[k] for k in sorted(found)]


def looks_like_table_doc(text: str) -> bool:
    rows = TABLE_ROW_RE.findall(text)
    return len(rows) >= 5 and text.count("`") >= 10


def extract_by_inline(text: str) -> List[Dict[str, Any]]:
    """提取行内反引号里的命令 —— 覆盖「标题/列表」式文档。

    形如「### `命令`」标题 + 「- **用法**：`命令`」列表的手册：
        ### 2.3 `show current-configuration`
        - **用法**：`show current-configuration`

    这类文档不给表格也不给裸命令行，命令只出现在 `反引号` 里。
    漏掉它的代价在实测中出现过：`show current-configuration` 没进清单，
    闭集校验让 Agent 无权采配置，shutdown 的接口只能诊断成「物理链路未建立」。
    """
    found: Dict[str, Dict[str, Any]] = {}
    lines = text.replace("\r\n", "\n").split("\n")
    section = ""
    desc = ""
    await_desc: List[str] = []   # 等着用标题下第一行正文回填 purpose 的命令
    for line in lines:
        stripped = line.strip()
        is_heading = stripped.startswith("#")
        if is_heading:
            section = re.sub(r"^[#\d\.\s]+", "", stripped).strip()
            desc = ""
            await_desc = []
        elif stripped and not stripped.startswith(("-", "|", "`", ">")):
            # 标题后的第一行正文当作该节命令的用途说明
            if not desc:
                desc = stripped[:120]
                for k in await_desc:
                    if k in found and not found[k]["purpose"]:
                        found[k]["purpose"] = desc
                await_desc = []
        for mark in BACKTICK_RE.findall(line):
            syntax = _clean(mark)
            parsed = split_syntax(syntax)
            base = parsed["base"]
            if not _is_read_only(base) or len(base.split()) < 2:
                continue
            prev = found.get(_key(base))
            # 同一基串多次出现，保留语法最短的那条 —— 参数最少、最容易下发
            if prev and len(prev["syntax"]) <= len(syntax):
                continue
            purpose = desc or BACKTICK_RE.sub(
                lambda m: m.group(1), section).strip()[:120]
            if _key(purpose) == _key(base):
                purpose = ""
            found[_key(base)] = {
                "command": base,
                "syntax": syntax,
                "required": parsed["required"],
                "purpose": purpose,
                "keywords": sorted(set(t for t in base.split()
                                       if t not in READ_VERBS)),
                "params": sorted({x.strip() for x in ANGLE_RE.findall(syntax)
                                  if x.strip()}),
                "sample": "",
                "read_only": True,
            }
            if is_heading and not purpose:
                await_desc.append(_key(base))
    return [found[k] for k in sorted(found)]


def _normalize_syntax_line(line: str) -> str:
    """去掉标题/列表/编号/标签装饰，留下可能是命令的正文。"""
    return LINE_PREFIX_RE.sub("", line, count=1).strip()


def _mark_bare_params(syntax: str) -> str:
    """把不带 <> 的必填参数（按命名习惯识别）包成 <param>，交给 split_syntax。"""
    out = []
    for i, tok in enumerate(syntax.split()):
        bare = tok.strip("[]{}|,")
        if i >= 1 and PARAM_WORD_RE.match(bare) and "<" not in tok:
            tok = tok.replace(bare, "<" + bare + ">")
        out.append(tok)
    return " ".join(out)


def extract_by_rule(text: str) -> List[Dict[str, Any]]:
    """正则提取：示例行 / 标题行 / 语法行 / 代码块 / 行内反引号，多路合并。

    覆盖的写法（各厂商手册常见）：
      <Sysname> display ospf peer                      示例行（带提示符）
      # display ospf peer  /  ## 1.2 display interface  标题就是命令
      【命令】 display vlan vlan-id                     标签 + 命令
      display ospf [ process-id ] peer [ verbose ]      「命令格式」段的语法行
      ```display ip routing-table [ verbose ]```        代码块
    同一命令多次出现只留一条（保留样例回显最长的），按命令串排序 —— 结果确定。
    """
    found: Dict[str, Dict[str, Any]] = {}
    lines = text.replace("\r\n", "\n").split("\n")
    for idx, line in enumerate(lines):
        body = _normalize_syntax_line(line)
        if not body or body.startswith("|"):
            continue
        m = SYNTAX_LINE_RE.match(body)
        if not m:
            continue
        syntax = _clean(m.group(1))
        parsed = split_syntax(_mark_bare_params(syntax))
        cmd = parsed["base"]
        if not _is_read_only(cmd) or len(cmd.split()) < 2:
            continue
        # 命令行之后连续的非空行当作样例回显（示例行才有；语法行后面通常是说明）
        sample: List[str] = []
        if line.strip().startswith("<") or line.strip().startswith("["):
            j = idx + 1
            while j < len(lines) and lines[j].strip() and not SYNTAX_LINE_RE.match(
                    _normalize_syntax_line(lines[j])):
                sample.append(lines[j])
                if len(sample) >= 24:
                    break
                j += 1
        # 命令行之前最近的非空行当作用途说明
        purpose = ""
        k = idx - 1
        while k >= 0 and k > idx - 6:
            prev = lines[k].strip()
            if prev and not SYNTAX_LINE_RE.match(_normalize_syntax_line(prev)) \
                    and not prev.startswith("```"):
                purpose = LINE_PREFIX_RE.sub("", prev, count=1).strip()[:120]
                break
            k -= 1
        prev_entry = found.get(_key(cmd))
        if prev_entry:
            # 已有：样例更长的替换；语法更完整（含参数）的补上
            if len("\n".join(sample)) > len(prev_entry["sample"]):
                prev_entry["sample"] = "\n".join(sample)
            if len(syntax) > len(prev_entry["syntax"]) and parsed["required"] and not prev_entry["required"]:
                prev_entry.update(syntax=syntax, required=parsed["required"],
                                  params=sorted(set(parsed["required"])))
            continue
        found[_key(cmd)] = {
            "command": cmd,
            "syntax": syntax,
            "required": parsed["required"],
            "purpose": purpose,
            "keywords": sorted(set(t for t in cmd.split() if t not in READ_VERBS)),
            "params": sorted(set(parsed["required"])),
            "sample": "\n".join(sample),
            "read_only": True,
        }
    for c in extract_by_inline(text):
        if _key(c["command"]) not in found:   # 裸命令行带样例回显，优先
            found[_key(c["command"])] = c
    return [found[k] for k in sorted(found)]


AI_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "commands": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "完整只读命令，小写，不含设备提示符"},
                    "purpose": {"type": "string", "description": "一句话说明它看什么"},
                    "keywords": {"type": "array", "items": {"type": "string"},
                                 "description": "该命令相关的中英文检索词"},
                    "params": {"type": "array", "items": {"type": "string"},
                               "description": "可变参数名，如 ip-address / interface"},
                },
                "required": ["command", "purpose", "keywords", "params"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["commands"],
    "additionalProperties": False,
}

AI_SYSTEM = (
    "你是网络设备 CLI 资料的整理助手。从给定资料中列出所有**只读诊断命令**"
    "（display / show 开头），忽略任何会改变设备状态的命令。"
    "只依据原文，不得臆造命令。keywords 要包含中文口语说法（如「路由表」「邻居」「光模块」），"
    "方便后续按用户提问检索。"
)


def extract_by_ai(text: str) -> Dict[str, Any]:
    from ...core import llm
    body = text if len(text) <= 60000 else text[:60000]
    res = llm.call_json("kb.extract", AI_SYSTEM, "资料原文：\n\n" + body, AI_SCHEMA)
    if not res["ok"]:
        return {"ok": False, "commands": [], "error": res["error"]}
    out: List[Dict[str, Any]] = []
    for c in res["data"].get("commands", []):
        cmd = _clean(str(c.get("command", "")))
        if not _is_read_only(cmd):
            continue
        out.append({
            "command": cmd,
            "syntax": str(c.get("command", "")),
            "required": [],
            "purpose": str(c.get("purpose", ""))[:200],
            "keywords": sorted({str(k).lower() for k in c.get("keywords", []) if k}),
            "params": sorted({str(p) for p in c.get("params", []) if p}),
            "sample": "",
            "read_only": True,
        })
    uniq = {_key(c["command"]): c for c in out}
    return {"ok": True, "commands": [uniq[k] for k in sorted(uniq)],
            "cached": res.get("cached", False), "error": ""}
