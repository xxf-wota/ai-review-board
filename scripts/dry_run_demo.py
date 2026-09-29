# -*- coding: utf-8 -*-
"""
演示预跑：真的开一场评审会，把实际问到的题、每次接话、每条判定和结局都打出来

为什么要有这个脚本：
    答辩材料里写了"上台前一定用商业模型预跑 2~3 次，确认接话稳定出现"。
    这个脚本就是那次预跑 —— 它不开网页，直接驱动评审会图把整场开完，
    把过程落成一份文字记录，用来：
      1. 提前看清这场评审会到底会问什么，好准备回答稿（演示输入素材包）
      2. 提前确认"接话"会不会出现（上线前自检，用 --expect-cross 卡住）
      3. 现场万一翻车，手里有一份跑通的原始记录可比对

用法：
    python scripts/dry_run_demo.py                          # 默认跑 data/demo_plan.txt
    python scripts/dry_run_demo.py data/sample_plan_eldercare.txt
    python scripts/dry_run_demo.py --strategy all-skip      # 每题都跳过，最快看到未答好清单
    python scripts/dry_run_demo.py --expect-cross           # 没出现接话就非零退出

跑完把记录写到 data/_demo_dry_run.txt，非零退出表示"这场没开完"或"没达到预期"。
"""
import argparse
import asyncio
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REPORT = os.path.join(ROOT, "data", "_demo_dry_run.txt")

# 演示台词里那两句"故意答虚 / 半答"，用来把追问和未答好清单都逼出来。
# SKIP 表示这句跳过（等价于前端点"跳过"）
SKIP = "__SKIP__"

# 演示台词里那几句答话。前两段是"故意答虚 / 答得含糊"，用来说明追问从哪来；
# 第三段是认真答（带数字），用来说明系统不只挑刺、答清楚了就不进清单。
# 内容和 docs/演示输入素材包/10_回答稿.txt 保持一致，改一处要改两处
ANSWER_WEAK = "这个我们前期都考虑过了，后面会把方案做得更完善一些，具体的细节还在细化。"
ANSWER_SEMI = ("费用方面我们做过初步估算，大概在一个可以接受的范围内，"
               "我们会尽量控制成本，把资源用在最需要的地方。")
ANSWER_CONCRETE = (
    "按每月 10 万次调用、每次约 0.1 元算，一个月约 1 万元；教材交易集中"
    "在开学两周，峰值月按 1.5 万元封顶。这笔钱分三块出：学校创新创业训练计划"
    "经费先出 2 万元做两个月验证；之后靠平台成交服务费，每单 1 元、一学期约 3000 单；"
    "差额用实验室算力跑本地模型替代。调用量的估算是按 1200 份校园问卷里"
    "「愿意用平台卖书」的比例 62% 折算出来的。"
)
ANSWER_GOOD_TECH = ("后端 FastAPI，数据落 MySQL，会话和热点用 Redis；语义匹配没有每次都调大模型，"
                    "先用教材 ISBN 和课程名做规则召回，只对召回到的几十条候选调一次大模型重排，"
                    "所以 5000 人规模下成本是可控的。")
ANSWER_GOOD_COMPLIANCE = ("封面照片只用于识别书名、作者、出版社这些商品属性，不存原图、不做训练，"
                          "用户协议里单独一条授权说明，学生可以随时删号并要求删除数据；"
                          "交易链路走的是校内线下交付，平台不经手资金，规避了支付资质问题。")
ANSWER_GOOD_USER = ("5000 人这个数字来自上一学期本校教务的选课数据：选课人数超过 100 人的课程有 46 门，"
                    "涉及学生约 5300 人，我们按 5000 取整。问卷收回来 1200 份，"
                    "其中 62% 表示愿意用平台卖书、48% 愿意买书。")


# 策略是一段小函数：给它（第几问、题型、问题原文），它返回这一问该答什么
def _list_picker(items):
    def pick(i, qtype, question):
        return items[i] if i < len(items) else SKIP
    return pick


# 演示主线：第一问答虚（逼出追问和严重度 3~4），出现追问就认真答一次
# （让现场看到"答清楚了"的另一面），其余一路跳过，最快走到散会和未答好清单
def _demo_picker(i, qtype, question):
    if i == 0:
        return ANSWER_WEAK
    if qtype == "followup":
        return ANSWER_CONCRETE
    if i == 1:
        return ANSWER_SEMI
    return SKIP


