# -*- coding: utf-8 -*-
"""
评审会记录库：状态里只留索引，问答正文放数据库

为什么要有这一层
----------------
一场评审会的问答正文（问题原文、学生回答、判定评语）原本整份堆在图的状态里。
LangGraph 的检查点是"每个超步把当前状态整份写一遍"，状态越胖，每一步越贵，
而且写的次数还随着问答条数在涨 —— 合起来是平方级增长。
更要紧的是：正文只要还在状态里，就随时可能被某个节点整段拼进提示词，
本地模型看不出价格，换成商业模型就是真金白银。

所以状态里只留索引：

    {id, round, speaker_role, question_type, target_speaker, question,
     verdict, severity, followup_depth, followup_hint}

  - `id` 是 review_question 表的自增主键，也就是全场的发言序号；
  - `question` 只在这条**还没判过**的时候留着（同步的 manager 要拿它去问学生），
    判完就从索引里 pop 掉，正文永远躺在库里。

谁要用正文，谁拿 id 来取：

    追问发言  -> 取本议题的问答
    接话判定  -> 取最近 6 条发言
    判定      -> 取本议题的问答
    散会      -> 取整场记录推给前端（前端契约不变）

分工：
  纯函数（不碰数据库）—— 条目构造、议题切片、记录渲染、未答好清单。
     同步的 manager 节点只用这些，它不该有任何 I/O。
  异步函数（走 review_dao）—— 落库、回填判定、按 id 取正文。
     pymysql 是同步阻塞的，统一用 asyncio.to_thread 挪到线程里，
     否则会卡住事件循环（cross_node 踩过这个坑：astream 一个 chunk 都收不到）。
"""
import asyncio

from app.ai.agent.review_agent import reviewers
from app.ai.tool import review_dao

# ==================== 纯函数：只在内存里摆弄索引 ====================


def new_entry(round_no: int, role: str, qtype: str, question: str = "",
              target: str = "", severity: int = 0, depth: int = 0) -> dict:
    """造一条索引条目。

    id 要等落库时才回填，先占 0 —— 所以这个函数只负责"形状"，
    真正入库的是 ask()。
    """
    return {
        "id": 0,
        "round": int(round_no or 1),
        "speaker_role": role,
        "question_type": qtype or "main",
        "target_speaker": target or "",
        "question": question or "",
        "verdict": "",
        "severity": int(severity or 0),
        "followup_depth": int(depth or 0),
        "followup_hint": "",
    }


def split_issues(index: list) -> list:
    """按议题切开：每条主问题开一个新议题，后面的接话/追问都归到它名下"""
    issues = []
    for item in (index or []):
        if item.get("question_type") == "main" or not issues:
            issues.append([item])
        else:
            issues[-1].append(item)
    return issues


def current_issue(index: list) -> list:
    """当前议题 = 最后一条主问题开始往后那一段"""
    issues = split_issues(index)
    return issues[-1] if issues else []


def first_unjudged(issue: list):
    """本议题里第一条还没判过的问题。学生接下来要答的就是它，答完再轮到下一条"""
    for item in (issue or []):
        if not item.get("verdict"):
            return item
    return None


def last_ref(index: list):
    """最后一条"抛给学生的问题"（主问题或追问），接话针对的就是它"""
    for item in reversed(index or []):
        if item.get("question_type") in ("main", "followup"):
            return item
    return None


def collect_unresolved(rows: list) -> list:
    """把"未答好的问题清单"从质询记录里推出来

    每次都整体重算而不是逐条追加，保证清单和判定结果永远一致。

    一个议题里每位评审只问一次（主问题或接话），追问是这个人的第二次。
    所以按提问者归拢、以**最后一次判定**为准：
      - 追问答好了      -> 这个问题不算未答好，从清单里消失
      - 追了还是没答好  -> 进清单，严重度取两次里更高的那个

    清单里显示这位评审**最先问的那个问题**（议题的主线），追问单独附在后面，
    这样学生看到的是"我当时被问的是什么"，而不是被追问绕晕
    """
    result = []
    for group in split_issues(rows):
        by_role = {}
        for item in group:
            by_role.setdefault(item.get("speaker_role"), []).append(item)
        for role, items in by_role.items():
            first, last = items[0], items[-1]
            if not last.get("verdict") or last["verdict"] == "resolved":
                continue
            followup = items[1] if len(items) > 1 else {}
            result.append({
                "round": group[0].get("round", 1),
                "speaker_role": role,
                "question": first.get("question", ""),
                "student_answer": first.get("student_answer", ""),
                "followup_question": followup.get("question", ""),
                "followup_answer": followup.get("student_answer", ""),
                "verdict": last.get("verdict", ""),
                "severity": max(int(x.get("severity") or 0) for x in items),
                "comment": last.get("verdict_comment", ""),
            })
    # 严重度高的排前面，会议纪要按这个顺序念
    result.sort(key=lambda x: -x["severity"])
    return result


