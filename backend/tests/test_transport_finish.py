"""回显判定：拒绝行、空回显、未见提示符都必须判失败，坏证据不能进快照。"""
from app.modules.devices import transport as T


def test_rejected_command_detected():
    r = T.finish("show foo\n% Invalid command\nLEAF1# ", "show foo")
    assert not r["ok"]
    assert r["unsupported"]


def test_incomplete_command_detected():
    r = T.finish("show isis neighbor\nIncomplete command\nLEAF1# ",
                 "show isis neighbor")
    assert not r["ok"]


def test_normal_output_passes():
    r = T.finish("show version\nCNetNexus 1.0\nLEAF1# ", "show version")
    assert r["ok"]
    assert "CNetNexus" in r["text"]


def test_empty_output_fails():
    assert not T.finish("", "show version")["ok"]


def test_missing_prompt_fails():
    # 读到一半连接断了：有内容但没见提示符，宁可判缺失
    r = T.finish("show version\nCNetNexus 1.0\n", "show version")
    assert not r["ok"]
