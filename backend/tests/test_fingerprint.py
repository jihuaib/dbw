"""指纹 = 一致性的锚：任何一个输入分量变了，指纹必须变；全同则必须同。"""
from app.modules.diagnose import prompt as P


BASE = dict(question_norm="q", snapshot_hash="s", model_identity="m",
            catalog_digest="c", history_hash="h", mode="agent")


def _fp(**kw):
    a = dict(BASE); a.update(kw)
    return P.fingerprint(**a)


def test_same_inputs_same_fingerprint():
    assert _fp() == _fp()


def test_each_component_changes_fingerprint():
    base = _fp()
    for k in BASE:
        assert _fp(**{k: BASE[k] + "x"}) != base, k


def test_history_and_mode_are_isolated():
    # 会话前缀不同 → 追问指纹不同；模式不同（agent/single）→ 指纹不同
    assert _fp(history_hash="") != _fp(history_hash="turn1")
    assert _fp(mode="single") != _fp(mode="agent")
