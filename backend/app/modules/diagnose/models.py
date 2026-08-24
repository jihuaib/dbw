"""诊断表：会话 / 轮次 / 冻结答案。"""
from __future__ import annotations

from ...core.db import register_column, register_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '新会话',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turn (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    question TEXT NOT NULL,
    epoch_id INTEGER,
    plan TEXT NOT NULL DEFAULT '[]',
    plan_hash TEXT NOT NULL DEFAULT '',
    plan_engine TEXT NOT NULL DEFAULT '',
    snapshot_hash TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT '{}',
    prompt_system TEXT NOT NULL DEFAULT '',
    prompt_user TEXT NOT NULL DEFAULT '',
    model_raw TEXT NOT NULL DEFAULT '',
    model_meta TEXT NOT NULL DEFAULT '{}',
    rounds TEXT NOT NULL DEFAULT '[]',
    fallback_level TEXT NOT NULL DEFAULT '',
    trace TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_session ON turn(session_id, seq);

-- 冻结答案：一致性兜底的第一级。指纹命中即原样返回，零模型调用。
CREATE TABLE IF NOT EXISTS frozen_answer (
    fingerprint TEXT PRIMARY KEY,
    question_norm TEXT NOT NULL DEFAULT '',
    snapshot_hash TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_frozen_snap ON frozen_answer(snapshot_hash);
"""
register_schema(SCHEMA)
for _col, _decl in (
    ("prompt_system", "TEXT NOT NULL DEFAULT ''"),
    ("prompt_user", "TEXT NOT NULL DEFAULT ''"),
    ("model_raw", "TEXT NOT NULL DEFAULT ''"),
    ("model_meta", "TEXT NOT NULL DEFAULT '{}'"),
    ("rounds", "TEXT NOT NULL DEFAULT '[]'"),
):
    register_column("turn", _col, _decl)
