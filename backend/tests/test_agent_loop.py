"""Agent 运行时（LangGraph，离线）：注入假模型，验证我们包在框架外面的笼子。

循环本身是开源框架的，不测它；测的是 run_cli 工具的守门性质：
闭集校验、去重、预算上限、设备白名单，以及 run_loop 的轨迹/纪元形状。
"""
from typing import Any, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.modules.collect import planner
from app.modules.diagnose import agent


class FakeToolModel(BaseChatModel):
    """按脚本逐条吐 AIMessage 的假模型。cache=False 绕开全局 SQLite 缓存。"""

    script: List[AIMessage]

    def __init__(self, script: List[AIMessage]):
        super().__init__(script=script, cache=False)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeToolModel":
        return self

    def _generate(self, messages: Any, stop: Optional[List[str]] = None,
                  run_manager: Any = None, **kwargs: Any) -> ChatResult:
        msg = self.script.pop(0)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "fake-tool"


CATALOG = {
    "show interface": {"command": "show interface", "base": "show interface",
                       "syntax": "show interface", "purpose": "", "required": []},
    "show lldp neighbors": {"command": "show lldp neighbors",
                            "base": "show lldp neighbors",
                            "syntax": "show lldp neighbors", "purpose": "",
                            "required": []},
}


def _call(cid: str, device: str, command: str) -> dict:
    return {"id": cid, "name": "run_cli", "type": "tool_call",
            "args": {"device": device, "command": command, "reason": "r"}}


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(planner, "_catalog_index", lambda: dict(CATALOG))
    from app.modules.devices import service as dev
    monkeypatch.setattr(dev, "unsupported_map", lambda: {})
    collected: List[dict] = []

    def fake_collect(steps, plan_hash, plan_engine, task_id="", epoch_id=None):
        collected.extend(steps)
        return {"epoch_id": 1,
                "blocks": [{"device": s["device"], "command": s["command"],
                            "output": "up"} for s in steps]}
    monkeypatch.setattr(agent.collect, "collect", fake_collect)
    monkeypatch.setattr(agent, "_ensure_cache", lambda: None)
    return collected


def _run(script):
    return agent.run_loop("q", ["LEAF1"], {}, "", "",
                          model=FakeToolModel(script))


def test_run_then_answer(wired):
    out = _run([
        AIMessage(content="先看接口",
                  tool_calls=[_call("1", "LEAF1", "show interface")]),
        AIMessage(content="结论：接口正常。\n置信度：高"),
    ])
    assert out["answer"]["text"].startswith("结论")
    assert out["answer"]["confidence"] == "高"
    assert [t["action"] for t in out["transcript"]] == ["run", "answer"]
    assert out["epoch_id"] == 1
    assert wired == [{"device": "LEAF1", "command": "show interface",
                      "reason": "r"}]


def test_closed_set_and_device_whitelist(wired):
    out = _run([
        AIMessage(content="", tool_calls=[
            _call("1", "LEAF1", "reboot"),                 # 不在清单
            _call("2", "SPINE9", "show interface"),        # 设备不存在
            _call("3", "LEAF1", "show interface"),         # 合法
        ]),
        AIMessage(content="done"),
    ])
    # 只有合法那条真正下发；非法调用得到 [拒绝] 回执而不是执行
    assert wired == [{"device": "LEAF1", "command": "show interface",
                      "reason": "r"}]
    assert out["answer"]["text"] == "done"


def test_dedup_across_rounds(wired):
    _run([
        AIMessage(content="", tool_calls=[_call("1", "LEAF1", "show interface")]),
        AIMessage(content="", tool_calls=[_call("2", "LEAF1", "show interface")]),
        AIMessage(content="done"),
    ])
    assert len(wired) == 1          # 第二次重复被拒，不再碰设备


def test_budget_cap(wired, monkeypatch):
    monkeypatch.setattr(agent, "MAX_CMDS_TOTAL", 1)
    _run([
        AIMessage(content="", tool_calls=[
            _call("1", "LEAF1", "show interface"),
            _call("2", "LEAF1", "show lldp neighbors"),
        ]),
        AIMessage(content="done"),
    ])
    assert len(wired) == 1          # 预算 1 条，第二条被拒


def test_recursion_limit_terminates(wired, monkeypatch):
    monkeypatch.setattr(agent, "MAX_ROUNDS", 2)
    cmds = ["show interface", "show lldp neighbors"]
    # 模型永远要求继续采：框架的 recursion_limit 兜底终止
    script = [AIMessage(content="",
                        tool_calls=[_call(str(i), "LEAF1", cmds[i % 2])])
              for i in range(10)]
    out = _run(script)
    assert out["answer"] is None
    assert "最大轮数" in out["error"]


def test_model_failure_surfaces_error(wired):
    class Boom(FakeToolModel):
        def _generate(self, *a, **k):
            raise RuntimeError("api down")
    out = agent.run_loop("q", ["LEAF1"], {}, "", "", model=Boom([]))
    assert out["answer"] is None
    assert "api down" in out["error"]


def test_cache_key_ignores_volatile_ids():
    """两次运行只差消息 uuid / 运行期元数据 → 必须归一成同一个缓存键。"""
    import json as _json
    a = _json.dumps([{"kwargs": {"content": "hi", "id": "uuid-1",
                                 "response_metadata": {"token_usage": 42}}}])
    b = _json.dumps([{"kwargs": {"content": "hi", "id": "uuid-2",
                                 "response_metadata": {"token_usage": 99}}}])
    assert agent.canonical_prompt_key(a) == agent.canonical_prompt_key(b)
    c = _json.dumps([{"kwargs": {"content": "HELLO", "id": "uuid-1"}}])
    assert agent.canonical_prompt_key(a) != agent.canonical_prompt_key(c)
