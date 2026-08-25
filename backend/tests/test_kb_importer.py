"""手册导入：大小写保真、必需参数识别、表格抽取。"""
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
