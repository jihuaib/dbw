"""MIB 编译结果（每个模块一行）。索引本体在 data/mibs/index.json。"""
from ...core.db import register_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS mib_module (
    module TEXT PRIMARY KEY,
    file TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',        -- bundled / user
    status TEXT NOT NULL DEFAULT '',        -- compiled / failed / missing …
    error TEXT NOT NULL DEFAULT '',
    symbols INTEGER NOT NULL DEFAULT 0,
    compiled_at TEXT NOT NULL
);
"""
register_schema(SCHEMA)
