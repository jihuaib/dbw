"""手册导入：大小写保真、必需参数识别、表格抽取。"""
from pathlib import Path

from app.modules.kb import importer as I


def test_clean_keeps_case_key_lowers():
    assert I._clean("  Show   Interface ") == "Show Interface"
    assert I._key("Show   Interface") == "show interface"


def test_split_syntax_marks_required():
    got = I.split_syntax("show isis neighbor <tag> [detail]")
    assert got["base"] == "show isis neighbor"
    assert got["required"] == ["tag"]   # 必需参数不能剥掉；可选段可以


def test_markdown_table_extraction():
    md = ("| 命令 | 视图 | 说明 |\n|---|---|---|\n"
          "| `show version` | any | 查看版本 |\n"
          "| `show interface <if-name>` | any | 查看接口 |\n")
    cmds = {c["command"]: c for c in I.extract_by_markdown_table(md)}
    assert cmds["show version"]["required"] == []
    assert cmds["show interface"]["required"] == ["if-name"]
    assert "<if-name>" in cmds["show interface"]["syntax"]


def test_markdown_table_keeps_escaped_pipe_variants():
    md = ("| command | view | description |\n|---|---|---|\n"
          "| `show bgp route af {ipv4-qp\\|ipv6-qp} peer "
          "<ipv4-address\\|ipv6-address> advertise-routes` | global | 出方向路由 |\n")
    commands = I.extract_by_markdown_table(md)
    assert len(commands) == 1
    command = commands[0]
    assert command["syntax"] == (
        "show bgp route af {ipv4-qp|ipv6-qp} peer "
        "<ipv4-address|ipv6-address> advertise-routes")
    assert command["purpose"] == "出方向路由"
    assert command["required"] == ["ipv4-address|ipv6-address"]


def test_table_auto_detection_without_backticks_and_command_not_first_column():
    md = ("| 序号 | 命令 | 说明 |\n|---:|---|---|\n"
          "| 1 | display bgp summary | BGP 汇总 |\n")
    assert I.looks_like_table_doc(md)
    commands = I.extract_by_markdown_table(md)
    assert [c["command"] for c in commands] == ["display bgp summary"]


def test_non_table_extractors_preserve_same_prefix_variants():
    plain = (
        "display bgp peer <peer> received-routes\n"
        "display bgp peer <peer> advertised-routes\n")
    assert len(I.extract_by_rule(plain)) == 2

    inline = (
        "`show bgp peer <peer> received-routes`\n"
        "`show bgp peer <peer> advertised-routes`\n")
    assert len(I.extract_by_inline(inline)) == 2


def test_hidden_dangerous_branch_is_not_imported():
    assert I._entry("show bgp summary [ reset ]") is None
    assert I._entry("show bgp summary | system-view terminal now") is None


def test_huawei_repeat_annotation_survives_markdown_normalization():
    value = I._plain_markdown(
        "*community-number*&&lt;1-32&gt;", parameters=True)
    assert value == "<community-number>&<1-32>"


def test_ai_extraction_analyzes_parameter_syntax_before_storage():
    from app.core import llm
    original = llm.call_json
    llm.call_json = lambda *args, **kwargs: {
        "ok": True,
        "data": {"commands": [{
            "command": "show interface <if-name>",
            "purpose": "接口详情",
            "keywords": ["端口"],
            "params": ["if-name"],
        }]},
        "cached": False,
    }
    try:
        result = I.extract_by_ai("manual")
    finally:
        llm.call_json = original
    command = result["commands"][0]
    assert command["command"] == "show interface"
    assert command["required"] == ["if-name"]


def test_builtin_bgp_table_preserves_all_show_syntax_variants():
    path = Path(__file__).resolve().parents[1] / "samples" / "cnetnexus" / "bgp.md"
    commands = I.extract_by_markdown_table(path.read_text(encoding="utf-8"))
    assert len(commands) == 17
    syntaxes = {c["syntax"] for c in commands}
    assert any("recieve-routes" in syntax and "<ipv4-address|ipv6-address>" in syntax
               for syntax in syntaxes)
    assert any("advertise-routes" in syntax and "{ipv4-qp|ipv6-qp}" in syntax
               for syntax in syntaxes)


def test_inline_backtick_extraction():
    """标题/列表式文档（cli.md 风格）：命令只在反引号里出现也必须提出来。

    实测教训：`show current-configuration` 漏提 → Agent 无权采配置 →
    shutdown 的接口被诊断成「物理链路未建立」。
    """
    md = ("### 2.3 `show current-configuration`\n"
          "显示从各业务模块收集的当前运行配置。\n\n"
          "- **用法**：`show current-configuration`\n"
          "- **视图**：`global`（所有视图可用）\n\n"
          "### 2.7 `show configuration difference current-configuration"
          " <snapshot-name>`\n"
          "- **用法**：`show configuration difference current-configuration"
          " <snapshot-name>`\n")
    cmds = {c["command"]: c for c in I.extract_by_rule(md)}
    assert "show current-configuration" in cmds
    diff = cmds["show configuration difference current-configuration"]
    assert diff["required"] == ["snapshot-name"]   # 必需参数不能丢