STRATEGIES = {
    "demo": _demo_picker,
    "weak-first": _list_picker([ANSWER_WEAK, ANSWER_SEMI] + [SKIP] * 10),
    "all-skip": _list_picker([SKIP] * 12),
    "all-good": _list_picker([ANSWER_GOOD_TECH, ANSWER_SEMI, ANSWER_CONCRETE,
                              ANSWER_GOOD_COMPLIANCE, ANSWER_GOOD_USER] * 3),
}


def parse_args():
    p = argparse.ArgumentParser(description="演示预跑：真开一场评审会并留档")
    p.add_argument("plan", nargs="?", default=os.path.join("data", "demo_plan.txt"),
                   help="方案文件（txt），默认 data/demo_plan.txt")
    p.add_argument("--strategy", default="demo", choices=sorted(STRATEGIES),
                   help="回答策略，默认 demo（第一问答虚逼出追问，追问认真答一次，其余跳过）")
    p.add_argument("--round", type=int, default=1, help="议题轮数上限，默认 1（和页面一致）")
    p.add_argument("--expect-cross", action="store_true",
                   help="要求至少出现一次接话，否则非零退出（上台前自检用）")
    p.add_argument("--expect-followup", action="store_true",
                   help="要求至少出现一次追问，否则非零退出。追问取决于判定模型给不给追问方向，"
                        "是概率事件，演示前要跑到出现为止")
    return p.parse_args()


async def run(plan_path, pick, strategy_name, max_round):
    """开完一整场评审会，返回（记录行, 统计）"""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from app.ai.agent.review_agent.graph.review_graph import ReviewGraph
    from app.ai.agent.review_agent.node.extract_node import extract_elements, format_elements
    from app.ai.agent.review_agent.node.judge_node import SKIP_MARK
    from app.ai.agent.review_agent.node.speaker_node import REVIEWER_NAMES

    with open(plan_path, encoding="utf-8") as f:
        plan_text = f.read().strip()

    out = []
    stat = {"questions": 0, "cross": 0, "followup": 0, "verdict": {}, "ended": False}
    verdicts = []
    waits = []  # （第几问, 这一问等了多少秒），用来算现场演示要留多久

    out.append(f"方案：{os.path.relpath(plan_path, ROOT)}（{len(plan_text)} 字）")
    out.append(f"回答策略：{strategy_name}")

    # 先抽要素：和页面上的"提交方案"是同一步，抽完后面不再重复抽
    elements = extract_elements(plan_text)
    out.append("")
    out.append("=" * 68)
    out.append("一、方案要素表（评审就是照着它问的）")
    out.append("=" * 68)
    out.append(format_elements(elements))

    session_id = "dryrun-" + uuid.uuid4().hex[:10]
    out.append("")
    out.append(f"评审会 session_id：{session_id}")

    async with AsyncPostgresSaver.from_conn_string(os.getenv("POSTGRESQL_URL")) as saver:
        await saver.setup()
        graph = ReviewGraph(saver)

        async def collect(agen):
            got = []
            async for ev in agen:
                got.append(ev)
            return got

        t0 = time.perf_counter()
        events = await collect(graph.start(plan_text, session_id, max_round=max_round,
                                           plan_elements=elements))
        start_secs = time.perf_counter() - t0
        out.append("")
        out.append(f"【开场等待】提交方案到第一个问题出现：{start_secs:.1f} 秒")
        i = 0
        while True:
            tail = events[-1]
            if tail.get("event") != "await_answer":
                break

            stat["questions"] += 1
            qtype = tail.get("question_type") or "main"
            if qtype == "cross":
                stat["cross"] += 1
            elif qtype == "followup":
                stat["followup"] += 1

            out.append("")
            out.append("-" * 68)
            out.append(f"第 {stat['questions']} 问 · {tail.get('name')}"
                       f"（{qtype}，本议题第 {tail.get('question_index')}/{tail.get('issue_total')} 问）")
            out.append("-" * 68)
            out.append("【问】" + (tail.get("question") or "").strip())

            answer = pick(i, qtype, tail.get("question") or "")
            i += 1
            if answer == SKIP:
                out.append("【我答】跳过（等价于前端点「跳过」）")
                payload = SKIP_MARK
            else:
                out.append("【我答】" + answer)
                payload = answer

            t1 = time.perf_counter()
            events = await collect(graph.resume(session_id, payload))
            seg_secs = time.perf_counter() - t1
            waits.append((stat["questions"], seg_secs))
            out.append(f"【本问等待】{seg_secs:.1f} 秒（判定这一答 + 下一位发言 / 散会）")
            # 判定事件散在每一段里，必须跨段累加，只看最后一段只剩一条
            verdicts.extend(ev for ev in events if ev.get("event") == "verdict")

        # 判定按顺序抄出来更直观
        out.append("")
        out.append("=" * 68)
        out.append("二、逐条判定")
        out.append("=" * 68)
        for ev in verdicts:
            v = ev.get("verdict") or "?"
            stat["verdict"][v] = stat["verdict"].get(v, 0) + 1
            cn = {"resolved": "已答清楚", "partial": "答得不全", "unresolved": "未解决"}.get(v, v)
            out.append(f"  [{cn} 严重度 {ev.get('severity')}] {ev.get('name')}"
                       f"：{(ev.get('comment') or '').strip()}")

        tail = events[-1]
        stat["ended"] = tail.get("event") == "meeting_end"
        out.append("")
        out.append("=" * 68)
        out.append("三、散会产出")
        out.append("=" * 68)
        if stat["ended"]:
            out.append(f"接话次数：{tail.get('cross_total', 0)}")
            log = tail.get("question_log") or []
            out.append(f"质询记录：{len(log)} 条")
            out.append("")
            out.append("未答好的问题清单（按严重度降序）：")
            unresolved = tail.get("unresolved") or []
            if not unresolved:
                out.append("  （空 —— 这场全都答清楚了）")
            for k, item in enumerate(unresolved, 1):
                # 清单里的条目只存角色（speaker_role），显示名要自己查
                who = REVIEWER_NAMES.get(item.get("speaker_role"), item.get("speaker_role"))
                out.append(f"  {k}. [严重度 {item.get('severity')}] {who}："
                           f"{(item.get('question') or '').strip()}")
                if item.get("followup_question"):
                    out.append(f"     追问过：{(item.get('followup_question') or '').strip()}")
                if item.get("student_answer"):
                    out.append(f"     学生答的是：{(item.get('student_answer') or '').strip()[:60]}")
        else:
            out.append(f"**没开完**，最后停在：{tail.get('event')} —— {str(tail)[:300]}")

    out.append("")
    out.append("=" * 68)
    out.append(f"统计：问 {stat['questions']} 次 / 接话 {stat['cross']} 次 / "
               f"追问 {stat['followup']} 次 / 判定 {stat['verdict']}")
    stat["start_secs"] = start_secs
    stat["waits"] = waits
    if waits:
        each = [w for _, w in waits]
        out.append(f"耗时：开场 {start_secs:.0f} 秒，之后每问平均 {sum(each) / len(each):.0f} 秒"
                   f"（最长 {max(each):.0f} 秒），"
                   f"全程约 {start_secs + sum(each):.0f} 秒 —— 现场要按这个留时间")
    return out, stat


