"""采集纪元：agent 多轮追加必须落进同一纪元，快照覆盖全部轮次。"""
from app.modules.collect import service as C


class _FakeTransport:
    def device_names(self):
        return ["LEAF1"]

    def run(self, device, command):
        return {"ok": True, "text": command + " up", "error": ""}

    def close(self):
        pass


def _steps(cmd):
    return [{"device": "LEAF1", "command": cmd, "reason": "t"}]


def test_append_rounds_share_epoch(monkeypatch):
    monkeypatch.setattr(C, "LiveTransport", lambda *a, **k: _FakeTransport())
    r1 = C.collect(_steps("show a"), "", "test")
    r2 = C.collect(_steps("show b"), "", "test", epoch_id=r1["epoch_id"])
    assert r2["epoch_id"] == r1["epoch_id"]
    ep = C.epoch(r1["epoch_id"])
    caps = C.captures(r1["epoch_id"])
    assert {c["command"] for c in caps} == {"show a", "show b"}
    assert ep["snapshot_hash"]          # 快照哈希覆盖两轮
