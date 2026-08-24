"""设置表：API key 与模型参数，页面可配。"""
from __future__ import annotations

from ...core.db import register_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""
register_schema(SCHEMA)
