"""闭集校验：命令必须来自手册、大小写保真、必需参数必须补齐。"""
from app.modules.collect import planner


CATALOG = {
    "show interface": {"command": "show interface", "required": []},
    "show isis neighbor": {"command": "show isis neighbor",
                           "required": ["tag"]},
}


def test_exact_command_passes_and_keeps_case():
    got = planner._validate("show interface GE-1", CATALOG)
    assert got == "show interface GE-1"   # 参数大小写保真（GE-1 不能变 ge-1）


def test_unknown_command_rejected():
    assert planner._validate("reboot now", CATALOG) is None


def test_shell_metacharacters_rejected():
    assert planner._validate("show interface; rm -rf /", CATALOG) is None


def test_required_param_must_be_filled():
    # 光杆 base（缺 tag）必须被拒，避免设备回 Incomplete command
    assert planner._validate("show isis neighbor", CATALOG) is None
    assert planner._validate("show isis neighbor 1", CATALOG) \
        == "show isis neighbor 1"
