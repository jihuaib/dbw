"""知识库业务层。"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

from ...core.canon import sha256_of, short
from ...core.config import UPLOAD_DIR
from ...core.db import execute, loads, query, query_one
from . import importer
from . import models  # noqa: F401  建表注册


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def import_doc(name: str, raw: bytes, engine: str = "rule") -> Dict[str, Any]:
    text = importer.read_text(name, raw)
    if not text.strip():
        raise ValueError("未能从文件中提取到文本")
    digest = sha256_of(raw)
    path = os.path.join(str(UPLOAD_DIR), "doc-{0}.txt".format(short(digest, 16)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)

    kind = "docx" if name.lower().endswith(".docx") else (
        "md" if name.lower().endswith((".md", ".markdown")) else "txt")
    doc_id = execute(
        "INSERT INTO kb_doc(name, kind, sha256, chars, text_path, engine, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (name, kind, digest, len(text), path, engine, _now()))

    warn = ""
    table_doc = importer.looks_like_table_doc(text)
    if engine == "auto":
        engine = "table" if table_doc else "rule"
        execute("UPDATE kb_doc SET engine=? WHERE id=?", (engine, doc_id))
    if engine == "table":
        commands = importer.extract_by_markdown_table(text)
        if not commands:
            commands = importer.extract_by_rule(text)
            engine = "rule"
            warn = "未从表格中提取到命令，已回退正则提取"
            execute("UPDATE kb_doc SET engine='rule' WHERE id=?", (doc_id,))
    elif engine == "ai":
        res = importer.extract_by_ai(text)
        if res["ok"]:
            commands = res["commands"]
        else:
            commands = importer.extract_by_rule(text)
            engine = "rule"
            warn = "AI 提取不可用（{0}），已降级为正则提取".format(res["error"])
            execute("UPDATE kb_doc SET engine='rule' WHERE id=?", (doc_id,))
    else:
        commands = importer.extract_by_rule(text)

    added = 0
    for c in commands:
        try:
            execute(
                "INSERT INTO kb_command(doc_id, command, syntax, required, purpose,"
                " keywords, params, sample, read_only, enabled, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,1,1,?)",
                (doc_id, c["command"], c.get("syntax", c["command"]),
                 json.dumps(c.get("required", []), ensure_ascii=False), c["purpose"],
                 json.dumps(c["keywords"], ensure_ascii=False),
                 json.dumps(c["params"], ensure_ascii=False), c["sample"], _now()))
            added += 1
        except Exception:
            # 命令全局唯一：同一条命令在多份资料里出现，只保留首次导入的那条
            continue
    return {"doc_id": doc_id, "kind": kind, "chars": len(text), "engine": engine,
            "found": len(commands), "added": added, "warn": warn}


def list_docs() -> List[Dict[str, Any]]:
    rows = query("SELECT * FROM kb_doc ORDER BY id DESC")
    for r in rows:
        r["sha256_short"] = short(r["sha256"])
        r["command_count"] = query_one(
            "SELECT COUNT(*) n FROM kb_command WHERE doc_id=?", (r["id"],))["n"]
    return rows


def delete_doc(doc_id: int) -> None:
    execute("DELETE FROM kb_command WHERE doc_id=?", (doc_id,))
    execute("DELETE FROM kb_doc WHERE id=?", (doc_id,))


def _cmd_row(r: Dict[str, Any]) -> Dict[str, Any]:
    r["keywords"] = loads(r["keywords"], [])
    r["params"] = loads(r["params"], [])
    r["required"] = loads(r.get("required"), [])
    r["enabled"] = bool(r["enabled"])
    return r


def list_commands(q: str = "", enabled_only: bool = False) -> List[Dict[str, Any]]:
    """命令清单按命令串字典序 —— 顺序确定，才能进指纹。"""
    sql = "SELECT * FROM kb_command WHERE 1=1"
    params: List[Any] = []
    if enabled_only:
        sql += " AND enabled=1"
    if q:
        sql += " AND (command LIKE ? OR purpose LIKE ? OR keywords LIKE ?)"
        params += ["%{0}%".format(q)] * 3
    sql += " ORDER BY command"
    return [_cmd_row(r) for r in query(sql, params)]


def set_enabled(cmd_id: int, enabled: bool) -> Dict[str, Any]:
    execute("UPDATE kb_command SET enabled=? WHERE id=?", (1 if enabled else 0, cmd_id))
    row = query_one("SELECT * FROM kb_command WHERE id=?", (cmd_id,))
    return _cmd_row(row) if row else {}


def catalog_digest() -> str:
    """命令清单指纹 —— 清单变了，诊断口径就变了，冻结答案必须失效。"""
    cmds = [{"command": c["command"], "params": c["params"]}
            for c in list_commands(enabled_only=True)]
    return sha256_of(cmds)


def summary() -> Dict[str, Any]:
    total = query_one("SELECT COUNT(*) n FROM kb_command")["n"]
    enabled = query_one("SELECT COUNT(*) n FROM kb_command WHERE enabled=1")["n"]
    docs = query_one("SELECT COUNT(*) n FROM kb_doc")["n"]
    return {"docs": docs, "commands": total, "enabled": enabled,
            "catalog_digest": catalog_digest()}
