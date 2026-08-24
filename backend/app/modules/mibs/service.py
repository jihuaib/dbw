"""MIB 管理：源文件导入 → pysmi 编译 → OID 索引 / 树 → trap 解码。

面向真实设备：没有任何写死的 MIB 清单。
  · 源文件两处：仓库自带的标准 MIB（backend/mibs/src，只读示例）
    + 用户上传目录（data/mibs/src，同名覆盖自带）。换厂商就上传厂商 MIB。
  · 编译对象 = 两处目录里的**全部**文件，依赖由 pysmi 按 IMPORTS 自动解析；
    单个 MIB 失败不拖累其它（真实设备的 MIB 集合常常不完整）。
  · 产物是 pysmi 的 JSON 符号表：每个符号带 oid / class / nodetype / syntax /
    描述 —— 树形浏览与 trap 符号化解码都从这一份索引来。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ...core.config import BASE_DIR, DATA_DIR
from ...core.db import execute, query

BUNDLED_DIR = BASE_DIR / "mibs" / "src"
USER_DIR = DATA_DIR / "mibs" / "src"
INDEX_FILE = DATA_DIR / "mibs" / "index.json"
MIB_EXTS = (".mib", ".txt", ".my", ".smi")

MODULE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9\-]*)\s+DEFINITIONS\s*::=\s*BEGIN", re.M)

_INDEX: Optional[Dict[str, Any]] = None       # 进程内缓存：oids + children


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ── 源文件 ───────────────────────────────────────────────────────────
def _module_name(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(20000)
        m = MODULE_RE.search(head)
        return m.group(1) if m else os.path.splitext(os.path.basename(path))[0]
    except OSError:
        return os.path.splitext(os.path.basename(path))[0]


def sources() -> List[Dict[str, Any]]:
    """两处目录合并；用户目录同名覆盖自带。"""
    seen: Dict[str, Dict[str, Any]] = {}
    for origin, d in (("bundled", BUNDLED_DIR), ("user", USER_DIR)):
        if not d.exists():
            continue
        for f in sorted(os.listdir(str(d))):
            if not f.lower().endswith(MIB_EXTS) or f.startswith("."):
                continue
            path = str(d / f)
            seen[f] = {"file": f, "module": _module_name(path), "origin": origin,
                       "bytes": os.path.getsize(path)}
    return sorted(seen.values(), key=lambda x: x["module"])


def upload(filename: str, data: bytes) -> Dict[str, Any]:
    name = os.path.basename(filename)
    if not name.lower().endswith(MIB_EXTS):
        raise ValueError("只接受 .mib / .txt / .my / .smi 文件")
    text = data.decode("utf-8", errors="replace")
    if "DEFINITIONS" not in text or "BEGIN" not in text:
        raise ValueError("不是 MIB 源文件（缺少 DEFINITIONS ::= BEGIN）")
    os.makedirs(str(USER_DIR), exist_ok=True)
    with open(str(USER_DIR / name), "w", encoding="utf-8") as fh:
        fh.write(text)
    return {"file": name, "module": _module_name(str(USER_DIR / name))}


def delete_source(filename: str) -> None:
    path = USER_DIR / os.path.basename(filename)
    if path.exists():
        path.unlink()


# ── 编译 ─────────────────────────────────────────────────────────────
def compile_all() -> Dict[str, Any]:
    """pysmi 编译全部源文件（JSON 产物），构建 OID 索引与树。"""
    from pysmi.codegen import JsonCodeGen
    from pysmi.compiler import MibCompiler
    from pysmi.parser import SmiStarParser
    from pysmi.reader import FileReader
    from pysmi.writer import CallbackWriter

    produced: Dict[str, Dict[str, Any]] = {}

    def sink(mibname, data, _ctx):
        produced[mibname] = json.loads(data)

    compiler = MibCompiler(SmiStarParser(), JsonCodeGen(), CallbackWriter(sink))
    # 用户目录在前：同名模块优先取用户上传的
    if USER_DIR.exists():
        compiler.addSources(FileReader(str(USER_DIR)))
    compiler.addSources(FileReader(str(BUNDLED_DIR)))

    srcs = sources()
    names = [s["module"] for s in srcs]
    status = compiler.compile(*names, ignoreErrors=True) if names else {}

    oids: Dict[str, Dict[str, Any]] = {}
    per_module: List[Dict[str, Any]] = []
    for s in srcs:
        st = status.get(s["module"])
        st_text = str(st) if st is not None else "missing"
        err = str(getattr(st, "error", "") or "") if st is not None else ""
        symbols = produced.get(s["module"], {})
        count = 0
        for sym, d in symbols.items():
            if not isinstance(d, dict) or not d.get("oid"):
                continue
            oid = str(d["oid"])
            entry = {"name": sym, "module": s["module"],
                     "class": d.get("class", ""), "nodetype": d.get("nodetype", ""),
                     "access": d.get("maxaccess", ""), "status": d.get("status", "")}
            syn = d.get("syntax")
            if isinstance(syn, dict):
                entry["syntax"] = syn.get("type", "")
            desc = d.get("description")
            if desc:
                entry["description"] = str(desc)[:600]
            objs = d.get("objects")
            if objs:
                entry["objects"] = [o.get("object", "") for o in objs
                                    if isinstance(o, dict)]
            # 用户覆盖优先：同一 OID 后写的（bundled 在后）不覆盖已有
            if oid not in oids or s["origin"] == "user":
                oids[oid] = entry
            count += 1
        per_module.append({"module": s["module"], "file": s["file"],
                           "origin": s["origin"], "status": st_text,
                           "error": err[:300], "symbols": count})

    summary = {"ok": all(m["status"] == "compiled" for m in per_module),
               "compiled": sum(1 for m in per_module if m["status"] == "compiled"),
               "total": len(per_module), "oid_count": len(oids),
               "compiled_at": _now(), "modules": per_module}

    os.makedirs(str(INDEX_FILE.parent), exist_ok=True)
    with open(str(INDEX_FILE), "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "oids": oids}, fh, ensure_ascii=False)

    execute("DELETE FROM mib_module")
    for m in per_module:
        execute("INSERT INTO mib_module(module, file, origin, status, error,"
                " symbols, compiled_at) VALUES (?,?,?,?,?,?,?)",
                (m["module"], m["file"], m["origin"], m["status"], m["error"],
                 m["symbols"], summary["compiled_at"]))
    global _INDEX
    _INDEX = None
    return summary


def status() -> Dict[str, Any]:
    if not INDEX_FILE.exists():
        return {"ok": False, "compiled": 0, "total": len(sources()),
                "oid_count": 0, "compiled_at": "", "modules": [],
                "note": "尚未编译"}
    try:
        with open(str(INDEX_FILE), encoding="utf-8") as fh:
            return json.load(fh)["summary"]
    except Exception as exc:
        return {"ok": False, "compiled": 0, "total": 0, "oid_count": 0,
                "compiled_at": "", "modules": [], "note": "索引损坏: {0}".format(exc)}


# ── 索引：解码与树 ────────────────────────────────────────────────────
def _parts(oid: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in oid.strip(". ").split(".") if x)


def _load_index() -> Dict[str, Any]:
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    oids: Dict[str, Dict[str, Any]] = {}
    if INDEX_FILE.exists():
        try:
            with open(str(INDEX_FILE), encoding="utf-8") as fh:
                oids = json.load(fh).get("oids", {})
        except Exception:
            oids = {}
    # 父子索引：父 = 最长的、且在索引里的前缀
    children: Dict[str, List[str]] = {}
    keys = sorted(oids, key=_parts)
    for oid in keys:
        p = _parts(oid)
        parent = ""
        for cut in range(len(p) - 1, 0, -1):
            cand = ".".join(str(x) for x in p[:cut])
            if cand in oids:
                parent = cand
                break
        children.setdefault(parent, []).append(oid)
    _INDEX = {"oids": oids, "children": children}
    return _INDEX


def translate(oid: str) -> str:
    """数字 OID → 模块::符号名.实例（最长前缀匹配）。没命中原样返回。"""
    oids = _load_index()["oids"]
    try:
        p = _parts(oid)
    except ValueError:
        return oid
    for cut in range(len(p), 0, -1):
        hit = oids.get(".".join(str(x) for x in p[:cut]))
        if hit:
            tail = ".".join(str(x) for x in p[cut:])
            return "{0}::{1}{2}".format(hit["module"], hit["name"],
                                        "." + tail if tail else "")
    return oid


def lookup(oid: str) -> Optional[Dict[str, Any]]:
    entry = _load_index()["oids"].get(oid.strip(". "))
    return dict(entry, oid=oid.strip(". ")) if entry else None


def tree_children(parent: str = "") -> List[Dict[str, Any]]:
    """树的一层：parent 为空取根。每个节点带是否有子节点，供懒加载。"""
    idx = _load_index()
    out = []
    for oid in sorted(idx["children"].get(parent, ""), key=_parts):
        e = idx["oids"][oid]
        out.append({"oid": oid, "name": e["name"], "module": e["module"],
                    "class": e.get("class", ""), "nodetype": e.get("nodetype", ""),
                    "syntax": e.get("syntax", ""), "access": e.get("access", ""),
                    "has_children": bool(idx["children"].get(oid))})
    return out


def search(text: str, limit: int = 50) -> List[Dict[str, Any]]:
    """按匹配度排序：名称精确 > 名称前缀 > 名称包含 > OID 包含；同级按 OID。"""
    q = text.strip().lower()
    if not q:
        return []
    idx = _load_index()
    scored = []
    for oid, e in idx["oids"].items():
        name = e["name"].lower()
        if name == q:
            rank = 0
        elif name.startswith(q):
            rank = 1
        elif q in name:
            rank = 2
        elif q in oid:
            rank = 3
        else:
            continue
        scored.append((rank, _parts(oid), oid, e))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [{"oid": oid, "name": e["name"], "module": e["module"],
             "class": e.get("class", "")} for _r, _p, oid, e in scored[:limit]]


def modules() -> List[Dict[str, Any]]:
    return query("SELECT * FROM mib_module ORDER BY module")
