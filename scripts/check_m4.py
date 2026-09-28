# -*- coding: utf-8 -*-
"""
M4 验收：学生回答 + 追问

场景 A：走真实接口，一路"跳过"
        验证三件事：会议能跨请求续接、跳过的题不再追问、未答好清单记得住
场景 B：图直接跑，给一个答不到点上的回答
        验证追问机制真的会开火，而且**层数封顶 1 层**
场景 C：不调模型的纯函数检查
        调度决策表（manager 走到每个阶段去哪）+ 未答好清单的归拢规则

场景 C 是故意留的：A/B 依赖模型输出，会随模型心情波动，
但"最多追一层""跳过不追问""议题被追问答好了就不算未答好"这几条是硬规则，
必须每次都能确定地验出来
"""
import asyncio
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END  # noqa: E402

from app.ai.agent.review_agent.graph.review_graph import ReviewGraph  # noqa: E402
from app.ai.agent.review_agent.node.judge_node import SKIP_MARK, collect_unresolved  # noqa: E402
from app.ai.agent.review_agent.node.manager_node import (  # noqa: E402
    MAX_FOLLOWUP,
    manager_node,
)
from app.ai.agent.review_agent.node.speaker_node import REVIEWER_NAMES  # noqa: E402
from app.main import app  # noqa: E402

REPORT = os.path.join(ROOT, "data", "_m4_check.txt")
PLAN = os.path.join(ROOT, "data", "demo_plan.txt")
# 一句典型的"说了等于没说"，用来逼出追问
VAGUE_ANSWER = "这个我们后面会完善的，团队经验也比较足，您放心。"

_lines = []


def log(text=""):
    _lines.append(str(text))
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    print(str(text)[:200].encode("ascii", "replace").decode("ascii"))


def show_log(question_log):
    for i, item in enumerate(question_log, start=1):
        who = REVIEWER_NAMES.get(item["speaker_role"], item["speaker_role"])
        kind = item["question_type"]
        if kind == "cross":
            target = REVIEWER_NAMES.get(item["target_speaker"], item["target_speaker"])
            head = f"  {i}. 【{who}】⟶ 接话 → {target}"
        elif kind == "followup":
            head = f"  {i}. 【{who}】追问（第 {item.get('followup_depth', 0)} 层）"
        else:
            head = f"  {i}. 【{who}】主问题"
        log(head)
        log(f"       {item['question']}")
        answer = (item.get("student_answer") or "").strip()
        if answer:
            log(f"       学生：{answer[:60]}")
        if item.get("verdict"):
            log(f"       判定：{item['verdict']}（严重度 {item.get('severity', 0)}）")


# ---------------- 走 SSE 的两个小工具 ----------------
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


