# -*- coding: utf-8 -*-
"""
M5 验收：会议纪要 + 未答好的问题清单

场景 A：真开一场会（走 HTTP，一路"跳过"），散会后验证三件事
        1. SSE 的 meeting_end 里带齐了纪要正文和未答好清单
        2. 纪要落进了 MySQL（review_question 行数、字段、会话状态）
        3. GET /review/session/{id} 能原样读回来，且清单按严重度排序

场景 B：DAO 层单独验（不调模型）
        合成一份记录直接写库再读回，逐字段比对 —— 落库最容易犯的错是漏字段，
        比如交叉质询的 target_speaker、追问的 followup_depth，漏了页面上就少一截
        顺带验"同一场会重开一次是替换不是叠加"
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from app.ai.agent.review_agent.node.judge_node import collect_unresolved  # noqa: E402
from app.ai.agent.review_agent.node.speaker_node import REVIEWER_NAMES  # noqa: E402
from app.ai.tool.review_dao import get_questions, get_session, save_questions  # noqa: E402
from app.ai.utils.mysql_util import get_mysql_conn  # noqa: E402
from app.main import app  # noqa: E402

REPORT = os.path.join(ROOT, "data", "_m5_check.txt")
PLAN = os.path.join(ROOT, "data", "demo_plan.txt")
# 比对时要和落库字段对齐的列。ctype 这类只喂模型用的中间字段不落库，所以不在名单里
DB_FIELDS = ["round", "speaker_role", "question_type", "target_speaker", "question",
             "student_answer", "verdict", "verdict_comment", "severity", "followup_depth"]
STUDENT_ID = "check_m5"

_lines = []


def log(text=""):
    _lines.append(str(text))
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    print(str(text)[:200].encode("ascii", "replace").decode("ascii"))


def read_sse(client, path, payload):
    events = []
    with client.stream("POST", path, json=payload) as resp:
        status = resp.status_code
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            events.append(json.loads(line[6:]))
    return status, events


def last_real(events):
    for e in reversed(events):
        if e.get("event") != "done":
            return e.get("event")
    return ""


def db_count(session_id):
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute("select count(*) from review_question where session_id=%s", (session_id,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def cleanup(*session_ids):
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        for sid in session_ids:
            cur.execute("delete from review_question where session_id=%s", (sid,))
            cur.execute("delete from review_session where session_id=%s", (sid,))
        conn.commit()
    finally:
        conn.close()


# ---------------- 场景 A ----------------
def scenario_a(plan_text):
    log("\n" + "-" * 70)
    log("场景 A：开完一场会，验纪要落库与读回")
    log("-" * 70)

    session_id = ""
    with TestClient(app) as client:
        resp = client.post("/review/submit",
                           json={"plan_text": plan_text, "student_id": STUDENT_ID})
        if resp.status_code != 200:
            log(f"  提交方案失败：{resp.status_code} {resp.text[:200]}")
            return False
        session_id = resp.json()["session_id"]
        log(f"  会话ID：{session_id}")

        events = []
        _, first = read_sse(client, "/review/meeting/start",
                            {"session_id": session_id, "max_round": 1})
        events += first

        # 一路跳过，把所有议题走完
        guard = 0
        while last_real(events) == "await_answer" and guard < 20:
            guard += 1
            _, more = read_sse(client, "/review/meeting/answer",
                               {"session_id": session_id, "skip": True})
            events += more
        log(f"  共 {guard + 1} 次请求")

        end = next((e for e in events if e.get("event") == "meeting_end"), None)
        if not end:
            log("  没有收到 meeting_end，会议没开完")
            return False
        sse_log = end.get("question_log") or []
        sse_unresolved = end.get("unresolved") or []

        # 落库是散会那一刻做的，读回必须和事件里的一致
        back = client.get(f"/review/session/{session_id}")
        log(f"  GET /review/session/{{id}} → {back.status_code}")
        data = back.json() if back.status_code == 200 else {}

    log("\n  会议纪要（时间线）：")
    for i, m in enumerate(sse_log, start=1):
        who = REVIEWER_NAMES.get(m.get("speaker_role"), m.get("speaker_role"))
        tag = {"cross": "⟶ 接话", "followup": "↳ 追问"}.get(m.get("question_type"), "主问题")
        log(f"    {i}. 【{who}】{tag}：{m.get('question')}")
        if m.get("student_answer"):
            log(f"         学生：{m['student_answer']}")
        if m.get("verdict"):
            log(f"         判定：{m['verdict']}（严重度 {m.get('severity')}）")

    log("\n  未答好的问题清单：")
    for i, u in enumerate(sse_unresolved, start=1):
        who = REVIEWER_NAMES.get(u.get("speaker_role"), u.get("speaker_role"))
        log(f"    [{u.get('severity')}] {who}：{u.get('question')}")

    log("\n  检查项：")
    ok_event = bool(sse_log) and isinstance(sse_unresolved, list)
    log(f"    meeting_end 带纪要正文与清单：{'通过' if ok_event else '不通过'}"
        f"（{len(sse_log)} 条记录 / {len(sse_unresolved)} 个未答好）")

    rows = db_count(session_id)
    ok_db = rows == len(sse_log)
    log(f"    质询记录落库条数一致（库 {rows} / 事件 {len(sse_log)}）：{'通过' if ok_db else '不通过'}")

    ok_status = data.get("status") == "finished"
    log(f"    会话状态改成 finished（实际 {data.get('status')}）：{'通过' if ok_status else '不通过'}")

    db_log = data.get("question_log") or []
    mismatch = []
    for a, b in zip(sse_log, db_log):
        for k in DB_FIELDS:
            left = a.get(k, "" if isinstance(b.get(k), str) else 0)
            if str(left) != str(b.get(k)):
                mismatch.append((k, left, b.get(k)))
    ok_fields = len(db_log) == len(sse_log) and not mismatch
    log(f"    读回的字段与发言记录逐条一致（{len(db_log)} 条）：{'通过' if ok_fields else '不通过'}")
    if mismatch:
        log(f"      不一致：{mismatch[:5]}")

    db_unresolved = data.get("unresolved") or []
    ok_unres = db_unresolved == sse_unresolved
    log(f"    读回的未答好清单与散会时一致：{'通过' if ok_unres else '不通过'}")

    sev = [u.get("severity", 0) for u in sse_unresolved]
    ok_sort = sev == sorted(sev, reverse=True)
    log(f"    清单按严重度降序（{sev}）：{'通过' if ok_sort else '不通过'}")

    # 清单要能直接对上记录：每一条都必须是某条判定非 resolved 的问题
    questions = {m.get("question") for m in sse_log if m.get("verdict")}
    ok_ref = all(u.get("question") in questions for u in sse_unresolved)
    log(f"    清单每条都能在纪要里找到出处：{'通过' if ok_ref else '不通过'}")

    # 交叉质询和追问这两类字段最容易漏，单独点一下
    has_cross = any(m.get("question_type") == "cross" for m in db_log)
    cross_ok = (not has_cross) or all(
        m.get("target_speaker") for m in db_log if m.get("question_type") == "cross"
    )
    log(f"    交叉质询的 target_speaker 落库了（本场 {'有' if has_cross else '无'}接话）："
        f"{'通过' if cross_ok else '不通过'}")

    cleanup(session_id)
    log(f"\n  已清理测试数据：{session_id}")
    return all([ok_event, ok_db, ok_status, ok_fields, ok_unres, ok_sort, ok_ref, cross_ok])


# ---------------- 场景 B ----------------
def scenario_b():
    log("\n" + "-" * 70)
    log("场景 B：DAO 层单独验（不调模型）")
    log("-" * 70)

    sid = "check_m5_dao"
    sample = [
        {"round": 1, "speaker_role": "tech", "question_type": "main", "target_speaker": "",
         "question": "识别错了怎么办？", "student_answer": "（跳过）",
         "verdict": "unresolved", "verdict_comment": "学生没有回答这个问题",
         "severity": 4, "followup_depth": 0},
        {"round": 1, "speaker_role": "cost", "question_type": "main", "target_speaker": "",
         "question": "每月花多少钱？", "student_answer": "大概够吧",
         "verdict": "partial", "verdict_comment": "没给测算依据",
         "severity": 3, "followup_depth": 0},
        {"round": 1, "speaker_role": "tech", "question_type": "cross", "target_speaker": "cost",
         "question": "按你说的规模，成本撑得住吗？", "student_answer": "",
         "verdict": "", "verdict_comment": "", "severity": 5, "followup_depth": 0},
        {"round": 1, "speaker_role": "cost", "question_type": "followup", "target_speaker": "",
         "question": "按 5000 人算，每月接口费用是多少？", "student_answer": "没算过",
         "verdict": "unresolved", "verdict_comment": "仍然没有给出数字",
         "severity": 4, "followup_depth": 1},
    ]

    n = save_questions(sid, sample)
    back = get_questions(sid)
    log(f"\n  写入 {n} 条，读回 {len(back)} 条")

    log("  逐字段比对：")
    ok_fields = len(back) == len(sample)
    for i, (a, b) in enumerate(zip(sample, back), start=1):
        for k in DB_FIELDS:
            if str(a.get(k, "")) != str(b.get(k, "")):
                ok_fields = False
                log(f"    ✗ 第 {i} 条 {k}：写 {a.get(k)!r} 读 {b.get(k)!r}")
    log(f"    全部字段一致：{'通过' if ok_fields else '不通过'}")

    # 交叉质询的 target_speaker、追问的 followup_depth 这两列单独点出来，
    # 它们最容易在 insert 语句里被漏掉
    ok_cross = back[2]["target_speaker"] == "cost"
    log(f"    交叉质询的 target_speaker：{'通过' if ok_cross else '不通过'}（{back[2]['target_speaker']!r}）")
    ok_depth = back[3]["followup_depth"] == 1
    log(f"    追问的 followup_depth：{'通过' if ok_depth else '不通过'}（{back[3]['followup_depth']}）")

    # 清单从记录推出来：两个议题各自都没最终解决，所以是 2 条，严重度取议题内最高
    unresolved = collect_unresolved(back)
    ok_derive = len(unresolved) == 2 and all(u["severity"] == 4 for u in unresolved)
    log(f"    清单归拢（2 个议题都没解决）：{'通过' if ok_derive else '不通过'}"
        f"，实际 {len(unresolved)} 条，严重度 {[u['severity'] for u in unresolved]}")
    ok_comment = bool(unresolved) and all(u["comment"] for u in unresolved)
    sample_comment = unresolved[0]["comment"] if unresolved else ""
    log(f"    清单带上判定理由：{'通过' if ok_comment else '不通过'}，例如 {sample_comment!r}")

    # 重开一场：记录应该是替换而不是叠加
    save_questions(sid, sample[:2])
    again = get_questions(sid)
    ok_replace = len(again) == 2 and db_count(sid) == 2
    log(f"    重开一场是替换不是叠加（应为 2 条，实际 {len(again)}）：{'通过' if ok_replace else '不通过'}")

    # 空日志也要能把旧记录清干净
    save_questions(sid, [])
    ok_clear = db_count(sid) == 0
    log(f"    写空日志能清掉旧记录：{'通过' if ok_clear else '不通过'}")

    ok_none = get_session(sid) is None
    log(f"    没提交过方案的会话读回来是 None：{'通过' if ok_none else '不通过'}")

    cleanup(sid)
    return all([ok_fields, ok_cross, ok_depth, ok_derive, ok_comment,
                ok_replace, ok_clear, ok_none])


def main():
    log("=" * 70)
    log("M5 验收：会议纪要 + 未答好的问题清单")
    log("=" * 70)

    with open(PLAN, encoding="utf-8") as f:
        plan_text = f.read()
    log(f"\n方案样本 {len(plan_text)} 字")

    a_ok = scenario_a(plan_text)
    b_ok = scenario_b()

    log("\n" + "=" * 70)
    log("M5 结论")
    log("=" * 70)
    log(f"  场景 A 纪要落库与读回：{'通过' if a_ok else '不通过'}")
    log(f"  场景 B DAO 字段与幂等  ：{'通过' if b_ok else '不通过'}")
    # 用退出码兜住结论：只看 exit code 的场合不能永远返回 0
    return a_ok and b_ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
