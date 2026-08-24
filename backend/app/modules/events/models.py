"""事件存储：syslog 与 SNMP trap 统一一张表 —— 都是「设备主动上报的状态变化」。"""
from ...core.db import register_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                       -- syslog / trap
    source_ip TEXT NOT NULL DEFAULT '',
    device TEXT NOT NULL DEFAULT '',          -- 映射出的设备名，映射不上为空
    severity TEXT NOT NULL DEFAULT '',        -- syslog: debug..error
    module TEXT NOT NULL DEFAULT '',          -- syslog: 上报模块（如 ospf / dev）
    event TEXT NOT NULL DEFAULT '',           -- syslog: 事件名 / trap: 符号名
    message TEXT NOT NULL DEFAULT '',
    trap_oid TEXT NOT NULL DEFAULT '',
    varbinds TEXT NOT NULL DEFAULT '[]',
    raw TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_kind ON event(kind, id);
CREATE INDEX IF NOT EXISTS idx_event_device ON event(device, id);

-- 源 IP → 设备名 映射（trap/syslog 报文里没有主机名，只能按源地址归属）
CREATE TABLE IF NOT EXISTS event_source (
    source_ip TEXT PRIMARY KEY,
    device TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',          -- auto-docker / manual / host-match
    created_at TEXT NOT NULL
);
"""
register_schema(SCHEMA)