# ---------------- 场景 A ----------------
def scenario_a(plan_text):
    log("\n" + "-" * 70)
    log("场景 A：走真实接口，一路「跳过」（跨请求续接 + 跳过不追问）")
    log("-" * 70)

    events = []
    asks = []
    with TestClient(app) as client:
        status, first = read_sse(client, "/review/meeting/start",
                                 {"plan_text": plan_text, "max_round": 1})
        log(f"  /meeting/start 状态码：{status}")
        events += first
        session_id = next((e.get("session_id") for e in first if e.get("session_id")), "")

        guard = 0
        while last_real(events) == "await_answer" and guard < 20:
            guard += 1
            ask = next(e for e in reversed(events) if e.get("event") == "await_answer")
            asks.append(ask)
            log(f"  第 {guard} 问【{ask.get('name')}】：{ask.get('question')}")
            _, more = read_sse(client, "/review/meeting/answer",
                               {"session_id": session_id, "skip": True})
            events += more

        # 散场之后再交回答，和拿一个不存在的 session_id 来交，都必须被拦住。
        # 拦不住的话图会凭空开一场要素表为空的会，或者把回答接到过期状态上
        bad_codes = []
        for title, sid in [("不存在的会话", "no-such-meeting"),
                           ("已经散场的会话", session_id)]:
            code, _ = read_sse(client, "/review/meeting/answer",
                               {"session_id": sid, "skip": True})
            bad_codes.append((title, code))
            log(f"  {title} 交回答 → {code}")

    kinds = [e.get("event") for e in events]
    end = next((e for e in events if e.get("event") == "meeting_end"), None)
    qlog = (end or {}).get("question_log") or []
    unresolved = (end or {}).get("unresolved") or []

    log(f"\n  共 {guard + 1} 次请求，{len(events)} 个事件")
    log(f"  类型统计：{dict(Counter(kinds))}")
    log("\n  发言记录：")
    show_log(qlog)

    log("\n  检查项：")
    ok_multi = guard >= 1
    log(f"    会议真的分了多次请求（跨请求续接）：{'通过' if ok_multi else '不通过'}"
        f"，一共问了 {guard} 次")

    ok_ask = bool(asks) and all(a.get("question") and a.get("name") for a in asks)
    log(f"    每次停下来都带齐「谁在问、问的什么」：{'通过' if ok_ask else '不通过'}")

    mains = [q for q in qlog if q["question_type"] == "main"]
    ok_main = len(mains) == 4
    log(f"    4 位评审各提一个主问题（实际 {len(mains)}）：{'通过' if ok_main else '不通过'}")

    crosses = [q for q in qlog if q["question_type"] == "cross"]
    ok_answered = all(q.get("verdict") and (q.get("student_answer") or "").strip() for q in crosses)
    log(f"    接话也被逐条问过、判过了（{len(crosses)} 条接话）："
        f"{'通过' if ok_answered else '不通过'}"
        + ("" if ok_answered else "，" + str([(q.get("question"), q.get("verdict")) for q in crosses])))

    followups = [q for q in qlog if q["question_type"] == "followup"]
    ok_skip = not followups
    log(f"    跳过的题不再追问（追问条目 {len(followups)} 条）：{'通过' if ok_skip else '不通过'}")

    judged = [q for q in qlog if q.get("verdict")]
    ok_judged = len(judged) == len([q for q in qlog if q["question_type"] != "followup"]) + len(followups)
    log(f"    每个问题都判过（{len(judged)}/{len(qlog)}）：{'通过' if ok_judged else '不通过'}")

    ok_verdict = all(q.get("verdict") == "unresolved" for q in judged)
    log(f"    跳过的都记成 unresolved：{'通过' if ok_verdict else '不通过'}"
        f"，实际 {sorted({q.get('verdict') for q in judged})}")

    ok_answer = all((q.get("student_answer") or "").startswith(SKIP_MARK) for q in judged)
    log(f"    学生点的「跳过」被记进记录：{'通过' if ok_answer else '不通过'}")

    # 依次回答之后，本议题里每位提问者各占清单一条（都跳过了，所以一条不少）
    expect_unres = len(mains) + len(crosses)
    ok_unres = len(unresolved) == expect_unres
    log(f"    未答好清单按提问者逐条记（{len(unresolved)}/{expect_unres}）："
        f"{'通过' if ok_unres else '不通过'}")

    # 这条是本次改动的重点：以前接话问了没人答，也进不了清单
    cross_questions = {q["question"] for q in crosses}
    ok_cross_in = bool(crosses) and any(u["question"] in cross_questions for u in unresolved)
    log(f"    接话问题进了未答好清单：{'通过' if ok_cross_in else '不通过'}"
        + ("" if crosses else "（本场没有接话，跳过）"))
    if not crosses:
        ok_cross_in = True

    ok_end = bool(end) and kinds and kinds[-1] == "done"
    log(f"    会议正常收尾：{'通过' if ok_end else '不通过'}")

    ok_guard = all(code in (404, 409) for _, code in bad_codes)
    log(f"    乱交回答被拦住（404 / 409）：{'通过' if ok_guard else '不通过 ' + str(bad_codes)}")

    return all([ok_multi, ok_ask, ok_main, ok_answered, ok_skip, ok_judged, ok_verdict,
                ok_answer, ok_unres, ok_cross_in, ok_unres, ok_end, ok_guard])


