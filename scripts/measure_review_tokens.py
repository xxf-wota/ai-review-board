# -*- coding: utf-8 -*-
"""
评审会 token / 状态体积测量（纯计算，不调模型）

要回答的问题：
    1. 一场评审会到底往模型里送了多少字？其中有多少是 question_log 带进去的？
    2. 状态里放全量正文（改造前）和只放索引（改造后），检查点分别要写多少？
    3. 议题轮数从 1 加到 3，这两组数字各涨多少？（演示只开 1 轮，生产要开多轮）

数据来源：MySQL 里最近一场有记录的评审会（真实要素表 + 真实质询记录）。
没有记录时用 data/demo_plan.txt 的要素表 + 造出来的记录兜底，保证脚本永远能跑。

用法：
    python scripts/measure_review_tokens.py                 # 测最近一场
    python scripts/measure_review_tokens.py --rounds 3      # 按"3 轮"重排一遍再测
    python scripts/measure_review_tokens.py --session <id>  # 指定会话

报告写到 data/_review_token_report.txt，退出码恒为 0（这是测量，不是验收）。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REPORT = os.path.join(ROOT, "data", "_review_token_report.txt")


# ---------- 估 token ----------
# 中文字符大约 1 字 1 token，英文/数字大约 4 字符 1 token。
# 不引 tiktoken：这里要的是"哪儿在涨钱"，不是精确到个位数的账单。
def est_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or ch in "，。！？：；、（）【】「」《》":
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4


# ---------- 数据来源 ----------
def _fallback_log(elements: dict) -> list:
    """没有真实记录时造一份：结构、长度都照着实测那场的量级来"""
    q = ("这个方案里最关键的技术难点你打算怎么落地？有没有更简单的替代方案？"
         "如果用户量涨到现在的十倍，哪一块会先扛不住？")
    a = ("后端用 FastAPI，数据落 MySQL，热点走 Redis；语义匹配先用 ISBN 做规则召回，"
         "只对召回的候选调一次大模型重排，所以五千人规模成本可控。")
    log = []
    for i, role in enumerate(["tech", "cost", "compliance", "user"]):
        log.append({"round": 1, "speaker_role": role, "question_type": "main",
                    "target_speaker": "", "question": q, "student_answer": a,
                    "verdict": "unresolved" if i % 2 == 0 else "resolved",
                    "verdict_comment": "回答里没有给出可验证的依据，无法判断能不能落地。",
                    "severity": 3, "followup_depth": 0,
                    "followup_hint": "预算里的人力成本怎么算的？"})
    return log


def load_real():
    """从 MySQL 取最近一场有质询记录的会话（要素表 + 记录）"""
    from app.ai.tool.review_dao import get_questions, get_session
    from app.ai.utils.mysql_util import get_mysql_conn

    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute("select session_id from review_question "
                    "group by session_id order by max(id) desc limit 1")
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None, None, None
    session_id = row[0]
    session = get_session(session_id) or {}
    return session_id, session.get("plan_elements") or {}, get_questions(session_id)


# ---------- 复原"这一场一共调了几次模型、每次喂了什么" ----------
def replay_calls(log: list, elements: dict) -> list:
    """按真实代码的调用时机把每一次模型调用的提示词拼出来

    还原的是 scripts 之外的真实路径：
      - 主问题发言：只喂要素表（speaker_node 里 is_followup=False，history 为空）
      - 接话判定：喂"最近 6 条发言"（store.recent_transcript）
      - 接话发言：喂要素表 + 对方那一句话
      - 判定：喂"本议题问答"（store.issue_transcript）
      - 追问发言：喂"本议题问答" + 追问方向
    """
    from app.ai.agent.review_agent.node.extract_node import format_elements
    from app.ai.agent.review_agent.node.judge_node import is_skip
    from app.ai.agent.review_agent.node.speaker_node import _build_input
    from app.ai.agent.review_agent.reviewers import REVIEWER_CONCERNS, PROMPTS
    from app.ai.agent.review_agent.store.question_store import (
        issue_transcript, recent_transcript,
    )
    from app.ai.prompt.builder_prompt import BuilderPromptYaml

    elements_text = format_elements(elements)
    crosstalk = BuilderPromptYaml.get_prompt("review/crosstalk.yaml")
    crosstalk_speak = BuilderPromptYaml.get_prompt("review/crosstalk_speak.yaml")
    judge_prompt = BuilderPromptYaml.get_prompt("review/judge.yaml")

    calls = []

    def add(kind, role, system, user, idx, elems=True, log_part=""):
        calls.append({
            "kind": kind, "role": role, "idx": idx,
            "system": system, "user": user,
            "system_chars": len(system), "user_chars": len(user),
            "elements_chars": len(elements_text) if elems else 0,
            "log_chars": len(log_part),
        })

    for idx, item in enumerate(log):
        qtype = item.get("question_type") or "main"
        role = item.get("speaker_role") or "tech"
        prefix = log[:idx]

        if qtype == "main":
            # 主问题：前面可能先做了一次接话判定（cross_node 在 speaker 之后跑）
            add("cross_judge", "all", crosstalk,
                f"以下是学生提交的方案要素表：\n{elements_text}\n\n"
                f"以下是本场评审会刚才的发言：\n{recent_transcript(prefix)}\n\n"
                f"本议题已经发过言的评审是：{role}\n请判断每一位评审是否要接话。",
                idx, log_part=recent_transcript(prefix))
            role = item.get("speaker_role") or "tech"
            add("speaker_main", role, PROMPTS[role],
                _build_input(elements, "", "", False), idx)
        elif qtype == "followup":
            add("speaker_followup", role, PROMPTS[role],
                _build_input(elements, issue_transcript(prefix),
                             item.get("followup_hint") or "", True),
                idx, log_part=issue_transcript(prefix))
        elif qtype == "cross":
            target = item.get("target_speaker") or ""
            add("cross_speak", role, f"{PROMPTS[role]}\n\n{crosstalk_speak}",
                f"以下是学生提交的方案要素表：\n{elements_text}\n\n"
                f"刚才 {target} 说的是：\n「{item.get('question') or ''}」\n\n"
                f"你只能从自己的关注点提问，你的关注点只有：{REVIEWER_CONCERNS.get(role, '')}\n\n"
                f"请针对「{item.get('question') or ''}」这件事，从你自己的关注点提出一个问题。",
                idx, elems=False)

        # 这条被学生答过了，就有一次判定调用。
        # 但"跳过 / 没回答"这条路径不调模型（judge_node 里直接记成未解决），必须排除，
        # 不然会把"跳过"也算成钱，数字虚高
        if (item.get("verdict") or item.get("student_answer")) and not is_skip(item.get("student_answer")):
            add("judge", role, judge_prompt,
                f"以下是学生提交的方案要素表：\n{elements_text}\n\n"
                f"以下是本议题已经发生的问答：\n{issue_transcript(log[:idx + 1])}\n\n"
                f"现在要判定的是【{role}】提的这个疑问：\n「{item.get('question') or ''}」\n\n"
                f"学生刚才的回答是：\n「{item.get('student_answer') or ''}」\n\n"
                "请判断这个回答有没有解决他的疑问。",
                idx, log_part=issue_transcript(log[:idx + 1]))
    return calls


# ---------- 状态体积 ----------
# 索引里留下的字段（改造后状态的全部内容）+ 还没判过的那一条会临时带上问题原文
INDEX_KEYS = ("id", "round", "speaker_role", "question_type", "target_speaker",
              "verdict", "severity", "followup_depth", "followup_hint")


def state_bytes(log: list, plan_text: str, steps_per_entry: int = 6) -> dict:
    """模拟检查点：每个超步都把当前状态整份写一次

    LangGraph 的 Postgres 检查点是"每个超步写一份完整通道值"，
    所以状态里的东西越长，每写一次越贵，写的次数还随问答条数在涨 —— 合计是平方级。

    before：改造前的状态（question_log 全文 + messages + 方案全文）
    after ：改造后的状态（只留索引；正文在 MySQL，方案全文抽完要素就清掉）
    """
    from langchain_core.messages import AIMessage, HumanMessage

    def dump(obj) -> int:
        return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    before = 0
    after = 0
    text_bytes = 0
    for k in range(1, len(log) + 1):
        part = log[:k]
        # ---- 改造前：状态里就是 log 原文 + messages + 方案全文 ----
        msgs = [HumanMessage(content=plan_text).model_dump()]
        for item in part:
            msgs.append(AIMessage(content=item.get("question") or "").model_dump())
        before += dump({"question_log": part, "messages": msgs,
                        "plan_text": plan_text}) * steps_per_entry
        # ---- 改造后：状态里只有索引 ----
        # 还没判过的那条带着问题原文（同步的 manager 要拿它去问学生），判过的就删掉
        idx_part = []
        for i, item in enumerate(part):
            entry = {kk: item.get(kk) for kk in INDEX_KEYS}
            if i == len(part) - 1 and not item.get("verdict"):
                entry["question"] = item.get("question") or ""
            idx_part.append(entry)
        after += dump({"question_index": idx_part}) * steps_per_entry
        # 这条记录新产生的正文（问题 / 回答 / 评语 / 追问方向）—— 现在这些都进库，不进状态
        text_bytes += dump({"question": part[-1].get("question") or "",
                            "student_answer": part[-1].get("student_answer") or "",
                            "verdict_comment": part[-1].get("verdict_comment") or "",
                            "followup_hint": part[-1].get("followup_hint") or ""})
    # 任何一步里，索引中带着问题原文的条数：只有"还没判过的那一条"，最多 1
    pending = 1 if (log and not log[-1].get("verdict")) else 0
    return {"before": before, "after": after, "text": text_bytes, "n": len(log),
            "steps_per_entry": steps_per_entry, "pending": pending,
            "plan_text": len(plan_text)}


def main():
    p = argparse.ArgumentParser(description="评审会 token / 状态体积测量（不调模型）")
    p.add_argument("--session", default="", help="指定会话 id，默认取最近一场")
    p.add_argument("--rounds", type=int, default=1, help="按几轮重排（演示 1 轮，生产多轮）")
    args = p.parse_args()

    out = []
    out.append("评审会 token / 状态体积测量（纯计算，不调模型；字数为字符数，token 为估算）")

    session_id, elements, log = load_real()
    source = "MySQL 真实记录"
    if not log:
        source = "兜底数据（MySQL 里没有质询记录）：data/demo_plan.txt 的要素表 + 造的记录"
        with open(os.path.join(ROOT, "data", "demo_plan.txt"), encoding="utf-8") as f:
            plan_text = f.read().strip()
        elements = {"goal": plan_text[:60], "tech": ["FastAPI", "MySQL", "Redis"],
                    "budget": "未提及", "schedule": "10 周", "risks": [], "compliance": "未提及",
                    "users": "本校 5000 名学生", "missing": ["预算", "风险应对", "数据合规"]}
        log = _fallback_log(elements)
    else:
        from app.ai.tool.review_dao import get_session
        plan_text = (get_session(session_id) or {}).get("plan_text") or ""

    if args.session:
        from app.ai.tool.review_dao import get_questions, get_session
        session_id = args.session
        got = get_questions(session_id)
        if got:
            log = got
            elements = (get_session(session_id) or {}).get("plan_elements") or elements
            source = f"MySQL 指定会话 {session_id}"

    # 多轮重排：把同一批议题复制若干轮，只为看增长趋势
    if args.rounds > 1:
        base = [dict(x) for x in log]
        log = []
        for r in range(1, args.rounds + 1):
            for item in base:
                item = dict(item)
                item["round"] = r
                log.append(item)

    out.append(f"数据来源：{source}")
    out.append(f"会话：{session_id or '（无）'}")
    out.append(f"方案全文：{len(plan_text)} 字")
    out.append(f"质询记录：{len(log)} 条"
               f"（主问题 {sum(1 for x in log if x.get('question_type') == 'main')}、"
               f"接话 {sum(1 for x in log if x.get('question_type') == 'cross')}、"
               f"追问 {sum(1 for x in log if x.get('question_type') == 'followup')}）")
    out.append(f"议题轮数：{args.rounds}")

    calls = replay_calls(log, elements)
    total_chars = sum(c["system_chars"] + c["user_chars"] for c in calls)
    total_tokens = sum(est_tokens(c["system"]) + est_tokens(c["user"]) for c in calls)
    elems_chars = sum(c["elements_chars"] for c in calls)
    log_chars = sum(c["log_chars"] for c in calls)

    out.append("")
    out.append("=" * 72)
    out.append("一、模型调用（按真实调用时机复原）")
    out.append("=" * 72)
    per_kind = {}
    for c in calls:
        d = per_kind.setdefault(c["kind"], {"n": 0, "chars": 0, "tokens": 0, "log": 0})
        d["n"] += 1
        d["chars"] += c["system_chars"] + c["user_chars"]
        d["tokens"] += est_tokens(c["system"]) + est_tokens(c["user"])
        d["log"] += c["log_chars"]
    out.append(f"{'调用类型':<16}{'次数':>5}{'提示词字数':>12}{'估算token':>11}{'其中质询记录':>14}")
    for kind, d in sorted(per_kind.items(), key=lambda kv: -kv[1]["chars"]):
        out.append(f"{kind:<16}{d['n']:>5}{d['chars']:>12,}{d['tokens']:>11,}{d['log']:>14,}")
    out.append(f"{'合计':<16}{len(calls):>5}{total_chars:>12,}{total_tokens:>11,}{log_chars:>14,}")
    out.append("")
    out.append(f"要素表重复投喂：{elems_chars:,} 字（占全部提示词 {elems_chars / max(total_chars, 1):.0%}）"
               f" —— 每次调用都要带一遍，这就是最大的一块固定开销")
    out.append(f"质询记录投喂：{log_chars:,} 字（占全部提示词 {log_chars / max(total_chars, 1):.0%}）")
    out.append(f"人设等固定提示词：{total_chars - elems_chars - log_chars:,} 字")
    longest = max(calls, key=lambda c: c["system_chars"] + c["user_chars"]) if calls else None
    if longest:
        out.append(f"最长的一次提示词：{longest['kind']}（{longest['role']}）"
                   f"{longest['system_chars'] + longest['user_chars']:,} 字"
                   f"，估算 {est_tokens(longest['system']) + est_tokens(longest['user']):,} token")

    st = state_bytes(log, plan_text)
    out.append("")
    out.append("=" * 72)
    out.append("二、状态体积（检查点每个超步整份写一次，这里按每条约 "
               f"{st['steps_per_entry']} 个超步估算）")
    out.append("=" * 72)
    out.append(f"问答正文总量（问题 / 回答 / 评语 / 追问方向）：{st['text']:,} 字节")
    out.append(f"改造前累计写入（question_log 全文 + messages + 方案全文）：{st['before']:,} 字节"
               f"（{st['before'] / 1024:.1f} KB，{st['n']} 条记录）")
    out.append(f"改造后累计写入（状态只留索引，正文在 MySQL）：{st['after']:,} 字节"
               f"（{st['after'] / 1024:.1f} KB）")
    out.append(f"体积比：{st['before'] / max(st['after'], 1):.1f}×"
               f"（{st['before'] / 1024:.1f} KB → {st['after'] / 1024:.1f} KB）")
    out.append("")
    out.append(f"方案全文 {st['plan_text']:,} 字：改造前每个超步都要写一遍；改造后只在"
               "要素表还没有时进状态，抽取节点用完立刻清空")
    out.append("索引里任何时候最多只有 1 条带着问题原文（还没判过的那一条，"
               "同步的 manager 要拿它去问学生），判完即删；"
               f"本场记录是已经散会的，所以末态实测 {st['pending']} 条")
    out.append("注：改造前的 messages 里只算了每问一条 AI 消息，判定消息、assistant 气泡也都进 messages，")
    out.append("    真实体积比这里更大；而 messages 在评审图里没有任何节点读取。")

    text = "\n".join(out)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(text + "\n")

    print(f"报告已写入 {os.path.relpath(REPORT, ROOT)}")
    print(f"模型调用 {len(calls)} 次 / 提示词 {total_chars:,} 字 / 估算 {total_tokens:,} token")
    print(f"其中要素表 {elems_chars:,} 字、质询记录 {log_chars:,} 字")
    print(f"检查点累计写入：改造前 {st['before'] / 1024:.1f} KB → 改造后 {st['after'] / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
