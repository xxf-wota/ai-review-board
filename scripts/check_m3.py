# -*- coding: utf-8 -*-
"""
M3 验收：评审会的调度与交叉质询

场景 A：把接话关掉（max_cross_total=0），只验证调度 —— 四位评审是否按顺序各发言一次
场景 B：打开接话，验证是否真的出现"评审接评审的话"，以及次数是否受额度限制

分开验证是故意的：如果两个一起测，出问题时分不清是调度错了还是接话判定错了
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from app.ai.agent.review_agent.graph.review_graph import ReviewGraph  # noqa: E402
from app.ai.agent.review_agent.node.manager_node import (  # noqa: E402
    MAX_CROSS_PER_ISSUE,
    MAX_CROSS_TOTAL,
)
from app.ai.agent.review_agent.node.speaker_node import REVIEWER_NAMES  # noqa: E402

REPORT = os.path.join(ROOT, "data", "_m3_check.txt")
PLAN = os.path.join(ROOT, "data", "demo_plan.txt")

_lines = []


def log(text=""):
    _lines.append(str(text))
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    print(str(text)[:200].encode("ascii", "replace").decode("ascii"))


def show_log(question_log):
    """把发言记录按顺序打印出来，接话用箭头标出反驳对象"""
    for i, item in enumerate(question_log, start=1):
        who = REVIEWER_NAMES.get(item["speaker_role"], item["speaker_role"])
        kind = item["question_type"]
        if kind == "cross":
            target = REVIEWER_NAMES.get(item["target_speaker"], item["target_speaker"])
            head = f"  {i}. 【{who}】⟶ 接话 → {target}"
        else:
            head = f"  {i}. 【{who}】主问题"
        log(head)
        log(f"       {item['question']}")


async def scenario_a(plan_text):
    """场景 A：关掉接话，验证四位评审按顺序发言"""
    log("\n" + "-" * 70)
    log("场景 A：关掉接话（max_cross_total=0），验证调度")
    log("-" * 70)

    graph = ReviewGraph(InMemorySaver())
    state = await graph.run_get_state(plan_text, "m3-a", max_round=1, max_cross_total=0)
    qlog = state.get("question_log") or []

    log(f"\n  发言共 {len(qlog)} 条：")
    show_log(qlog)

    log("\n  检查项：")
    roles = [q["speaker_role"] for q in qlog]
    expect = ["tech", "cost", "compliance", "user"]
    ok_order = roles == expect
    log(f"    顺序为 tech/cost/compliance/user：{'通过' if ok_order else '不通过，实际 ' + str(roles)}")
    log(f"    每人恰好发言一次：{'通过' if len(roles) == 4 else '不通过，共 ' + str(len(roles)) + ' 条'}")
    cross_n = sum(1 for q in qlog if q["question_type"] == "cross")
    log(f"    没有接话（应为 0）：{'通过' if cross_n == 0 else '不通过，出现 ' + str(cross_n) + ' 次'}")
    log(f"    会议正常结束：{'通过' if state.get('meeting_phase') == 'done' else '不通过，停在 ' + str(state.get('meeting_phase'))}")
    empty = [q for q in qlog if not q["question"].strip()]
    log(f"    没有空问题：{'通过' if not empty else '不通过，有 ' + str(len(empty)) + ' 条空问题'}")
    return ok_order and len(roles) == 4 and cross_n == 0 and state.get("meeting_phase") == "done"


async def scenario_b(plan_text):
    """场景 B：打开接话，验证出现接话且受额度限制"""
    log("\n" + "-" * 70)
    log("场景 B：打开接话，验证交叉质询")
    log("-" * 70)

    graph = ReviewGraph(InMemorySaver())
    state = await graph.run_get_state(plan_text, "m3-b", max_round=1)
    qlog = state.get("question_log") or []

    log(f"\n  发言共 {len(qlog)} 条：")
    show_log(qlog)

    log("\n  检查项：")
    cross_items = [q for q in qlog if q["question_type"] == "cross"]
    log(f"    出现接话：{'通过，共 ' + str(len(cross_items)) + ' 次' if cross_items else '不通过，一次都没有'}")

    # 接话次数不能超过总上限
    over_total = len(cross_items) > MAX_CROSS_TOTAL
    log(f"    总次数不超上限 {MAX_CROSS_TOTAL}：{'通过' if not over_total else '不通过'}")

    # 每个议题最多接话 MAX_CROSS_PER_ISSUE 次（按 round+主问题分组数）
    main_n = sum(1 for q in qlog if q["question_type"] == "main")
    cap = main_n * MAX_CROSS_PER_ISSUE
    log(f"    每议题最多 {MAX_CROSS_PER_ISSUE} 次（共 {main_n} 个议题，上限 {cap}）："
        f"{'通过' if len(cross_items) <= cap else '不通过'}")

    # 接话必须指向真的发过言的人
    spoke_roles = {q["speaker_role"] for q in qlog if q["question_type"] == "main"}
    bad_target = [q for q in cross_items if q["target_speaker"] not in spoke_roles]
    log(f"    接话对象都是发过言的评审：{'通过' if not bad_target else '不通过 ' + str([q['target_speaker'] for q in bad_target])}")

    # 同一议题里不能同一个人连说两次
    log(f"    会议正常结束：{'通过' if state.get('meeting_phase') == 'done' else '不通过'}")

    return bool(cross_items) and not over_total and len(cross_items) <= cap and not bad_target


async def main():
    log("=" * 70)
    log("M3 验收：评审会调度 + 交叉质询")
    log("=" * 70)

    with open(PLAN, encoding="utf-8") as f:
        plan_text = f.read()
    log(f"\n方案样本 {len(plan_text)} 字")

    a_ok = await scenario_a(plan_text)
    b_ok = await scenario_b(plan_text)

    log("\n" + "=" * 70)
    log("M3 结论")
    log("=" * 70)
    log(f"  场景 A 调度：{'通过' if a_ok else '不通过'}")
    log(f"  场景 B 交叉质询：{'通过' if b_ok else '不通过'}")


if __name__ == "__main__":
    asyncio.run(main())