# ==================== 渲染：把库里的记录变成提示词里的那几行 ====================


def format_entry(item: dict) -> str:
    """渲染一条记录，评审和学生的话用不同前缀标出来"""
    who = reviewers.REVIEWER_NAMES.get(item.get("speaker_role", ""),
                                       item.get("speaker_role", ""))
    question = item.get("question", "")
    if item.get("question_type") == "cross":
        target = reviewers.REVIEWER_NAMES.get(item.get("target_speaker", ""), "学生")
        lines = [f"【{who}】接{target}的话：{question}"]
    elif item.get("question_type") == "followup":
        lines = [f"【{who}】追问：{question}"]
    else:
        lines = [f"【{who}】提问：{question}"]
    answer = (item.get("student_answer") or "").strip()
    if answer:
        lines.append(f"【学生】回答：{answer}")
    return "\n".join(lines)


# 取"本议题"的问答记录：从最后一条主问题开始往后切。
# 追问必须看到学生上一条答了什么，不然会照着原问题再问一遍；
# 但也不能把整场会议的记录都倒给它，那样评审会跑题去问别人议题的东西
def issue_transcript(rows: list) -> str:
    rows = rows or []
    start = 0
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].get("question_type") == "main":
            start = i
            break
    return "\n".join(format_entry(item) for item in rows[start:])


# 把最近的发言拼成文本，让判定的模型知道刚才发生了什么
def recent_transcript(rows: list, limit: int = 6) -> str:
    lines = []
    for item in (rows or [])[-limit:]:
        who = reviewers.REVIEWER_NAMES.get(item.get("speaker_role", ""),
                                           item.get("speaker_role", ""))
        if item.get("question_type") == "cross":
            target = reviewers.REVIEWER_NAMES.get(item.get("target_speaker", ""), "学生")
            lines.append(f"【{who}】接{target}的话：{item.get('question', '')}")
        else:
            lines.append(f"【{who}】{item.get('question', '')}")
    return "\n".join(lines) if lines else "（还没有人发言）"


# ==================== 异步：真正的落库与取正文 ====================


async def reset(session_id: str) -> int:
    """清掉这场会话已有的记录。

    重开一场评审会时先清一次：记录是边开边写透的，不清的话
    上一次的记录会跟这一次的叠在一起，索引里的 id 也会对不上
    """
    if not session_id:
        return 0
    return await asyncio.to_thread(review_dao.delete_questions, session_id)


async def ask(session_id: str, entry: dict) -> int:
    """把刚问出来的问题落库，返回它在库里的 id —— 这个 id 就是索引

    没有 session_id 就直接报错：宁可当场炸掉，也不能把记录写进一个
    不知道是谁的会话里（那样索引会指向别人的正文）
    """
    if not session_id:
        raise ValueError("session_id 为空，问答记录无处落库")
    entry_id = await asyncio.to_thread(review_dao.append_question, session_id, entry)
    entry["id"] = entry_id
    return entry_id


async def save_verdict(entry_id: int, answer: str, verdict: str, severity: int,
                       comment: str) -> None:
    """学生答完、判定出来，把正文写回库里那条记录"""
    if not entry_id:
        return
    await asyncio.to_thread(review_dao.update_verdict, entry_id, answer, verdict,
                            severity, comment)


async def rows_of(entries: list) -> list:
    """按索引取回正文，按 id 升序（也就是发言顺序）"""
    ids = [e.get("id") for e in (entries or []) if e.get("id")]
    if not ids:
        return []
    return await asyncio.to_thread(review_dao.get_questions_by_ids, ids)


async def rows_of_role(index: list, role: str) -> list:
    """取某位评审在这场里问过的正文（接话查"是不是在炒自己的冷饭"要用）"""
    return await rows_of([e for e in (index or []) if e.get("speaker_role") == role])


async def question_of(entry: dict) -> str:
    """取某一条的问题原文。没落过库（id=0）时退回索引里那份"""
    if not entry:
        return ""
    if not entry.get("id"):
        return entry.get("question") or ""
    rows = await rows_of([entry])
    return rows[0].get("question", "") if rows else ""


async def all_rows(session_id: str) -> list:
    """整场记录。散会时推给前端用，前端契约和以前一样"""
    if not session_id:
        return []
    return await asyncio.to_thread(review_dao.get_questions, session_id)
