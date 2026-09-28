import json

from app.ai.utils.mysql_util import get_mysql_conn

"""
评审会数据访问
只负责读写 review_session / review_question / review_score 三张表
"""


# 写入（或更新）一次评审会
def save_session(session_id: str, plan_title: str, plan_text: str,
                 elements: dict, student_id: str = "") -> int:
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        sql = (
            "insert into review_session "
            "(session_id, plan_title, plan_text, plan_elements, student_id, status, round) "
            "values (%s,%s,%s,%s,%s,'submitted',0) "
            "on duplicate key update "
            "plan_title=values(plan_title), plan_text=values(plan_text), "
            "plan_elements=values(plan_elements), student_id=values(student_id)"
        )
        # 要素表存成 JSON 字符串，中文不转义，方便直接看库
        cur.execute(sql, (
            session_id, plan_title, plan_text,
            json.dumps(elements, ensure_ascii=False), student_id,
        ))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# 按会话ID读回一次评审会，读不到返回 None
def get_session(session_id: str):
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "select session_id, plan_title, plan_text, plan_elements, student_id, status, round, created_at "
            "from review_session where session_id=%s",
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "session_id": row[0],
            "plan_title": row[1],
            "plan_text": row[2],
            "plan_elements": json.loads(row[3]) if row[3] else {},
            "student_id": row[4],
            "status": row[5],
            "round": row[6],
            "created_at": str(row[7]),
        }
    finally:
        conn.close()


# 整批写入质询记录（会议纪要的数据源）
# 先按 session_id 删旧的再插：同一场会重开一次，记录应该是"替换"而不是"叠加"。
# 顺带这个函数就自带了幂等性，重跑验收脚本不会越积越多
def save_questions(session_id: str, question_log: list) -> int:
    if not session_id:
        return 0
    rows = [
        (
            session_id,
            int(item.get("round") or 1),
            item.get("speaker_role") or "",
            item.get("question_type") or "main",
            item.get("target_speaker") or "",
            item.get("question") or "",
            item.get("student_answer") or "",
            item.get("verdict") or "",
            item.get("verdict_comment") or "",
            int(item.get("severity") or 0),
            int(item.get("followup_depth") or 0),
        )
        for item in (question_log or [])
    ]
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute("delete from review_question where session_id=%s", (session_id,))
        if rows:
            cur.executemany(
                "insert into review_question "
                "(session_id, round, speaker_role, question_type, target_speaker, "
                " question, student_answer, verdict, verdict_comment, severity, followup_depth) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


# 读回一场评审会的质询记录，按 id 升序 —— 也就是发言顺序
def get_questions(session_id: str) -> list:
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "select round, speaker_role, question_type, target_speaker, question, "
            "student_answer, verdict, verdict_comment, severity, followup_depth "
            "from review_question where session_id=%s order by id",
            (session_id,),
        )
        return [
            {
                "round": r[0],
                "speaker_role": r[1],
                "question_type": r[2],
                "target_speaker": r[3] or "",
                "question": r[4],
                "student_answer": r[5] or "",
                "verdict": r[6] or "",
                "verdict_comment": r[7] or "",
                "severity": r[8],
                "followup_depth": r[9],
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


# 会议结束，把会话状态改成已结束，轮次记为实际开到了第几轮
def finish_session(session_id: str, round_no: int = 1) -> int:
    if not session_id:
        return 0
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "update review_session set status='finished', round=%s where session_id=%s",
            (int(round_no or 1), session_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
