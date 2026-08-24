"""采集表：纪元 + 原始/归一化回显 + 证据快照。"""
from __future__ import annotations

from ...core.db import register_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS epoch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    devices TEXT NOT NULL DEFAULT '[]',
    plan TEXT NOT NULL DEFAULT '[]',
    plan_hash TEXT NOT NULL DEFAULT '',
    plan_engine TEXT NOT NULL DEFAULT 'ai',
    snapshot TEXT NOT NULL DEFAULT '',
    snapshot_hash TEXT NOT NULL DEFAULT '',
    ok_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- 实测标定出的易变 token 位置。测量代替猜测。
CREATE TABLE IF NOT EXISTS volatility_profile (
    device TEXT NOT NULL,
    command TEXT NOT NULL,
    positions TEXT NOT NULL DEFAULT '[]',
    samples INTEGER NOT NULL DEFAULT 0,
    calibrated_at TEXT NOT NULL,
    PRIMARY KEY (device, command)
);

CREATE TABLE IF NOT EXISTS capture (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch_id INTEGER NOT NULL,
    device TEXT NOT NULL,
    command TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 1,
    error TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    raw_sha TEXT NOT NULL DEFAULT '',
    norm_text TEXT NOT NULL DEFAULT '',
    norm_sha TEXT NOT NULL DEFAULT '',
    applied TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cap_epoch ON capture(epoch_id, device, command);
"""
register_schema(SCHEMA)
