"""SQLite 存储。各模块自己注册建表语句，表结构归模块所有。"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional

from .config import DB_PATH

_local = threading.local()
_SCHEMAS: List[str] = []
_MIGRATIONS: List[tuple] = []


def register_schema(ddl: str) -> None:
    _SCHEMAS.append(ddl)


def register_column(table: str, column: str, decl: str) -> None:
    """表已存在时补列（CREATE IF NOT EXISTS 不会加新列）。幂等。"""
    _MIGRATIONS.append((table, column, decl))


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    for ddl in _SCHEMAS:
        conn.executescript(ddl)
    for table, column, decl in _MIGRATIONS:
        have = {r[1] for r in conn.execute(
            "PRAGMA table_info({0})".format(table)).fetchall()}
        if column not in have:
            conn.execute("ALTER TABLE {0} ADD COLUMN {1} {2}".format(
                table, column, decl))
    conn.commit()


def query(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in get_conn().execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    conn = get_conn()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur.lastrowid


def loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default
