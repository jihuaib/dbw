"""归一化是一致性的根基：同一状态的两次回显必须归一成同一串字节。"""
from app.modules.collect import normalize as N


def _norm(text, profile=None):
    return N.normalize_output(text, profile)["text"]


def test_erases_timestamps_and_durations():
    a = "Time: 2026-08-24 10:00:01\nGE-1 up 00:03:04 \n"
    b = "Time: 2026-08-24 10:12:44\nGE-1 up 00:59:59 \n"
    assert _norm(a) == _norm(b)
    assert "<ELIDED:timestamp>" in _norm(a)


def test_collapses_column_width_drift():
    # 同一取值、不同列宽（尾随空格漂移）必须归一致
    a = "MAC          VLAN\naa:bb  10"
    b = "MAC     VLAN\naa:bb      10"
    assert _norm(a) == _norm(b)


def test_header_aware_column_elision():
    text = ("Neighbor    State   Dead    Address\n"
            "1.1.1.1     Full    00:38   10.0.0.1\n")
    text2 = text.replace("00:38", "00:12")
    assert _norm(text) == _norm(text2)
    # 易变列擦除、非易变列保留
    assert "<ELIDED:dead>" in _norm(text)
    assert "Full" in _norm(text)
    assert "10.0.0.1" in _norm(text)


def test_calibrate_then_apply_profile():
    samples = ["a 100 b", "a 250 b", "a 999 b"]
    pos = N.calibrate(samples)
    assert pos == [(0, 1)]         # 只有实际在变的位置被标定
    outs = {N.apply_profile(s, pos)[0] for s in samples}
    assert len(outs) == 1          # 标定后三份样本归一致


def test_calibrate_refuses_structural_change():
    # 行/词数对不上＝内容真变了，不是抖动，放弃标定
    assert N.calibrate(["a b", "a b c"]) == []


def test_profile_backstops_unknown_fields():
    # 规则表没覆盖的字段，靠实测 profile 兜底擦除
    a, b = "weird-counter 12345", "weird-counter 99999"
    pos = N.calibrate([a, b])
    assert _norm(a, pos) == _norm(b, pos)


def test_normalize_is_idempotent():
    raw = "Time: 2026-08-24 10:00:01\nifindex 5 up\n"
    once = _norm(raw)
    assert _norm(once) == once


def test_lldp_ttl_countdown_erased():
    # LLDP 详情里的存活倒计时：19 sec remaining, 1 sec age —— 表头列擦除覆盖不到
    a = "  TTL       : 19 sec remaining, 1 sec age"
    b = "  TTL       : 15 sec remaining, 5 sec age"
    assert _norm(a) == _norm(b)