def main():
    args = parse_args()
    plan_path = args.plan if os.path.isabs(args.plan) else os.path.join(ROOT, args.plan)
    if not os.path.exists(plan_path):
        print(f"找不到方案文件：{plan_path}")
        return 2

    pick = STRATEGIES[args.strategy]
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        lines, stat = asyncio.run(run(plan_path, pick, args.strategy, args.round))
    except Exception:
        import traceback
        lines = ["**跑挂了**", traceback.format_exc()]
        stat = {"ended": False, "cross": 0, "questions": 0}

    lines.append("")
    lines.append("结论：" + ("这场开完了" if stat["ended"] else "**这场没开完**"))
    lines.append(f"接话出现：{'是' if stat['cross'] else '否'}"
                 f"（对应选型文档里的阈值：本地小模型更保守，演示走商业模型）")
    lines.append(f"追问出现：{'是' if stat.get('followup') else '否'}"
                 f"（追问要判定模型给出追问方向才会发生，属概率事件）")
    if args.expect_cross and not stat["cross"]:
        lines.append("上线自检：**没出现接话** —— 别就这么上去，重跑一次或换商业模型")
    if args.expect_followup and not stat.get("followup"):
        lines.append("上线自检：**没出现追问** —— 重跑一次；追问是演示的第二个看点，不能靠赌")
    text = "\n".join(lines) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(text)

    print(text)
    print(f"（已写入 {os.path.relpath(REPORT, ROOT)}）")

    if not stat["ended"]:
        return 1
    if args.expect_cross and not stat["cross"]:
        return 1
    if args.expect_followup and not stat.get("followup"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
