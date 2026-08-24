"""真机端到端（-m live 才跑）：需要后端 :8099 + CNetNexus 容器 + API key。

CI 的单元阶段不碰这里；本地或 nightly 用
    pytest -m live
验证 agent 问答与一致性兜底在真环境仍成立。
"""
import json
import time
import urllib.request

import pytest

BASE = "http://127.0.0.1:8099"


def _call(path, payload=None, timeout=30):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _backend_up():
    try:
        _call("/api/devices")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.live


@pytest.fixture(scope="module", autouse=True)
def require_backend():
    if not _backend_up():
        pytest.skip("后端 :8099 未运行")


def _wait_task(task_id, budget=300):
    t0 = time.time()
    while time.time() - t0 < budget:
        t = _call("/api/diagnose/tasks/" + task_id)
        if t["status"] != "running":
            assert t["status"] == "done", t.get("error")
            return t["turn"]
        time.sleep(1)
    raise AssertionError("任务超时")


def test_agent_ask_and_freeze():
    s = _call("/api/diagnose/sessions", {"title": "live 测试"})
    turn1 = _wait_task(_call(
        "/api/diagnose/sessions/{0}/ask".format(s["id"]),
        {"question": "LEAF2 出什么问题了？", "mode": "agent"})["task_id"])
    assert turn1["answer"]["text"]
    assert turn1["fallback_level"] in ("AI", "F0")
    meta = turn1["model_meta"]
    # AI 生成时必须记录轮数；F0 命中冻结时必须标 frozen
    assert meta.get("rounds", 0) >= 1 or meta.get("frozen")

    # 同一问题再问：设备状态未变时应命中冻结（F0）且答案字节一致
    turn2 = _wait_task(_call(
        "/api/diagnose/sessions/{0}/ask".format(s["id"]),
        {"question": "LEAF2 出什么问题了？", "mode": "agent"})["task_id"])
    if turn2["snapshot_hash"] == turn1["snapshot_hash"]:
        assert turn2["fallback_level"] == "F0"
        assert turn2["answer"] == turn1["answer"]


def test_prompt_observability():
    rows = _call("/api/diagnose/sessions")
    assert rows, "无会话"
    turns = _call("/api/diagnose/sessions/{0}/turns".format(rows[0]["id"]))
    with_prompt = [t for t in turns if t.get("has_prompt")]
    assert with_prompt, "无带 prompt 的轮次"
    p = _call("/api/diagnose/turns/{0}/prompt".format(with_prompt[-1]["id"]))
    assert p["prompt_system"]
    assert p["prompt_user"]
