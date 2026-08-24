"""诊断编排 + **一致性兜底策略**（赛题设计要求 1）。

核心机制一句话：
    一致性不来自「让模型稳定」，而来自「同一输入只调一次模型，答案冻结」。

六级兜底阶梯，逐级降级，但每一级都是确定的：

  F0 指纹冻结   指纹命中 → 原样返回，零模型调用          → 字节一致
  F1 快照复用   同一快照不同问法 → 复用同一份事实          → 事实一致
  F2 结构校验   AI 输出不合 schema → 固定次数重试          → 不放行脏输出
  F3 自洽投票   首次生成可 k 次采样，按根因编码多数票       → 首答稳定
  F4 模型不可用 无 key / 超时 / 报错 → 降级为「证据陈述」    → 仍有确定输出
  F5 证据缺失   采集失败 → 明确列出缺什么，下调置信度       → 缺失也确定
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Dict, List, Optional, Tuple, Tuple

from ...core import llm
from ...core.canon import sha256_of, short
from ...core.db import execute, loads, query, query_one
from ..collect import planner
from ..collect import service as collect
from ..devices import service as device_service
from ..kb import service as kb
from ..settings import service as settings
from . import models  # noqa: F401  建表注册
from . import agent
from . import progress
from . import prompt as P

FALLBACK_LADDER = [
    ("F0", "指纹冻结", "指纹命中，原样返回冻结答案，零模型调用"),
    ("F1", "快照复用", "同一快照不同问法，复用同一份事实"),
    ("F2", "结构校验", "AI 输出不合 schema，固定次数重试"),
    ("F3", "自洽投票", "首次生成 k 次采样，按根因编码多数票"),
    ("F4", "模型兜底", "模型不可用时降级为证据陈述，不下结论"),
    ("F5", "缺证兜底", "采集失败时列出缺口并下调置信度"),
]


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ── 会话 ──────────────────────────────────────────────────────────────
def create_session(title: str = "") -> Dict[str, Any]:
    sid = execute("INSERT INTO session(title, created_at) VALUES (?,?)",
                  (title or "新会话", _now()))
    return query_one("SELECT * FROM session WHERE id=?", (sid,))


def list_sessions() -> List[Dict[str, Any]]:
    rows = query("SELECT * FROM session ORDER BY id DESC")
    for r in rows:
        r["turn_count"] = query_one(
            "SELECT COUNT(*) n FROM turn WHERE session_id=?", (r["id"],))["n"]
    return rows


def delete_session(session_id: int) -> None:
    """删除会话及其**全部关联记录**：轮次、采集纪元与回显、冻结答案。

    共享的记录不动：同一指纹/纪元若被别的会话引用（同一问题在别处也问过），
    删除会破坏那边的一致性，所以只清理本会话独占的部分。"""
    rows = query("SELECT epoch_id, fingerprint FROM turn WHERE session_id=?",
                 (session_id,))
    epochs = {r["epoch_id"] for r in rows if r["epoch_id"]}
    fps = {r["fingerprint"] for r in rows if r["fingerprint"]}
    execute("DELETE FROM turn WHERE session_id=?", (session_id,))
    execute("DELETE FROM session WHERE id=?", (session_id,))
    for eid in epochs:
        if not query_one("SELECT id FROM turn WHERE epoch_id=? LIMIT 1", (eid,)):
            execute("DELETE FROM capture WHERE epoch_id=?", (eid,))
            execute("DELETE FROM epoch WHERE id=?", (eid,))
    for fp in fps:
        if not query_one("SELECT id FROM turn WHERE fingerprint=? LIMIT 1", (fp,)):
            execute("DELETE FROM frozen_answer WHERE fingerprint=?", (fp,))


def _turn_row(r: Dict[str, Any]) -> Dict[str, Any]:
    r["answer"] = loads(r["answer"], {})
    r["trace"] = loads(r["trace"], [])
    r["plan"] = loads(r["plan"], [])
    r["model_meta"] = loads(r.get("model_meta"), {})
    r["has_prompt"] = bool(r.get("prompt_user"))
    # 列表接口不带大字段，详情单独取
    for k in ("prompt_system", "prompt_user", "model_raw"):
        r.pop(k, None)
    return r


def turn_prompt(turn_id: int) -> Optional[Dict[str, Any]]:
    """取某一轮**逐字送给模型的内容**与模型原始回复。"""
    r = query_one("SELECT id, session_id, seq, question, prompt_system, prompt_user,"
                  " model_raw, model_meta, rounds, fingerprint, snapshot_hash,"
                  " fallback_level FROM turn WHERE id=?", (turn_id,))
    if not r:
        return None
    r["model_meta"] = loads(r["model_meta"], {})
    r["rounds"] = loads(r["rounds"], [])
    r["model_raw"] = loads(r["model_raw"], None) if r["model_raw"] else None
    return r


def list_turns(session_id: int) -> List[Dict[str, Any]]:
    return [_turn_row(r) for r in
            query("SELECT * FROM turn WHERE session_id=? ORDER BY seq", (session_id,))]


# ── 冻结答案 ──────────────────────────────────────────────────────────
def frozen_get(fingerprint: str) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM frozen_answer WHERE fingerprint=?", (fingerprint,))
    if not row:
        return None
    row["answer"] = loads(row["answer"], {})
    row["verified"] = bool(row["verified"])
    return row


def frozen_put(fingerprint: str, question_norm: str, snapshot_hash: str,
               answer: Dict[str, Any], verified: bool) -> None:
    execute(
        "INSERT OR REPLACE INTO frozen_answer(fingerprint, question_norm, snapshot_hash,"
        " model, answer, verified, hit_count, created_at) VALUES (?,?,?,?,?,?,0,?)",
        (fingerprint, question_norm, snapshot_hash, settings.model(),
         json.dumps(answer, ensure_ascii=False), 1 if verified else 0, _now()))


def frozen_hit(fingerprint: str) -> None:
    execute("UPDATE frozen_answer SET hit_count = hit_count + 1 WHERE fingerprint=?",
            (fingerprint,))


def list_frozen(limit: int = 50) -> List[Dict[str, Any]]:
    rows = query("SELECT fingerprint, question_norm, snapshot_hash, model, verified,"
                 " hit_count, created_at FROM frozen_answer ORDER BY created_at DESC"
                 " LIMIT ?", (limit,))
    for r in rows:
        r["verified"] = bool(r["verified"])
    return rows


def verify_frozen(fingerprint: str, verified: bool) -> Dict[str, Any]:
    execute("UPDATE frozen_answer SET verified=? WHERE fingerprint=?",
            (1 if verified else 0, fingerprint))
    return frozen_get(fingerprint) or {}


def unfreeze(fingerprint: str) -> None:
    execute("DELETE FROM frozen_answer WHERE fingerprint=?", (fingerprint,))


# ── 兜底：证据陈述（F4）────────────────────────────────────────────────
def evidence_statement(blocks: List[Dict[str, Any]], failed: int) -> Dict[str, Any]:
    """模型不可用时的输出：只陈述采到了什么，**不下结论**。

    这仍然是确定性的 —— 同一快照必然得到同一段文字。
    """
    lines = ["模型当前不可用，本次只做证据陈述，不给出根因判断。", "",
             "已采集 {0} 条命令回显：".format(len(blocks))]
    for b in blocks:
        first = (b["output"].split("\n") or [""])[0][:90]
        lines.append("  · {0} · {1} → {2}".format(b["device"], b["command"], first))
    if failed:
        lines.append("")
        lines.append("其中 {0} 条未采到，已计入证据快照。".format(failed))
    return {
        "summary": "模型不可用，仅陈述证据",
        "root_causes": [], "derived": [],
        "gaps": ["未调用模型，无根因判断"] + ([] if not failed else ["有命令未采到"]),
        "confidence": "需人工确认",
        "text": "\n".join(lines),
    }


def render(answer: Dict[str, Any]) -> str:
    """把结构化结论渲染成文本 —— 模板固定，所以同一结论必然同一措辞。"""
    if answer.get("text"):
        return answer["text"]
    lines: List[str] = []
    if answer.get("summary"):
        lines.append(answer["summary"])
        lines.append("")
    roots = answer.get("root_causes") or []
    if roots:
        lines.append("根因：")
        for i, r in enumerate(roots, 1):
            lines.append("  {0}. [{1}/{2}] {3} · {4} —— {5}".format(
                i, r.get("layer", "?"), r.get("severity", "?"),
                r.get("device", ""), r.get("object", ""), r.get("statement", "")))
            if r.get("evidence"):
                lines.append("     证据：{0}".format("；".join(r["evidence"])))
            if r.get("advice"):
                lines.append("     建议：{0}".format(r["advice"]))
    else:
        lines.append("未发现根因级异常。")
    derived = answer.get("derived") or []
    if derived:
        lines.append("")
        lines.append("派生现象（由根因引起，不必单独处理）：")
        for d in derived:
            lines.append("  · {0} · {1} —— {2}（源自 {3}）".format(
                d.get("device", ""), d.get("object", ""), d.get("statement", ""),
                d.get("caused_by", "")))
    gaps = answer.get("gaps") or []
    if gaps:
        lines.append("")
        lines.append("证据缺口：")
        for g in gaps:
            lines.append("  · {0}".format(g))
    lines.append("")
    lines.append("置信度：{0}".format(answer.get("confidence", "需人工确认")))
    return "\n".join(lines)


def _vote(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """自洽投票（F3）：按根因编码集合取多数票。并列时按签名字典序，绝不随机。"""
    if len(candidates) == 1:
        return candidates[0]
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        sig = "|".join(sorted("{0}/{1}".format(r.get("device"), r.get("object"))
                              for r in (c.get("root_causes") or [])))
        buckets.setdefault(sig, []).append(c)
    best = sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0]
    return best[1][0]


# ── 主流程 ────────────────────────────────────────────────────────────
def history_of(session_id: int, exclude_q_norm: str = "",
               limit: int = 6) -> Tuple[str, str]:
    """会话前缀：只带「问题 + 结论摘要」，不带时间戳、不带原始回显。

    历史必须是确定性的文本，否则它一进指纹，追问就永远命不中缓存。

    与当前问题相同的历史轮次**不带**：重复提问是「再问一遍」而不是追问，
    它的上下文若进指纹，单会话里连问 N 次就会 N 个指纹，一致性判据直接失效。
    排除之后，第 N 次重复提问与第 1 次的指纹相同 → F0 命中 → 字节一致。
    """
    rows = query("SELECT question, answer FROM turn WHERE session_id=?"
                 " ORDER BY seq DESC LIMIT ?", (session_id, limit * 2))
    if exclude_q_norm:
        rows = [r for r in rows
                if planner.normalize_question(r["question"]) != exclude_q_norm]
    rows = rows[:limit]
    parts: List[str] = []
    for r in reversed(rows):
        ans = loads(r["answer"], {})
        summary = (ans.get("summary") or "").strip()
        roots = "；".join("{0} {1}".format(x.get("device", ""), x.get("object", ""))
                          for x in (ans.get("root_causes") or []))
        parts.append("Q: {0}\nA: {1}{2}".format(
            r["question"], summary, "（根因：{0}）".format(roots) if roots else ""))
    text = "\n\n".join(parts)
    return text, (sha256_of(text) if text else "")


def ask(session_id: int, question: str, task_id: str = "",
        mode: str = "agent") -> Dict[str, Any]:
    """mode=agent 走多轮工具循环；mode=single 走单轮编排（更快、更省）。"""
    trace: List[Dict[str, str]] = []
    progress.step(task_id, "① 提取信息", "归一化提问、抽取 IP / 接口 / MAC")
    q_norm = planner.normalize_question(question)
    devices = [d["name"] for d in device_service.enabled_devices()]
    if not devices:
        trace.append({"step": "② 采集编排", "detail": "设备清单为空"})
        return _save(session_id, question, None,
                     {"steps": [], "plan_hash": "", "engine": "none"}, "", "",
                     {"summary": "设备清单为空，请先在「设备与拓扑」添加设备",
                      "root_causes": [], "derived": [], "gaps": ["无可用设备"],
                      "confidence": "需人工确认",
                      "text": "设备清单为空，请先在「设备与拓扑」添加设备。"},
                     "F4", trace)
    entities = planner.extract_entities(question, devices)
    history_text, history_hash = history_of(session_id, q_norm)
    if history_text:
        trace.append({"step": "① 会话上下文",
                      "detail": "带入前 {0} 轮的问题与结论摘要（进指纹，追问也确定）".format(
                          history_text.count("Q: "))})
    trace.append({"step": "① 提取信息",
                  "detail": "归一化提问 + 实体：{0}".format(
                      json.dumps(entities, ensure_ascii=False) or "无")})

    if mode == "agent":
        return _ask_agent(session_id, question, q_norm, devices, entities,
                          history_text, history_hash, trace, task_id)

    # ② 采集编排
    progress.step(task_id, "② 采集编排",
                  "AI 从知识库挑要下发的命令（{0} 台设备）".format(len(devices)))
    plan = planner.build_plan(question, devices, entities)
    trace.append({"step": "② 采集编排",
                  "detail": "{0} 条命令 × {1} 台设备（共 {2} 次下发），引擎 {3}{4}{5}".format(
                      len({s["command"] for s in plan["steps"]}), len(devices),
                      len(plan["steps"]), plan["engine"],
                      "（缓存命中）" if plan.get("cached") else "",
                      "，超上限截断 {0} 条".format(plan["dropped"])
                      if plan.get("dropped") else "")})
    if not plan["steps"]:
        return _save(session_id, question, None, plan, "", "",
                     {"summary": plan["error"], "root_causes": [], "derived": [],
                      "gaps": [plan["error"]], "confidence": "需人工确认",
                      "text": plan["error"]}, "F4", trace)

    # ③ 采集 + ④ 归一化
    progress.step(task_id, "③ 采集", "准备下发 {0} 条命令".format(len(plan["steps"])),
                  0, len(plan["steps"]))
    epoch = collect.collect(plan["steps"], plan["plan_hash"], plan["engine"], task_id)
    progress.step(task_id, "④ 归一化",
                  "擦除易变量 → snapshot {0}".format(short(epoch["snapshot_hash"], 12)))
    trace.append({"step": "③ 采集",
                  "detail": "成功 {0} 条，失败 {1} 条，纪元 #{2}".format(
                      epoch["ok"], epoch["failed"], epoch["epoch_id"])})
    trace.append({"step": "④ 归一化",
                  "detail": "擦除易变量后得到 snapshot_hash {0}".format(
                      short(epoch["snapshot_hash"], 16))})

    # ⑤ 指纹 + 兜底阶梯
    fp = P.fingerprint(q_norm, epoch["snapshot_hash"], llm.identity(),
                       kb.catalog_digest(), history_hash, "single")

    hit = frozen_get(fp)
    if hit:
        frozen_hit(fp)
        progress.step(task_id, "⑤ 兜底 F0", "指纹命中冻结答案，零模型调用")
        trace.append({"step": "⑤ 兜底 F0",
                      "detail": "指纹命中冻结答案，**零模型调用**，原样返回"})
        return _save(session_id, question, epoch, plan,
                     epoch["snapshot_hash"], fp, hit["answer"], "F0", trace,
                     {"system": P.SYSTEM, "user": P.build(q_norm, epoch["snapshot"]),
                      "raw": hit["answer"],
                      "meta": {"frozen": True, "model": hit.get("model", ""),
                               "note": "指纹命中，本轮未调用模型"}})

    same_snap = query_one(
        "SELECT fingerprint FROM frozen_answer WHERE snapshot_hash=? LIMIT 1",
        (epoch["snapshot_hash"],))
    if same_snap:
        trace.append({"step": "⑤ 兜底 F1",
                      "detail": "同一快照已有冻结答案（问法不同），事实一致，本次重新生成结论"})

    if not llm.available():
        progress.step(task_id, "⑤ 兜底 F4", "未配置 API Key，降级为证据陈述")
        trace.append({"step": "⑤ 兜底 F4",
                      "detail": "未配置 API Key，降级为证据陈述（不下结论）"})
        answer = evidence_statement(epoch["blocks"], epoch["failed"])
        return _save(session_id, question, epoch, plan,
                     epoch["snapshot_hash"], fp, answer, "F4", trace)

    # ⑥ 调 AI（k 次采样投票，F3）
    progress.step(task_id, "⑥ AI 诊断",
                  "{0} 正在分析证据快照…".format(settings.model()))
    content = P.build(q_norm, epoch["snapshot"])
    k = settings.vote_k()
    candidates: List[Dict[str, Any]] = []
    last_err = ""
    for i in range(k):
        res = llm.call_json("diagnose", P.SYSTEM, content, P.SCHEMA,
                            max_tokens=8000, use_cache=(i == 0))
        if res["ok"]:
            candidates.append(res["data"])
            if res.get("cached"):
                break
        else:
            last_err = res["error"]
            break

    if not candidates:
        trace.append({"step": "⑤ 兜底 F4",
                      "detail": "模型调用失败（{0}），降级为证据陈述".format(last_err)})
        answer = evidence_statement(epoch["blocks"], epoch["failed"])
        return _save(session_id, question, epoch, plan,
                     epoch["snapshot_hash"], fp, answer, "F4", trace)

    answer = _vote(candidates)
    if epoch["failed"]:
        # F5 缺证兜底
        answer.setdefault("gaps", []).append(
            "{0} 条命令未采到，结论覆盖面受限".format(epoch["failed"]))
        if answer.get("confidence") == "确认":
            answer["confidence"] = "高"
        trace.append({"step": "⑤ 兜底 F5",
                      "detail": "{0} 条未采到，已列入缺口并下调置信度".format(epoch["failed"])})

    level = "F3" if len(candidates) > 1 else "AI"
    trace.append({"step": "⑥ AI 诊断",
                  "detail": "模型 {0}，{1}".format(
                      settings.model(),
                      "{0} 次采样多数票".format(len(candidates)) if len(candidates) > 1
                      else "单次生成")})

    progress.step(task_id, "⑦ 冻结", "按指纹冻结答案，后续同一故障命中 F0")
    prompt_record = {
        "system": P.SYSTEM, "user": content, "raw": candidates[0],
        "meta": {"provider": settings.get("provider") or "anthropic",
                 "model": settings.model(), "samples": len(candidates),
                 "cache_key": (res or {}).get("cache_key", ""),
                 "cached": bool((res or {}).get("cached"))},
    }
    frozen_put(fp, q_norm, epoch["snapshot_hash"], answer, settings.auto_freeze())
    trace.append({"step": "⑦ 冻结",
                  "detail": "答案已按指纹冻结{0}，后续同一故障永远命中 F0".format(
                      "（自动）" if settings.auto_freeze() else "（待人工确认）")})
    return _save(session_id, question, epoch, plan,
                 epoch["snapshot_hash"], fp, answer, level, trace, prompt_record)


def _save(session_id: int, question: str,
          epoch: Optional[Dict[str, Any]], plan: Dict[str, Any],
          snapshot_hash: str, fingerprint: str, answer: Dict[str, Any],
          level: str, trace: List[Dict[str, str]],
          prompt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    answer = dict(answer)
    answer["text"] = render(answer)
    seq = (query_one("SELECT COALESCE(MAX(seq),0) s FROM turn WHERE session_id=?",
                     (session_id,))["s"] or 0) + 1
    tid = execute(
        "INSERT INTO turn(session_id, seq, question, epoch_id, plan,"
        " plan_hash, plan_engine, snapshot_hash, fingerprint, answer, fallback_level,"
        " trace, prompt_system, prompt_user, model_raw, model_meta, rounds, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, seq, question,
         epoch["epoch_id"] if epoch else None,
         json.dumps(plan.get("steps", []), ensure_ascii=False),
         plan.get("plan_hash", ""), plan.get("engine", ""), snapshot_hash, fingerprint,
         json.dumps(answer, ensure_ascii=False), level,
         json.dumps(trace, ensure_ascii=False),
         (prompt or {}).get("system", ""), (prompt or {}).get("user", ""),
         json.dumps((prompt or {}).get("raw"), ensure_ascii=False)
         if (prompt or {}).get("raw") is not None else "",
         json.dumps((prompt or {}).get("meta", {}), ensure_ascii=False),
         json.dumps((prompt or {}).get("rounds", []), ensure_ascii=False), _now()))
    if seq == 1:
        execute("UPDATE session SET title=? WHERE id=?", (question[:40], session_id))
    return _turn_row(query_one("SELECT * FROM turn WHERE id=?", (tid,)))


# ── 一致性验证 ────────────────────────────────────────────────────────
def consistency_check(question: str, rounds: int = 5, mode: str = "cross",
                      gap_ms: int = 1500) -> Dict[str, Any]:
    """赛题判据的直接检验。

    mode=cross  N 个全新会话各问一次   → 多会话交互
    mode=single 同一会话里连问 N 次     → 单会话多次交互

    每轮都**真实重新采集**，轮次之间留出间隔让设备状态漂移。
    原始回显必须变、快照哈希必须不变、正文必须逐字相同 —— 三者缺一，结论都不成立。
    """
    import time as _t
    rounds = max(2, min(rounds, 10))
    created: List[int] = []
    shared = create_session("一致性验证 · 单会话") if mode == "single" else None
    if shared:
        created.append(shared["id"])

    results: List[Dict[str, Any]] = []
    for i in range(rounds):
        if i > 0 and gap_ms > 0:
            _t.sleep(gap_ms / 1000.0)
        sess = shared or create_session("一致性验证 #{0}".format(i + 1))
        if shared is None:
            created.append(sess["id"])
        turn = ask(sess["id"], question)
        results.append({
            "round": i + 1, "session_id": sess["id"], "epoch_id": turn["epoch_id"],
            "snapshot_hash": turn["snapshot_hash"], "fingerprint": turn["fingerprint"],
            "fallback_level": turn["fallback_level"],
            "root_causes": len(turn["answer"].get("root_causes") or []),
            "text": turn["answer"].get("text", ""),
        })

    epochs = [r["epoch_id"] for r in results if r["epoch_id"]]
    drift = collect.drift(epochs[0], epochs[-1]) if len(epochs) >= 2 else {}
    for sid in created:
        delete_session(sid)

    snaps = {r["snapshot_hash"] for r in results}
    fps = {r["fingerprint"] for r in results}
    texts = {r["text"] for r in results}
    return {
        "question": question, "mode": mode,
        "rounds": len(results), "results": results, "raw_drift": drift,
        "distinct_snapshots": len(snaps), "distinct_fingerprints": len(fps),
        "byte_identical": len(texts) == 1,
        "ssr": 1.0 if len(snaps) == 1 else 0.0,
        "consistent": len(fps) == 1 and len(texts) == 1,
        "input_really_changed": bool(drift.get("changed", 0)),
    }


def summary() -> Dict[str, Any]:
    return {
        "sessions": query_one("SELECT COUNT(*) n FROM session")["n"],
        "turns": query_one("SELECT COUNT(*) n FROM turn")["n"],
        "frozen": query_one("SELECT COUNT(*) n FROM frozen_answer")["n"],
        "frozen_hits": query_one(
            "SELECT COALESCE(SUM(hit_count),0) n FROM frozen_answer")["n"],
        "ladder": [{"level": a, "name": b, "desc": c} for a, b, c in FALLBACK_LADDER],
    }


def ask_async(session_id: int, question: str, mode: str = "agent") -> str:
    """启动一次诊断并立刻返回 task_id，进度由 progress 总线实时上报。"""
    import threading
    task_id = progress.create()

    def worker() -> None:
        try:
            turn = ask(session_id, question, task_id, mode)
            progress.finish(task_id, turn=turn)
        except Exception as exc:
            progress.finish(task_id, error="{0}: {1}".format(type(exc).__name__, exc))

    threading.Thread(target=worker, daemon=True).start()
    return task_id


def _ask_agent(session_id: int, question: str, q_norm: str, devices: List[str],
               entities: Dict[str, Any], history_text: str, history_hash: str,
               trace: List[Dict[str, str]], task_id: str) -> Dict[str, Any]:
    """多轮工具循环模式：模型看一轮回显再决定下一步。"""
    topo = device_service.topology_context()
    from ..events import service as events_service
    events_text, events_digest = events_service.context_for(devices)
    if events_text:
        trace.append({"step": "① 设备事件",
                      "detail": "带入 {0} 类近期 syslog/trap 事件（进指纹）".format(
                          events_text.count("\n") + 1)})
    loop = agent.run_loop(q_norm, devices, entities, history_text, topo, task_id,
                          recent_events=events_text)

    for t in loop["transcript"]:
        trace.append({
            "step": "第 {0} 轮".format(t["round"]),
            "detail": "{0}{1}".format(
                t["thinking"],
                "　→ 下发 {0} 条命令".format(len(t["commands"])) if t["commands"]
                else "　→ 给出结论"),
        })

    plan = {"steps": [c for t in loop["transcript"] for c in t["commands"]],
            "plan_hash": "", "engine": "agent"}
    epoch_id = loop["epoch_id"]
    ep = collect.epoch(epoch_id) if epoch_id else None
    if ep:
        ep["epoch_id"] = ep["id"]
    snapshot_hash = ep["snapshot_hash"] if ep else ""
    trace.append({"step": "证据汇总",
                  "detail": "{0} 轮共采 {1} 条命令，snapshot {2}".format(
                      loop["rounds"], len(plan["steps"]), short(snapshot_hash, 12))})

    fp = P.fingerprint(q_norm, snapshot_hash, llm.identity(), kb.catalog_digest(),
                       history_hash, "agent", events_digest) if snapshot_hash else ""

    hit = frozen_get(fp) if fp else None
    if hit:
        frozen_hit(fp)
        trace.append({"step": "兜底 F0", "detail": "指纹命中冻结答案"})
        return _save(session_id, question, ep, plan, snapshot_hash, fp,
                     hit["answer"], "F0", trace,
                     {"system": (loop["prompts"][0]["system"]
                             if loop["prompts"] else agent.AGENT_SYSTEM), "user": "（指纹命中，未调用模型）",
                      "raw": hit["answer"], "meta": {"frozen": True},
                      "rounds": loop["prompts"]})

    answer = loop["answer"]
    if not answer:
        progress.step(task_id, "兜底 F4", loop.get("error") or "模型未给出结论")
        trace.append({"step": "兜底 F4",
                      "detail": loop.get("error") or "模型未给出结论，降级为证据陈述"})
        answer = evidence_statement(loop["blocks"], 0)
        level = "F4"
    else:
        level = "AI"
        if fp:
            progress.step(task_id, "冻结", "按指纹冻结答案")
            frozen_put(fp, q_norm, snapshot_hash, answer, settings.auto_freeze())

    last = loop["prompts"][-1] if loop["prompts"] else {}
    return _save(session_id, question, ep, plan, snapshot_hash, fp, answer, level, trace,
                 {"system": (loop["prompts"][0]["system"]
                             if loop["prompts"] else agent.AGENT_SYSTEM), "user": last.get("user", ""),
                  "raw": last.get("raw"),
                  "meta": {"mode": "agent", "rounds": loop["rounds"],
                           "provider": settings.get("provider") or "anthropic",
                           "model": settings.model()},
                  "rounds": loop["prompts"]})
