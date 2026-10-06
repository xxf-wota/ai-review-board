# -*- coding: utf-8 -*-
"""
索引化改造的离线验收：不开网页、不调任何模型，把一整场评审会开完并逐项检查

为什么要有这个脚本：
    状态里的问答正文改成"索引 + MySQL 正文"（详见 store/question_store.py）之后，
    光读代码不放心 —— 正文取错了、索引里的 id 和库里那条对不上、判定没回填、
    重开一场会把旧记录叠上来，这些都不会报错，只会安静地给出一份错的会议纪要。
    而本地模型和商业模型都不是随时可用（Ollama 没起、没有 API key 时谁都不通），
    所以这里把三个模型入口换成**假模型**：只验"状态机 + 存储"这条链路，不需要网络。

    真模型跑通链路的是 scripts/dry_run_demo.py（需要 Ollama）；
    部署前自检是 scripts/check_startup.py；页面不变式是 scripts/check_page.py。

检查项（全过退出 0，任何一项不过退出 1）：
    1. 状态里没有 messages / question_log / unresolved / minutes / diagnosis
    2. 发言一条就落库一条，索引里的 id 就是库里那条记录的主键
    3. 等学生回答时：索引里那条还带着问题原文（同步的 manager 要拿它去问学生）
    4. 判定按 pending_id 精确定位，学生回答 / 判定评语进库，索引里那条的原文被删掉
    5. 追问：question_type = followup、followup_depth = 1，正文同样在库里
    6. 接话：target_speaker 记的是被接话的那位，判定时确实取到了发言正文
    7. 散会事件里的 question_log 与库里逐条一致，unresolved 由同一批记录算出
    8. 同一 session 重开一场：旧记录被清掉，不会翻倍
    9. 索引里不留 ctype / student_answer / verdict_comment（正文一律不进状态）

用法：
    python scripts/check_review_index.py
"""
import asyncio
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REPORT = os.path.join(ROOT, "data", "_review_index_check.txt")

# 一份小而完整的要素表（字段与 PlanElementsSchema 对齐），让会议直接从"发言"开始
ELEMENTS = {
    "goal": "给校园二手教材做撮合，让学生少花钱、旧书不压箱底",
    "tech": ["FastAPI + MySQL", "Redis 存会话", "封面识别只取书名作者等字段"],
    "budget": "建设期 3 万元：服务器 0.8 万、识别调用 1.2 万、其余留作应急",
    "schedule": "第 1 月原型，第 2 月联调，第 3 月试点 2 个学院",
    "risks": ["开学两周峰值并发", "封面识别准确率"],
    "compliance": "只存书名作者等商品属性，不存原图，可随时删号删数据",
    "users": "本校 5000 名有教材流转需求的学生",
    "missing": ["教材品相分级标准"],
}
PLAN_TEXT = "（离线验收用的方案全文，会议走的是上面那份要素表）"

# 依次答三问：第一问故意答虚（逼出追问），第二问给数字，第三问给数字出处
ANSWERS = [
    "这个我们前期都考虑过了，后面会把方案做得更完善一些，细节还在细化。",
    "按每月 10 万次调用、每次约 0.1 元算，一个月约 1 万元，峰值月按 1.5 万元封顶。",
    "调用量是按 1200 份校园问卷里 62% 愿意卖书折算出来的，选课数据也能对上。",
]


class _FakeChunk:
    """流式片段：speaker 节点只关心 .content"""

    def __init__(self, content):
        self.content = content


class _FakeAgent:
    """假模型，同时冒充三种调用方式

    - speaker：astream(user_msg, stream_mode="messages") → (chunk, metadata)
    - cross  ：ainvoke → {"messages": [...]}，取最后一条的 content
    - judge  ：ainvoke → {"structured_response": <JudgeSchema>}
    """

    def __init__(self, state, kind):
        self.state = state
        self.kind = kind

    async def astream(self, user_msg, stream_mode=None):
        self.state["speaker"] += 1
        text = f"【假发言 {self.state['speaker']}】请给出这一点的数据依据？"
        for i in range(0, len(text), 5):
            yield _FakeChunk(text[i:i + 5]), {}

    async def ainvoke(self, user_msg):
        from app.ai.agent.review_agent.schema.review_schema import JudgeSchema

        if self.kind == "judge":
            self.state["judge"] += 1
            if self.state["judge"] == 1:
                # 第一次判定给 partial + 追问方向，用来把"追问"这条路径走通
                return {"structured_response": JudgeSchema(
                    verdict="partial", severity=4, need_followup=True,
                    followup_question="请补充这个数字的出处？",
                    comment="给了方向但没有依据")}
            return {"structured_response": JudgeSchema(
                verdict="resolved", severity=2, need_followup=False,
                followup_question="", comment="有依据，疑问解除")}
        self.state["cross_speak"] += 1
        return {"messages": [_FakeChunk(
            f"【假接话 {self.state['cross_speak']}】你说的口径和成本估算对不上，怎么解释？")]}


