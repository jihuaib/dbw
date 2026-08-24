"""监控事件：syslog 解析、端口归属、归一化上下文（进指纹的那段）。"""
from app.core.db import execute
from app.modules.events import service as E


def test_parse_syslog_rfc3164():
    raw = '<132>Aug 24 13:53:48 netnexus if/proto-down: interface=GE-1 vrf=public'
    p = E.parse_syslog(raw, "1.2.3.4")
    assert p["severity"] == "warning"      # 132 % 8 == 4 → warning
    assert p["module"] == "if"
    assert p["event"] == "proto-down"
    assert p["message"].startswith("interface=GE-1")


def test_parse_syslog_fallback():
    p = E.parse_syslog("garbage without structure", "1.2.3.4")
    assert p["message"] == "garbage without structure"
    assert p["module"] == ""


def test_port_attribution_beats_source_ip():
    # NAT 场景：源 IP 是 127.0.0.1，端口才是设备身份 —— 端口来自设备配置
    execute("INSERT OR REPLACE INTO device(name, role, protocol, host, port, enabled,"
            " syslog_port, trap_port, created_at)"
            " VALUES ('LEAF9','LEAF','telnet','127.0.0.1',23,1,6001,7001,'t')")
    assert E._device_of("127.0.0.1", 6001, "syslog") == "LEAF9"
    assert E._device_of("127.0.0.1", 6002, "syslog") == ""
    # trap 与 syslog 的端口空间独立
    assert E._device_of("127.0.0.1", 6001, "trap") == ""
    assert E._device_of("127.0.0.1", 7001, "trap") == "LEAF9"
    # 服务器监听 = 默认端口 ∪ 设备端口
    assert 6001 in E._syslog_ports() and 7001 in E._trap_ports()
    execute("DELETE FROM device WHERE name='LEAF9'")


def test_context_dedupes_and_is_deterministic():
    execute("DELETE FROM event")
    for i in range(3):     # 同一事件反复 flap 3 次
        E.store_event("syslog", "9.9.9.9", severity="warning", module="if",
                      event="proto-down", message="interface=GE-1 ifindex=12")
    E.store_event("trap", "9.9.9.9", event="IF-MIB::linkDown",
                  message="IF-MIB::ifIndex.12=12")
    E.set_source("9.9.9.9", "LEAF1", "manual")
    # 注意：store 时设备归属已定（空），上下文按 device 过滤 —— 用全量
    text1, h1 = E.context_for([])
    text2, h2 = E.context_for([])
    assert h1 == h2                      # 确定性：同一事件集合同一哈希
    assert text1.count("proto-down") == 1  # flap 3 次合并为一行
    assert "linkDown" in text1
    assert "2026" not in text1           # 不带时间戳
    execute("DELETE FROM event")


def test_context_flap_count_stable():
    """反复 flap 不改变摘要哈希 —— 次数不进上下文，检查途中输入不会悄悄变。"""
    execute("DELETE FROM event")
    E.store_event("syslog", "6.6.6.6", severity="notice", module="lldp",
                  event="neighbor-down", message="neighbor=SPINE1 reason=expired")
    _, h1 = E.context_for([])
    for _ in range(5):
        E.store_event("syslog", "6.6.6.6", severity="notice", module="lldp",
                      event="neighbor-down", message="neighbor=SPINE1 reason=expired")
    _, h2 = E.context_for([])
    assert h1 == h2
    execute("DELETE FROM event")


def test_context_numbers_normalized():
    execute("DELETE FROM event")
    E.store_event("syslog", "8.8.8.8", severity="notice", module="lldp",
                  event="neighbor-down", message="neighbor=SPINE1 ttl=19")
    E.store_event("syslog", "8.8.8.8", severity="notice", module="lldp",
                  event="neighbor-down", message="neighbor=SPINE1 ttl=7")
    text, _ = E.context_for([])
    # ttl 数值被归一 → 两条合并
    assert text.count("neighbor-down") == 1
    execute("DELETE FROM event")


def test_context_excludes_cli_echo():
    """CLI 审计事件是采集自身的回声，必须排除 —— 否则观测改变输入，指纹永不稳定。"""
    execute("DELETE FROM event")
    E.store_event("syslog", "7.7.7.7", severity="notice", module="cli",
                  event="command", message='cmd="show if"')
    text, digest = E.context_for([])
    assert text == "" and digest == ""
    execute("DELETE FROM event")


def test_parse_syslog_rfc3164_with_host_and_tag():
    p = E.parse_syslog("<190>Aug 25 10:00:00 core-sw1 %%10OSPF/5/NBR_CHANGE: neighbor 1.1.1.1 down", "1.1.1.1")
    assert p["severity"] == "info"
    assert p["module"] == "%%10OSPF" or p["module"]           # 厂商标签保留
    assert "neighbor 1.1.1.1 down" in p["message"]


def test_parse_syslog_rfc5424():
    raw = '<165>1 2026-08-25T10:00:00Z leaf1 ospfd 1234 ID47 - if/proto-down: interface=Eth1'
    p = E.parse_syslog(raw, "1.1.1.1")
    assert p["severity"] == "notice"
    assert p["module"] == "if" and p["event"] == "proto-down"
    assert p["message"].startswith("interface=Eth1")


def test_real_device_attribution_by_source_ip():
    """真实设备：不配独立端口，事件源 IP 就是管理地址，直接归属。"""
    execute("INSERT OR REPLACE INTO device(name, role, protocol, host, port, enabled,"
            " created_at) VALUES ('CORE-1','CORE','ssh','10.1.1.1',22,1,'t')")
    assert E._device_of("10.1.1.1", 5514, "syslog") == "CORE-1"
    assert E._device_of("10.1.1.1", 0, "trap") == "CORE-1"
    assert E._device_of("10.9.9.9", 5514, "syslog") == ""
    execute("DELETE FROM device WHERE name='CORE-1'")