def test_inline_skips_non_commands():
    md = "- 配置在 `data/configs/<name>.cfg`；另见 `shutdown` 与 `lldp timer 5`\n"
    assert I.extract_by_rule(md) == []


def test_heading_only_command_gets_desc():
    """命令只出现在标题里（sbmp.md 风格）：也要提取，说明用标题下正文回填。"""
    md = ("### 3.1 `show bmp-server`\n"
          "显示 BMP 服务器状态和运行统计。\n")
    cmds = {c["command"]: c for c in I.extract_by_rule(md)}
    assert "show bmp-server" in cmds
    assert "BMP 服务器" in cmds["show bmp-server"]["purpose"]


def test_observer_echo_commands_excluded():
    """CLI 会话/审计类命令是观测者的镜子，不能进证据闭集。"""
    md = ("| 命令 | 视图 | 说明 |\n|---|---|---|\n"
          "| `show cli history` | global | 命令历史 |\n"
          "| `show line` | global | 线路状态 |\n"
          "| `show version` | any | 版本 |\n")
    cmds = {c["command"] for c in I.extract_by_markdown_table(md)}
    assert cmds == {"show version"}


H3C_MD = """# display ospf peer

## 命令功能
display ospf peer 命令用来显示 OSPF 邻居的信息。

## 命令格式
display ospf [ process-id ] peer [ verbose ] [ interface-type interface-number ] [ neighbor-id ]

## 视图
任意视图

# 1.2 display interface brief

【命令】 display interface [ interface-type [ interface-number ] ] brief [ description ]

```
display ip routing-table [ verbose ]
```

【命令】 display vlan vlan-id
"""


def test_h3c_style_markdown():
    cmds = {c["command"]: c for c in I.extract_by_rule(H3C_MD)}
    assert "display ospf peer" in cmds                 # 标题 + 命令格式段
    assert "display interface brief" in cmds           # 【命令】 行，可选段剥掉
    assert "display ip routing-table" in cmds          # 代码块
    assert cmds["display vlan"]["required"] == ["vlan-id"]   # 不带 <> 的必填参数按命名识别
    assert "任意视图" not in cmds                       # 非命令行不会误入


HUAWEI_COMMAND_BLOCKS = r"""
### display bgp all peer summary

**display bgp all peer summary**命令用来显示BGP所有地址族下对等体的状态。

【命令】

**display bgp** \[ **instance** *instance-name* \] **all peer summary**

【视图】

任意视图

### display bgp bmp server monitor-peer

**display bgp bmp server monitor-peer**命令用来显示BMP监控的BGP对等体。

【命令】

**display bgp** \[ **instance** *instance-name* \] **bmp server**
*server-number* **monitor-peer all**

**display bgp** \[ **instance** *instance-name* \] **bmp server**
*server-number* **monitor-peer** { **ipv4** | **ipv6** }
{ *ipv4-address* | *ipv6-address* }

【视图】

任意视图

### display bgp link-state segment-list

**display bgp link-state segment-list**命令用来显示SID列表。

【命令】

**display bgp** \[ **instance** *instance-name* \] **link-state**
*ls-prefix* **segment-list**

【视图】

任意视图

【举例】

<Sysname> display bgp link-state 1.1.1.1 segment-list

1. display bgp peer statistics命令显示信息描述表
"""


def test_huawei_command_blocks_join_lines_and_unwrap_markdown():
    commands = I.extract_by_rule(HUAWEI_COMMAND_BLOCKS)
    assert len(commands) == 4
    syntaxes = {c["syntax"]: c for c in commands}
    summary = syntaxes[
        "display bgp [ instance <instance-name> ] all peer summary"]
    assert summary["command"] == "display bgp all peer summary"
    assert "所有地址族" in summary["purpose"]
    monitor = syntaxes[
        "display bgp [ instance <instance-name> ] bmp server "
        "<server-number> monitor-peer all"]
    assert monitor["command"] == "display bgp bmp server"
    assert monitor["required"] == ["server-number"]
    segment = syntaxes[
        "display bgp [ instance <instance-name> ] link-state "
        "<ls-prefix> segment-list"]
    assert segment["required"] == ["ls-prefix"]
    assert all("*" not in syntax and "\\" not in syntax for syntax in syntaxes)
    assert not any("statistics命令显示信息描述表" in c["syntax"] for c in commands)


def test_formal_command_blocks_take_priority_over_tables():
    mixed = HUAWEI_COMMAND_BLOCKS + (
        "\n| 命令 | 说明 |\n|---|---|\n"
        "| `display should-not-win` | 示例表 |\n")
    assert not I.looks_like_table_doc(mixed)
    commands = I.extract_by_rule(mixed)
    assert len(commands) == 4
    assert all(c["command"] != "display should-not-win" for c in commands)