def install_fakes(state):
    """把三个模型入口换成假模型（只动节点模块里的名字，不碰业务代码）"""
    from app.ai.agent.review_agent.node import cross_node, judge_node, speaker_node

    def factory(kind):
        def make(**kwargs):
            return _FakeAgent(state, kind)
        return make

    async def fake_judge_cross(plan_elements, recent_rows, spoke_in_issue):
        # 接话判定要读"刚才的发言"，正文是 cross_node 按索引从库里取的
        state["cross_judge"] += 1
        state["cross_rows"] = len(recent_rows)
        state["cross_rows_ok"] = bool(recent_rows) and all(
            (r.get("question") or "").strip() for r in recent_rows)
        if state["cross_judge"] == 1:
            return [{"role": "cost", "want": True, "severity": 4,
                     "point": "成本口径没说清"}]
        return []

    async def fake_judge_answer(plan_elements, role, question, answer, issue_rows):
        # 判定节点把"这条问题原文"和"本议题问答"都取好了才调过来
        state["judge"] += 1
        state["judge_role"] = role
        state["judge_question"] = question
        state["judge_rows"] = len(issue_rows)
        if state["judge"] == 1:
            return {"verdict": "partial", "severity": 4,
                    "followup_question": "请补充这个数字的出处？",
                    "need_followup": True, "comment": "给了方向但没有依据"}
        return {"verdict": "resolved", "severity": 2, "followup_question": "",
                "need_followup": False, "comment": "有依据，疑问解除"}

    speaker_node.create_agent = factory("speaker")
    cross_node.create_agent = factory("cross")
    judge_node.create_agent = factory("judge")
    cross_node.judge_cross = fake_judge_cross
    judge_node.judge_answer = fake_judge_answer


async def collect(gen) -> list:
    out = []
    async for ev in gen:
        out.append(ev)
    return out


def dump(values: dict) -> str:
    return json.dumps(values, ensure_ascii=False, default=str)


