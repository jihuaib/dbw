"""Agent 运行时 —— 基于开源方案 LangGraph（LangChain 的 Agent 框架）。

不再自研工具循环：`create_react_agent` 提供主流 Agent 的完整循环
（模型自主决定调工具还是给结论、并行工具调用、流式输出）。
我们只做两件事：

  · 把「在设备上执行只读命令」包装成一个带闭集校验的 tool ——
    命令必须来自手册清单、必需参数必须补齐、探明不支持的不放行、
    去重、总量上限。框架管循环，笼子还是我们的。
  · 确定性：temperature=0 + LangChain 的 SQLite **精确匹配缓存** ——
    同一串消息第二次出现直接回放上次的回复，不再调模型。
    于是同一问题 + 同一设备状态 → 同一工具调用序列 → 同一快照 → 同一指纹。

流式事件（token 增量、工具调用、回显）推给进度总线，前端实时渲染 ——
点完发送就能看见模型在想什么、下了什么命令、设备回了什么。
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional

from ...core.config import DATA_DIR
from ..collect import planner
from ..collect import service as collect

MAX_ROUNDS = 10           # 最多几轮模型调用（每轮可并行多条工具调用）
MAX_CMDS_TOTAL = 24       # 一次诊断最多下发多少条命令
ECHO_LIMIT = 6000         # 回给模型的单条回显上限（字符）

AGENT_SYSTEM = """你是网络故障诊断 Agent。用 run_cli 工具在设备上执行**只读命令**取证，
每次看到回显后再决定下一步：继续取证，还是给出结论。

取证原则：
1. command 必须来自下面给出的命令清单；不得自创。
2. 标了「必须补齐参数」的命令，参数值只能取自已知实体或**已采到的回显**里出现的真实值。
   **绝不凭空猜参数值** —— 猜错会被设备拒绝，还会污染证据。
3. unsupported 里的「设备 → 命令」是已探明不支持的，不要再提议。
4. 已经采过的命令不要重复采。
5. 命令与参数的大小写照抄清单和回显（接口名等在很多设备上区分大小写）。
5b. **批量取证**：同一轮里把当下就能确定要采的命令一次全发出去
   （多台设备、多条命令并行调用 run_cli），不要一条一条挤牙膏 ——
   轮数有限（最多 10 轮、共 24 条命令预算），省着用。
6. **先排除「配置使然」再下物理结论**：接口 DOWN、协议没起、邻居消失，
   都可能是配置里显式关闭/未启用（如接口 shutdown）。清单里若有配置查看类命令
   （如 show current-configuration），先采来核对 —— 配置性关闭与物理故障的
   根因层级和处置建议完全不同。

下结论时（不再调工具，直接输出最终回答）：
- 只使用回显里出现的事实，**绝不臆造**表项、接口、地址或状态。
- 标记 <ELIDED:...> 的是被有意擦除的易变量（计数器、倒计时），不要围绕它们下结论。
- 根因要落到**最深可证层面**：配置里明写着 shutdown，根因就是「接口被配置关闭」
  （处置是改配置），而不是「物理链路未建立」（处置是查线缆）。
- 按固定结构组织：一句话结论 → 根因（层级/严重度/证据/建议）→
  派生现象 → 证据缺口 → 置信度（确认/高/中/需人工确认）。
