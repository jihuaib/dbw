"""会话历史：追问带上下文，重复提问不带自己的历史 —— 单会话一致性靠它。"""
from app.core.db import execute
from app.modules.diagnose import service as S


def _mk_session_with_turns():
    sess = S.create_session("hist 测试")
    for q, summary in (("LEAF2 出什么问题了", "GE-1 被关闭"),
                       ("为什么 OSPF 掉了", "邻居丢失")):
        execute(
            "INSERT INTO turn(session_id, seq, question, answer, trace,"
            " fallback_level, created_at) VALUES (?,?,?,?,?,?,?)",
            (sess["id"], 1, q,
             '{"summary": "%s", "root_causes": []}' % summary, "[]", "AI", "t"))
    return sess["id"]


def test_followup_carries_context():
    sid = _mk_session_with_turns()
    text, digest = S.history_of(sid, "追问：怎么修")
    assert "GE-1 被关闭" in text and "邻居丢失" in text
    assert digest


def test_repeat_question_excluded_from_history():
    from app.modules.collect.planner import normalize_question
    sid = _mk_session_with_turns()
    q = normalize_question("LEAF2 出什么问题了")
    text, _ = S.history_of(sid, q)
    # 自己的历史被排除，其余轮次保留
    assert "GE-1 被关闭" not in text
    assert "邻居丢失" in text


def test_repeat_fingerprint_stable():
    """连问 N 次：每次的会话前缀都一样 → 指纹一样 → F0 可命中。"""
    from app.modules.collect.planner import normalize_question
    sid = _mk_session_with_turns()
    q = normalize_question("为什么 OSPF 掉了")
    h1 = S.history_of(sid, q)[1]
    # 再插入一轮同样的问题（模拟第 2 次提问已入库）
    execute(
        "INSERT INTO turn(session_id, seq, question, answer, trace,"
        " fallback_level, created_at) VALUES (?,?,?,?,?,?,?)",
        (sid, 3, "为什么 OSPF 掉了",
         '{"summary": "邻居丢失", "root_causes": []}', "[]", "F0", "t"))
    h2 = S.history_of(sid, q)[1]
    assert h1 == h2


def test_delete_session_cascades_but_keeps_shared():
    """删会话要连带清掉独占的纪元/冻结答案，但共享的必须保留。"""
    from app.modules.diagnose.service import delete_session
    from app.core.db import query_one

    s1 = S.create_session("待删")
    s2 = S.create_session("另一个")
    eid = execute("INSERT INTO epoch(devices, plan, plan_hash, plan_engine,"
                  " created_at) VALUES ('[]','[]','','agent','t')")
    execute("INSERT INTO capture(epoch_id, device, command, ok, error, raw_text,"
            " raw_sha, norm_text, norm_sha, applied, created_at)"
            " VALUES (?,?,?,1,'','x','h','x','h','[]','t')", (eid, "L", "show"))
    execute("INSERT INTO frozen_answer(fingerprint, question_norm, snapshot_hash,"
            " answer, verified, hit_count, created_at)"
            " VALUES ('fp-solo','q','s','{}',0,0,'t')")
    execute("INSERT INTO frozen_answer(fingerprint, question_norm, snapshot_hash,"
            " answer, verified, hit_count, created_at)"
            " VALUES ('fp-shared','q','s','{}',0,0,'t')")
    execute("INSERT INTO turn(session_id, seq, question, answer, trace, epoch_id,"
            " fingerprint, fallback_level, created_at)"
            " VALUES (?,1,'q','{}','[]',?, 'fp-solo','AI','t')", (s1["id"], eid))
    execute("INSERT INTO turn(session_id, seq, question, answer, trace,"
            " fingerprint, fallback_level, created_at)"
            " VALUES (?,1,'q','{}','[]','fp-shared','F0','t')", (s1["id"],))
    execute("INSERT INTO turn(session_id, seq, question, answer, trace,"
            " fingerprint, fallback_level, created_at)"
            " VALUES (?,1,'q','{}','[]','fp-shared','F0','t')", (s2["id"],))

    delete_session(s1["id"])
    assert query_one("SELECT id FROM epoch WHERE id=?", (eid,)) is None
    assert query_one("SELECT epoch_id FROM capture WHERE epoch_id=?", (eid,)) is None
    assert query_one("SELECT fingerprint FROM frozen_answer"
                     " WHERE fingerprint='fp-solo'") is None
    # 另一个会话还引用着 fp-shared —— 不许动
    assert query_one("SELECT fingerprint FROM frozen_answer"
                     " WHERE fingerprint='fp-shared'") is not None
    delete_session(s2["id"])
