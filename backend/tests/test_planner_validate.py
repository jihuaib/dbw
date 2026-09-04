"""闭集校验：命令必须来自手册、大小写保真、必需参数必须补齐。"""
from app.modules.collect import planner


CATALOG = {
    "show interface": {"command": "show interface",
                       "syntax": "show interface [<if-name>]",
                       "required": [], "params": ["if-name"]},
    "show isis neighbor": {"command": "show isis neighbor",
                           "syntax": "show isis neighbor <tag>",
                           "required": ["tag"], "params": ["tag"]},
}


def test_exact_command_passes_and_keeps_case():
    got = planner._validate("show interface GE-1", CATALOG)
    assert got == "show interface GE-1"   # 参数大小写保真（GE-1 不能变 ge-1）


def test_unknown_command_rejected():
    assert planner._validate("reboot now", CATALOG) is None


def test_legacy_entry_does_not_accept_arbitrary_extra_tokens():
    legacy = {"x": {"command": "show interface", "required": []}}
    assert planner._validate("show interface", legacy) == "show interface"
    assert planner._validate("show interface reboot now", legacy) is None


def test_shell_metacharacters_rejected():
    assert planner._validate("show interface; rm -rf /", CATALOG) is None
    assert planner._validate("show interface\nreboot", CATALOG) is None


def test_required_param_must_be_filled():
    # 光杆 base（缺 tag）必须被拒，避免设备回 Incomplete command
    assert planner._validate("show isis neighbor", CATALOG) is None
    assert planner._validate("show isis neighbor 1", CATALOG) \
        == "show isis neighbor 1"


def test_complete_syntax_matches_middle_parameters_and_fixed_words():
    syntax = ("display bgp [ instance <instance-name> ] bmp server "
              "<server-number> monitor-peer all")
    catalog = {syntax: {"command": "display bgp bmp server",
                        "syntax": syntax, "required": ["server-number"]}}
    command = "display bgp instance Blue bmp server 1 monitor-peer all"
    assert planner._validate(command, catalog) == command
    assert planner._validate("display bgp bmp server 1", catalog) is None
    assert planner._validate(
        "display bgp bmp server 1 monitor-peer unknown", catalog) is None