- 没采到的证据要在「证据缺口」里列出，并相应下调置信度。
"""

_CACHE_READY = False

# 缓存键里不许有随机量：LangGraph 给每条消息随机分配 uuid，
# AIMessage 还带 run_id / token 用量等运行期元数据 —— 这些进了键，
# 同一段对话第二次出现就永远命不中，重放确定性直接失效。
_DROP_KEYS = {"id", "run_id", "usage_metadata", "response_metadata",
              "tool_call_id"}


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items()
                if k not in _DROP_KEYS}
    if isinstance(obj, list):
        return [_strip_volatile(x) for x in obj]
    return obj


def canonical_prompt_key(prompt: str) -> str:
    """把序列化后的消息串归一成语义键：只留角色、内容、工具调用。"""
    try:
        stripped = _strip_volatile(json.loads(prompt))
        return json.dumps(stripped, ensure_ascii=False, sort_keys=True)
    except (ValueError, TypeError):
        return prompt


def _make_cache():
    from langchain_community.cache import SQLiteCache

    class CanonicalSQLiteCache(SQLiteCache):
        """按语义内容命中的 SQLite 精确缓存。

        这是确定性的关键 —— 同样的消息序列（含工具回显）第二次出现时
        直接回放上次的回复，模型的随机性被彻底移出重放路径。
        """

        def lookup(self, prompt, llm_string):
            return super().lookup(canonical_prompt_key(prompt), llm_string)

        def update(self, prompt, llm_string, return_val):
            super().update(canonical_prompt_key(prompt), llm_string, return_val)

    return CanonicalSQLiteCache(
        database_path=str(DATA_DIR / "agent-llm-cache.db"))


def _ensure_cache() -> None:
    global _CACHE_READY
    if _CACHE_READY:
        return
    from langchain_core.globals import set_llm_cache
    set_llm_cache(_make_cache())
    _CACHE_READY = True


def build_chat_model():
    """按设置页的服务商配置构建 LangChain 聊天模型。temperature=0 压随机性。"""
    from ..settings import service as settings
    cfg = settings.llm_config()
    if cfg["provider"] == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=cfg["model"], api_key=cfg["api_key"],
                             temperature=0, max_tokens=4000)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=cfg["model"], api_key=cfg["api_key"],
                      base_url=cfg["base_url"] or None,
                      temperature=0, seed=1)


def _catalog_listing(catalog: Dict[str, Dict[str, Any]]) -> str:
    return "\n".join(
        "- {0}{1}{2}".format(
            c.get("syntax") or c["command"],
            "  # " + c["purpose"] if c.get("purpose") else "",
            "  必须补齐参数: " + "/".join(c["required"]) if c.get("required") else "")
        for c in (catalog[k] for k in sorted(catalog)))


def _build_context(question_norm: str, devices: List[str],
                   entities: Dict[str, Any], history: str, topo: str,
                   listing: str, blocked: Dict[str, Any],
                   recent_events: str = "") -> str:
    parts = [AGENT_SYSTEM]
    parts.append("# 设备清单\n" + "、".join(sorted(devices)))
    if topo:
        parts.append("# 网络拓扑\n" + topo)
    if recent_events:
        parts.append("# 近期设备事件（syslog / SNMP trap，已按内容去重）\n"
                     "这些是设备**主动上报**的状态变化，可作为取证方向的线索；"
                     "结论仍须以命令回显为准。\n" + recent_events)
    if entities:
        parts.append("# 从提问中抽取的实体\n"
                     + json.dumps(entities, ensure_ascii=False, sort_keys=True))
    if history:
        parts.append("# 会话历史（此前几轮的问题与结论摘要）\n" + history)
    unsupported = {d: sorted(c) for d, c in sorted(blocked.items()) if c}
    if unsupported:
        parts.append("# unsupported（已探明不支持，勿再提议）\n"
                     + json.dumps(unsupported, ensure_ascii=False, sort_keys=True))
    parts.append("# 命令清单（闭集，只能从这里选）\n" + listing)
    return "\n\n".join(parts)


def run_loop(question_norm: str, devices: List[str], entities: Dict[str, Any],
             history: str, topo: str, task_id: str = "",
             model=None, recent_events: str = "") -> Dict[str, Any]:
    """跑 LangGraph ReAct Agent，返回与旧实现相同的形状：
    {answer, rounds, blocks, epoch_id, transcript, prompts, error}。

    `model` 参数用于测试注入假模型；缺省按设置页配置构建。
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from langchain_core.tools import StructuredTool
    from langgraph.errors import GraphRecursionError
    from langgraph.prebuilt import create_react_agent

    from ..devices import service as device_service
    from . import progress

    _ensure_cache()
    catalog = planner._catalog_index()
    blocked = device_service.unsupported_map()
    listing = _catalog_listing(catalog)
    system = _build_context(question_norm, devices, entities, history, topo,
                            listing, blocked, recent_events)

    # 一次诊断的共享状态：闭集校验之外，还要跨工具调用去重、计数、聚纪元。
    # 框架会并行执行同一轮的多个工具调用，锁保证纪元只建一次、计数不竞态。
    ctx: Dict[str, Any] = {"epoch_id": None, "executed": set(), "count": 0,
                           "lock": threading.Lock()}

    def run_cli(device: str, command: str, reason: str = "") -> str:
        """在指定网络设备上执行一条只读 CLI 命令，返回回显。

        device 必须来自设备清单；command 必须严格来自命令清单，
        标注「必须补齐参数」的命令要把参数填上真实值。
        """
        dev = str(device).strip()
        if dev not in devices:
            return "[拒绝] 设备 {0} 不在设备清单里".format(dev)
        cmd = planner._validate(command, catalog)
        if not cmd:
            return ("[拒绝] 命令不在清单里或必需参数未补齐：{0}。"
                    "只能从命令清单选择，参数值不许猜。".format(command))
        if cmd in blocked.get(dev, set()):
            return "[拒绝] 已探明 {0} 不支持该命令".format(dev)
        with ctx["lock"]:
            if (dev, cmd) in ctx["executed"]:
                return "[拒绝] 该命令已采过，回显在之前的工具结果里，不要重复采"
            if ctx["count"] >= MAX_CMDS_TOTAL:
                return ("[拒绝] 本次诊断的命令预算（{0} 条）已用完，"
                        "请用现有证据下结论".format(MAX_CMDS_TOTAL))
            ctx["executed"].add((dev, cmd))
            ctx["count"] += 1

            progress.event(task_id, "tool_start",
                           {"device": dev, "command": cmd,
                            "reason": str(reason)[:160]})
            got = collect.collect([{"device": dev, "command": cmd,
                                    "reason": str(reason)[:160]}],
                                  "", "agent", "", epoch_id=ctx["epoch_id"])
            ctx["epoch_id"] = got["epoch_id"]
        # 追加模式返回的是整个纪元的 blocks，取本条命令自己的那块
        block = next(b for b in got["blocks"]
                     if b["device"] == dev and b["command"] == cmd)
        ok = not block["output"].startswith("<未采到")
        progress.event(task_id, "tool_end",
                       {"device": dev, "command": cmd, "ok": ok,
                        "output": block["output"][:ECHO_LIMIT]})
        return block["output"][:ECHO_LIMIT]

    tool = StructuredTool.from_function(func=run_cli, name="run_cli",
                                        description=run_cli.__doc__)
    agent = create_react_agent(model or build_chat_model(), [tool])
    messages = [SystemMessage(content=system),
                HumanMessage(content=question_norm)]

    transcript: List[Dict[str, Any]] = []
    prompts: List[Dict[str, Any]] = [{"round": 0, "system": system,
                                      "user": question_norm, "raw": None}]
    final_text = ""
    error = ""
    rnd = 0
    try:
        for mode, chunk in agent.stream({"messages": messages},
                                        {"recursion_limit": MAX_ROUNDS * 2 + 1},
                                        stream_mode=["updates", "messages"]):
            if mode == "messages":
                msg_chunk, _meta = chunk
                delta = _content_text(getattr(msg_chunk, "content", ""))
                if delta and type(msg_chunk).__name__ == "AIMessageChunk":
                    progress.event(task_id, "delta", {"text": delta})
                continue
            for node, payload in chunk.items():
                for msg in (payload or {}).get("messages", []):
                    if isinstance(msg, AIMessage):
                        rnd += 1
                        calls = [{"device": c["args"].get("device", ""),
                                  "command": c["args"].get("command", ""),
                                  "reason": c["args"].get("reason", "")}
                                 for c in (msg.tool_calls or [])]
                        text = _content_text(msg.content)
                        transcript.append({
                            "round": rnd,
                            "action": "run" if calls else "answer",
                            "thinking": text[:400],
                            "commands": calls})
                        prompts.append({"round": rnd, "system": "",
                                        "user": "（上下文=系统提示+此前全部消息与工具回显）",
                                        "raw": {"content": text,
                                                "tool_calls": calls}})
                        if calls:
                            progress.step(task_id, "第 {0} 轮".format(rnd),
                                          text[:80] or "调用工具取证")
                        else:
                            final_text = text
                            progress.event(task_id, "answer",
                                           {"text": final_text})
    except GraphRecursionError:
        error = "达到最大轮数（{0}），模型未收敛到结论".format(MAX_ROUNDS)
    except Exception as exc:  # 网络/鉴权等
        error = "{0}: {1}".format(type(exc).__name__, exc)

    answer: Optional[Dict[str, Any]] = None
    if final_text.startswith("Sorry, need more steps"):
        # 框架撞上限时补的兜底话术，不算结论
        final_text = ""
    elif final_text.strip() and error.startswith("达到最大轮数"):
        # 模型恰好在最后一步给出了真结论，之后才撞上限 —— 结论有效
        error = ""
    if final_text.strip():
        first_line = final_text.strip().split("\n")[0][:120]
        answer = {"summary": first_line, "text": final_text.strip(),
                  "root_causes": [], "derived": [], "gaps": [],
                  "confidence": _extract_confidence(final_text)}

    blocks = (collect._all_blocks(ctx["epoch_id"])
              if ctx["epoch_id"] else [])
    blocks.sort(key=lambda b: (b["device"], b["command"]))
    return {"answer": answer, "rounds": rnd, "blocks": blocks,
            "epoch_id": ctx["epoch_id"], "transcript": transcript,
            "prompts": prompts, "error": error}


def _content_text(content: Any) -> str:
    """AIMessage.content 可能是 str，也可能是分块列表（Anthropic 风格）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(x.get("text", "") for x in content
                       if isinstance(x, dict) and x.get("type") == "text")
    return str(content or "")


def _extract_confidence(text: str) -> str:
    for level in ("确认", "高", "中", "需人工确认"):
        if "置信度" in text and level in text.split("置信度")[-1][:20]:
            return level
    return ""
