"""知识库表：导入的资料 + 从中提取的命令清单。"""
from __future__ import annotations

from ...core.db import register_column, register_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_doc (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'md',
    sha256 TEXT NOT NULL,
    chars INTEGER NOT NULL DEFAULT 0,
    text_path TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT 'rule',
    parser_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_command (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    command TEXT NOT NULL,
    syntax TEXT NOT NULL DEFAULT '',
    required TEXT NOT NULL DEFAULT '[]',
    purpose TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    params TEXT NOT NULL DEFAULT '[]',
    sample TEXT NOT NULL DEFAULT '',
    read_only INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cmd_doc ON kb_command(doc_id);
DROP INDEX IF EXISTS idx_cmd_unique;
CREATE UNIQUE INDEX IF NOT EXISTS idx_cmd_variant_unique
    ON kb_command(command, syntax);
"""
register_schema(SCHEMA)
register_column("kb_doc", "parser_version", "TEXT NOT NULL DEFAULT ''")
