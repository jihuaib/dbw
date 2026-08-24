"""设备与拓扑表。"""
from __future__ import annotations

from ...core.db import register_column, register_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'LEAF',
    protocol TEXT NOT NULL DEFAULT 'ssh',
    host TEXT NOT NULL DEFAULT '',
    port INTEGER NOT NULL DEFAULT 22,
    username TEXT NOT NULL DEFAULT '',
    password TEXT NOT NULL DEFAULT '',
    enable_password TEXT NOT NULL DEFAULT '',
    vendor TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    pager_cmd TEXT NOT NULL DEFAULT '',
    lldp_cmd TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '',
    last_checked TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_device_role ON device(role, name);

-- 手册说有，这台设备未必有。命令能力按设备记录，采集时自动学习。
CREATE TABLE IF NOT EXISTS device_command (
    device TEXT NOT NULL,
    command TEXT NOT NULL,
    supported INTEGER NOT NULL DEFAULT 1,
    reason TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL,
    PRIMARY KEY (device, command)
);

CREATE TABLE IF NOT EXISTS topo_link (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_device TEXT NOT NULL,
    local_port TEXT NOT NULL,
    remote_device TEXT NOT NULL,
    remote_port TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    discovered_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_link_unique
    ON topo_link(local_device, local_port, remote_device, remote_port);

CREATE TABLE IF NOT EXISTS topo_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'lldp',
    devices INTEGER NOT NULL DEFAULT 0,
    links INTEGER NOT NULL DEFAULT 0,
    confirmed INTEGER NOT NULL DEFAULT 0,
    engine TEXT NOT NULL DEFAULT '',
    topo_hash TEXT NOT NULL DEFAULT '',
    log TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
"""
register_schema(SCHEMA)

# 上报配置属于设备：目标地址、每台设备独立的 syslog / trap 接收端口，
# 以及按厂商预设生成、可手改的两条下发命令模板（{host} / {port} 占位）。
for _col, _decl in (
    ("report_host", "TEXT NOT NULL DEFAULT ''"),
    ("syslog_port", "INTEGER NOT NULL DEFAULT 0"),
    ("trap_port", "INTEGER NOT NULL DEFAULT 0"),
    ("syslog_cmd", "TEXT NOT NULL DEFAULT ''"),
    ("trap_cmd", "TEXT NOT NULL DEFAULT ''"),
):
    register_column("device", _col, _decl)