# ---------------- 场景 B ----------------
async def scenario_b(plan_text):
    log("\n" + "-" * 70)
    log("场景 B：答不到点上，验证追问机制（关掉接话，专心看追问）")
    log("-" * 70)

    graph = ReviewGraph(InMemorySaver())
    # 关掉接话：追问的判定输入里多一句接话会让结果更飘，先单独验追问这条路
    _, state = await graph.run_to_end(plan_text, "m4-b", answer=VAGUE_ANSWER,
                                      max_round=1, max_cross_total=0)
    qlog = state.get("question_log") or []

    log(f"\n  学生统一回答：{VAGUE_ANSWER}")
    log("\n  发言记录：")
    show_log(qlog)

    mains = [q for q in qlog if q["question_type"] == "main"]
    followups = [q for q in qlog if q["question_type"] == "followup"]

    log("\n  检查项：")
    log(f"    出现追问：{'是，共 ' + str(len(followups)) + ' 次' if followups else '没有（模型认为都答清楚了）'}")

    ok_cap = all((q.get("followup_depth", 0) or 0) <= MAX_FOLLOWUP for q in qlog)
    log(f"    追问层数不超过 {MAX_FOLLOWUP} 层：{'通过' if ok_cap else '不通过'}")

    # 每个议题最多一条追问：按主问题分段统计
    per_issue = 0
    issue_max = 0
    for q in qlog:
        if q["question_type"] == "main":
            issue_max = max(issue_max, per_issue)
            per_issue = 0
        elif q["question_type"] == "followup":
            per_issue += 1
    issue_max = max(issue_max, per_issue)
    ok_one = issue_max <= MAX_FOLLOWUP
    log(f"    同一议题最多追 {MAX_FOLLOWUP} 次（实际最多 {issue_max}）：{'通过' if ok_one else '不通过'}")

    ok_answered = all((q.get("student_answer") or "").strip() for q in mains if q.get("verdict"))
    log(f"    主问题的回答都记下来了：{'通过' if ok_answered else '不通过'}")

    ok_done = state.get("meeting_phase") == "done"
    log(f"    会议正常结束：{'通过' if ok_done else '不通过，停在 ' + str(state.get('meeting_phase'))}")

    ok_unres = len(state.get("unresolved") or []) == len(mains)
    log(f"    未答好清单每议题一条（{len(state.get('unresolved') or [])}/{len(mains)}）："
        f"{'通过' if ok_unres else '不通过'}")

    return all([ok_cap, ok_one, ok_answered, ok_done, ok_unres])


