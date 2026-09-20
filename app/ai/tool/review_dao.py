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