async def run() -> tuple:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from app.ai.agent.review_agent.graph.review_graph import ReviewGraph
    from app.ai.agent.review_agent.store import question_store as store
    from app.ai.tool.review_dao import delete_questions, delete_session, get_questions

    state = {"speaker": 0, "judge": 0, "cross_judge": 0, "cross_speak": 0,
             "cross_rows": 0, "cross_rows_ok": False, "judge_question": "",
             "judge_role": "", "judge_rows": 0}
    install_fakes(state)

    session_id = "idxchk-" + uuid.uuid4().hex[:10]
    checks = []
    log = []

    def check(name, ok, note=""):
        checks.append((name, bool(ok), note))
        log.append(f"[{'通过' if ok else '不通过'}] {name}" + (f" —— {note}" if note else ""))
        return bool(ok)

    try:
        async with AsyncPostgresSaver.from_conn_string(os.getenv("POSTGRESQL_URL")) as saver:
            await saver.setup()
            graph = ReviewGraph(saver)

            # ---------- 第一场：跑到第一次等学生回答 ----------
            events = await collect(graph.start(PLAN_TEXT, session_id, max_round=1,
                                               plan_elements=dict(ELEMENTS)))
            start_ev = events[0] if events else {}
            await1 = next((e for e in events if e.get("event") == "await_answer"), {})
            snap1 = await graph.snapshot_values(session_id)
            rows1 = get_questions(session_id)

            log.append("")
            log.append("== 第一场：开场到第一次等回答 ==")
            log.append("事件：" + " → ".join(e.get("event", "?") for e in events))
            log.append("检查点状态：" + dump({k: v for k, v in snap1.items()
                                              if k != "plan_elements"}))
            log.append("库里记录：" + dump(rows1))

            check("开场事件是 meeting_start",
                  start_ev.get("event") == "meeting_start", start_ev.get("event"))
            check("状态里没有 messages / question_log / unresolved / minutes / diagnosis",
                  not ({"messages", "question_log", "unresolved", "minutes", "diagnosis"}
                       & set(snap1)))
            check("状态里换成了 session_id / pending_id / question_index",
                  snap1.get("session_id") == session_id and "pending_id" in snap1
                  and isinstance(snap1.get("question_index"), list))
            check("发言一条就落库一条（主问题），正文在库里",
                  len(rows1) == 2 and all((r.get("question") or "").strip() for r in rows1),
                  f"{len(rows1)} 条")
            check("索引里的 id 就是库里那条记录的主键",
                  [e.get("id") for e in snap1["question_index"]] == [r.get("id") for r in rows1],
                  dump([e.get("id") for e in snap1["question_index"]]))
            check("索引条目不夹带正文（没有 ctype / student_answer / verdict_comment）",
                  not any(("ctype" in e or "student_answer" in e or "verdict_comment" in e)
                          for e in snap1["question_index"]))
            check("接话记录带 target_speaker，指向被接话的那位",
                  any(r.get("question_type") == "cross" and r.get("target_speaker")
                      for r in rows1))
            check("等回答时，索引里那条还带着问题原文（同步的 manager 要用）",
                  any(e.get("id") == snap1.get("pending_id") and (e.get("question") or "")
                      for e in snap1["question_index"]))
            check("await_answer 事件带齐题号 / 总数 / 问题原文",
                  await1.get("question_index") == 1 and await1.get("issue_total") == 2
                  and bool(await1.get("question")),
                  f"{await1.get('question_index')}/{await1.get('issue_total')}")
            check("pending_id 指向索引里第一条未判的记录（不是随便一条）",
                  snap1.get("pending_id") == snap1["question_index"][0].get("id"),
                  str(snap1.get("pending_id")))
            check("接话判定确实取到了发言正文（正文不在状态里，是取回来的）",
                  state["cross_rows_ok"], f"{state['cross_rows']} 条")

            # ---------- 第二次请求：学生答第一条（主问题） ----------
            events = await collect(graph.resume(session_id, ANSWERS[0]))
            await2 = next((e for e in events if e.get("event") == "await_answer"), {})
            snap2 = await graph.snapshot_values(session_id)
            rows2 = get_questions(session_id)
            entry0 = snap2["question_index"][0]

            log.append("")
            log.append("== 第二次请求：答主问题（技术评审） ==")
            log.append("事件：" + " → ".join(e.get("event", "?") for e in events))
            log.append("判定拿到的记录数 / 问题：" + dump(
                {"rows": state["judge_rows"], "question": state["judge_question"]}))
            log.append("索引第一条：" + dump(entry0))
            log.append("库里第一条：" + dump(rows2[0] if rows2 else {}))

            check("判定按 pending_id 定位到的正是刚才那条问题",
                  state["judge_question"] == await1.get("question"),
                  state["judge_question"][:20])
            check("判定时取回了本议题问答（正文来自库里）",
                  state["judge_rows"] >= 2, f"{state['judge_rows']} 条")
            check("判定后索引里那条的原文被删掉（状态不留正文）",
                  "question" not in entry0)
            check("判定结果留在索引里（verdict / severity / followup_hint）",
                  entry0.get("verdict") == "partial" and entry0.get("severity")
                  and (entry0.get("followup_hint") or "").strip(),
                  dump({k: entry0.get(k) for k in ("verdict", "severity", "followup_hint")}))
            check("学生回答与判定评语写进了库里那条记录",
                  (rows2[0].get("student_answer") or "").strip() == ANSWERS[0]
                  and (rows2[0].get("verdict_comment") or "").strip()
                  and rows2[0].get("verdict") == "partial",
                  dump({k: rows2[0].get(k) for k in ("verdict", "verdict_comment")}))
            check("判定之后轮到本议题的第二条（接话）等学生回答",
                  await2.get("question_index") == 2 and await2.get("issue_total") == 2
                  and await2.get("question_type") == "cross",
                  f"{await2.get('question_type')} {await2.get('question_index')}/{await2.get('issue_total')}")

            # ---------- 第三次请求：答接话，逼出追问 ----------
            events = await collect(graph.resume(session_id, ANSWERS[1]))
            await3 = next((e for e in events if e.get("event") == "await_answer"), {})
            snap3 = await graph.snapshot_values(session_id)
            rows3 = get_questions(session_id)
            types3 = [e.get("question_type") for e in snap3["question_index"]]

            log.append("")
            log.append("== 第三次请求：答接话（应当被追问） ==")
            log.append("事件：" + " → ".join(e.get("event", "?") for e in events))
            log.append("索引题型：" + dump(types3))
            log.append("索引：" + dump(snap3["question_index"]))

            check("接话答完由接话的那位（cost）来判",
                  state["judge_role"] == "cost", state["judge_role"])
            check("出现了追问：question_type = followup",
                  "followup" in types3, dump(types3))
            follow = next((e for e in snap3["question_index"]
                           if e.get("question_type") == "followup"), {})
            check("追问带 followup_depth = 1，且原文在库里等取",
                  follow.get("followup_depth") == 1 and follow.get("id")
                  and any(r.get("id") == follow.get("id")
                          and (r.get("question") or "").strip() for r in rows3),
                  dump({"depth": follow.get("followup_depth"), "id": follow.get("id")}))
            check("追问也走 await_answer，而且不再触发接话",
                  await3.get("question_type") == "followup", await3.get("question_type"))

            # ---------- 剩下的问答一路跑完 ----------
            turn = 2
            while events and events[-1].get("event") == "await_answer" and turn < 12:
                events = await collect(graph.resume(session_id, ANSWERS[-1]))
                turn += 1
            end = events[-1] if events else {}
            rows_end = get_questions(session_id)

            log.append("")
            log.append("== 收尾 ==")
            log.append(f"共 {turn} 次学生回答请求")
            log.append("散会事件：" + dump({k: v for k, v in end.items()
                                            if k != "question_log"}))
            log.append("库里最终记录：" + dump(rows_end))

            check("会议正常散会（最后一个是 meeting_end）",
                  end.get("event") == "meeting_end", end.get("event"))
            check("散会事件的 question_log 与库里逐条一致（id 顺序都一样）",
                  [r.get("id") for r in (end.get("question_log") or [])]
                  == [r.get("id") for r in rows_end] and len(rows_end) == 6,
                  f"事件 {len(end.get('question_log') or [])} 条 / 库 {len(rows_end)} 条")
            check("散会事件里的记录都带完整正文（问题 / 回答 / 判定）",
                  all((r.get("question") or "").strip() and (r.get("student_answer") or "").strip()
                      and (r.get("verdict") or "").strip() for r in rows_end))
            check("未答好清单由同一批记录算出（不是状态里另存一份）",
                  isinstance(end.get("unresolved"), list)
                  and [x.get("speaker_role") for x in (end.get("unresolved") or [])]
                  == [x.get("speaker_role") for x in store.collect_unresolved(rows_end)],
                  dump(end.get("unresolved")))
            check("整场只接话一次（每个议题的额度生效）",
                  sum(1 for r in rows_end if r.get("question_type") == "cross") == 1)
            check("四类记录齐全（main / cross / followup 都在）",
                  {r.get("question_type") for r in rows_end} == {"main", "cross", "followup"},
                  dump(sorted({r.get("question_type") for r in rows_end})))

            # ---------- 重开同一场会：旧记录必须被清掉 ----------
            await collect(graph.start(PLAN_TEXT, session_id, max_round=1,
                                      plan_elements=dict(ELEMENTS)))
            rows_again = get_questions(session_id)
            old_questions = {r.get("question") for r in rows_end}
            log.append("")
            log.append("== 重开同一 session ==")
            log.append("重开后的库里记录：" + dump(rows_again))

            # 重开后应当只剩这一场刚问出来的记录（假模型的接话额度第一场已经用完，
            # 所以这里是 1 条主问题），关键是旧的 6 条一条都不能还在
            check("重开一场会先清掉旧记录（不会翻倍、旧问题不残留）",
                  rows_again and not ({r.get("question") for r in rows_again} & old_questions)
                  and len(rows_again) <= 2,
                  f"{len(rows_again)} 条，旧记录残留 "
                  f"{len({r.get('question') for r in rows_again} & old_questions)} 条")
            check("重开后的记录是全新的（新 id，不与上一场重叠）",
                  not ({r.get("id") for r in rows_again} & {r.get("id") for r in rows_end}),
                  dump(sorted(r.get("id") for r in rows_again)))
    finally:
        try:
            delete_questions(session_id)
            delete_session(session_id)
        except Exception as e:  # 清不干净不该盖掉验收结论，但也别假装没事
            log.append(f"清理测试数据失败：{e}")

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    log.append("")
    log.append(f"结论：{passed}/{total} 项通过")
    for name, ok, note in checks:
        if not ok:
            log.append(f"  ✗ {name}" + (f" —— {note}" if note else ""))
    return checks, log


def main() -> int:
    if sys.platform == "win32":
        # psycopg 的异步模式在 ProactorEventLoop 上直接报错，必须换 Selector
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    checks, log = asyncio.run(run())
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)

    header = [
        "评审会索引化改造 · 离线验收（假模型，不调任何模型 / 不需要网络）",
        "验证的是：状态机 + MySQL 正文存储 + 事件契约，端到端开完一整场评审会",
        "",
    ]
    text = "\n".join(header + log) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"index check: {passed}/{total} passed, report={REPORT}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