# ---------------- 场景 C ----------------
def scenario_c():
    log("\n" + "-" * 70)
    log("场景 C：不调模型的硬规则检查（调度决策表 + 未答好清单归拢）")
    log("-" * 70)

    def entry(role, kind, verdict="", sev=0, q="q", hint="", answer="", comment=""):
        return {"question_type": kind, "speaker_role": role, "question": q,
                "student_answer": answer, "verdict": verdict, "verdict_comment": comment,
                "severity": sev, "followup_depth": 0, "followup_hint": hint,
                "target_speaker": "", "round": 1}

    main_j = entry("tech", "main", "unresolved", 4, q="主问题", hint="按多少用户算的")
    cross_u = entry("cost", "cross", q="接话的问题")

    base = {"plan_elements": {"goal": "做个东西"}, "speaker_order": ["tech", "cost"],
            "speaker_index": 0, "round": 1, "max_round": 1, "question_log": []}

    cases = [
        ("没要素表先去抽取", dict(base, plan_elements={}, meeting_phase="main"), "extract"),
        ("主问题阶段去发言", dict(base, meeting_phase="main"), "speaker"),
        ("追问阶段也去发言", dict(base, meeting_phase="followup"), "speaker"),
        ("等学生回答：图停住", dict(base, meeting_phase="await_answer"), END),
        ("学生答完去判定", dict(base, meeting_phase="judge"), "judge"),
        # 交替回答的核心：接话环节结束后，停在本议题第一条**还没判过**的问题上
        ("接话判定做过了 -> 等学生答主问题",
         dict(base, meeting_phase="cross", cross_checked=True, question_log=[main_j, cross_u]), END),
        # 主问题答完，接话还没答 -> 接着问接话
        ("主问题答完了 -> 接着问接话",
         dict(base, meeting_phase="advance", question_log=[main_j, cross_u]), END),
        ("都答清楚了 -> 换下一位",
         dict(base, meeting_phase="advance",
              question_log=[entry("tech", "main", "resolved", 1)]), "speaker"),
        ("没答好且有追问方向 -> 让提问者追一层",
         dict(base, meeting_phase="advance", question_log=[main_j]), "speaker"),
        ("已经追过一层了 -> 换下一位，不再追",
         dict(base, meeting_phase="advance", followup_depth=1,
              question_log=[main_j, entry("tech", "followup", "unresolved", 3, q="追问")]), "speaker"),
        ("不认识的阶段也要散会", dict(base, meeting_phase="???"), END),
    ]

    log("")
    log_ok = True
    for title, state, expect in cases:
        got = manager_node(state).goto
        ok = got == expect
        log_ok = log_ok and ok
        log(f"    {'✓' if ok else '✗'} {title}：期望 {expect}，实际 {got}")

    # 上表里"追一层"和"换下一位"的 goto 都是 speaker，得再挖一层看 update 才能分清
    upd = manager_node(dict(base, meeting_phase="advance",
                            question_log=[main_j])).update or {}
    ok_follow = (upd.get("pending_type") == "followup"
                 and upd.get("current_speaker") == "tech"
                 and upd.get("followup_hint") == "按多少用户算的")
    log(f"\n    要追问时：谁问的谁追、方向带对 -> {'通过' if ok_follow else '不通过'}（{upd}）")
    log_ok = log_ok and ok_follow

    # 该答接话时，pending_* 必须指到接话那条上，不能还停在主问题
    upd2 = manager_node(dict(base, meeting_phase="advance",
                             question_log=[main_j, cross_u])).update or {}
    ok_next = (upd2.get("pending_question") == "接话的问题"
               and upd2.get("pending_from") == "cost"
               and upd2.get("meeting_phase") == "await_answer")
    log(f"    轮到接话时：pending 指到接话那条 -> {'通过' if ok_next else '不通过'}（{upd2}）")
    log_ok = log_ok and ok_next

    # 换人时该清的当前议题状态必须清干净，否则新问题会沿用上一题的标记
    cmd = manager_node(dict(base, meeting_phase="advance", followup_depth=1,
                            pending_question="上一题", pending_from="tech",
                            student_answer="上次的回答", cross_in_issue=1,
                            question_log=[entry("tech", "main", "resolved", 1)]))
    upd3 = cmd.update or {}
    clean_keys = ["followup_depth", "pending_question", "pending_from", "student_answer",
                  "cross_in_issue", "cross_checked", "spoke_in_issue", "pending_type"]
    dirty = [k for k in clean_keys if k not in upd3]
    log(f"\n    换议题时清空当前议题状态：{'通过' if not dirty else '不通过，漏清 ' + str(dirty)}")
    log(f"      换到第 {upd3.get('speaker_index')} 位，阶段 {upd3.get('meeting_phase')}")
    log_ok = log_ok and not dirty

    # 未答好清单的归拢规则
    rules = [
        ("答不上来 → 进清单", [entry("tech", "main", "unresolved", 4)], 1, 4),
        ("追问答好了 → 不进清单",
         [entry("tech", "main", "partial", 3), entry("tech", "followup", "resolved", 1)], 0, 0),
        ("追问还是没答好 → 进清单，取最严重度",
         [entry("tech", "main", "partial", 4), entry("tech", "followup", "unresolved", 2)], 1, 4),
        ("还没判过 → 不进清单", [entry("tech", "main", "")], 0, 0),
        # 一个议题里两人提问，只有一人没答好 -> 只记他（这次改动的重点）
        ("一个议题两人问、只有一人没答好 → 只记他",
         [entry("tech", "main", "resolved", 1), entry("cost", "cross", "unresolved", 5)], 1, 5),
        ("两个议题同一个评审 → 分开记",
         [entry("tech", "main", "unresolved", 2, q="第一题"),
          entry("cost", "main", "resolved", 1, q="第二题"),
          entry("tech", "main", "unresolved", 3, q="第三题")], 2, 3),
    ]

    log("")
    rule_ok = True
    for title, qlog, expect_n, expect_sev in rules:
        got = collect_unresolved(qlog)
        ok = len(got) == expect_n and (not got or got[0]["severity"] == expect_sev)
        rule_ok = rule_ok and ok
        detail = f"{len(got)} 条" + (f"，严重度 {got[0]['severity']}" if got else "")
        log(f"    {'✓' if ok else '✗'} {title}：期望 {expect_n} 条/严重度 {expect_sev}，实际 {detail}")

    return log_ok and rule_ok


async def main():
    log("=" * 70)
    log("M4 验收：学生回答 + 追问")
    log("=" * 70)

    with open(PLAN, encoding="utf-8") as f:
        plan_text = f.read()
    log(f"\n方案样本 {len(plan_text)} 字，追问上限 {MAX_FOLLOWUP} 层")

    a_ok = scenario_a(plan_text)
    b_ok = await scenario_b(plan_text)
    c_ok = scenario_c()

    log("\n" + "=" * 70)
    log("M4 结论")
    log("=" * 70)
    log(f"  场景 A 跨请求续接 + 跳过不追问：{'通过' if a_ok else '不通过'}")
    log(f"  场景 B 追问与层数封顶        ：{'通过' if b_ok else '不通过'}")
    log(f"  场景 C 硬规则（无模型）      ：{'通过' if c_ok else '不通过'}")
    # 用退出码兜住结论：只看 exit code 的场合不能永远返回 0
    return a_ok and b_ok and c_ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
