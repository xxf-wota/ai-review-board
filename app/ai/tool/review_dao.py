import json

from app.ai.utils.mysql_util import get_mysql_conn

"""
评审会数据访问
只负责读写 review_session / review_question 两张表
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


# 单条质询记录：问题一抛出来就写一条，返回自增 id。
# 与 save_questions 的"整批替换"分工不同 —— 这是评审会进行中的写透，
# 图的状态里只留这个 id，正文（问题/回答/评语）都在库里
def append_question(session_id: str, item: dict) -> int:
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "insert into review_question "
            "(session_id, round, speaker_role, question_type, target_speaker, "
            " question, student_answer, verdict, verdict_comment, severity, followup_depth) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


# 回填一条记录的判定结果：学生答完、判定出来就写，散会时不用再补
def update_verdict(question_id: int, answer: str, verdict: str, severity: int,
                   comment: str) -> int:
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "update review_question set student_answer=%s, verdict=%s, severity=%s, "
            "verdict_comment=%s where id=%s",
            (answer or "", verdict or "", int(severity or 0), comment or "",
             int(question_id)),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# 质询记录的字段顺序：select 和"行转字典"共用一份，免得改了一处漏一处
# id 一定要带上：状态里存的就是它（索引 -> 正文全靠这个键对回去），
# 排查问题时也要能一眼把状态里的索引和库里的行对上
_QUESTION_SELECT = (
    "id, round, speaker_role, question_type, target_speaker, question, "
    "student_answer, verdict, verdict_comment, severity, followup_depth"
)


# 一行质询记录 -> 字典。键名和图里用的名字一致，所以状态索引和归档记录能直接对上
def _row_to_item(row) -> dict:
    return {
        "id": row[0],
        "round": row[1],
        "speaker_role": row[2],
        "question_type": row[3],
        "target_speaker": row[4] or "",
        "question": row[5] or "",
        "student_answer": row[6] or "",
        "verdict": row[7] or "",
        "verdict_comment": row[8] or "",
        "severity": row[9],
        "followup_depth": row[10],
    }


# 按 id 取回指定的几条记录，按 id 升序 —— 也就是发言顺序。
# 状态索引里只有 id，节点要用哪一段正文就拿哪些 id 来取
def get_questions_by_ids(ids: list) -> list:
    ids = [int(i) for i in (ids or []) if i]
    if not ids:
        return []
    marks = ",".join(["%s"] * len(ids))
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"select {_QUESTION_SELECT} from review_question "
            f"where id in ({marks}) order by id",
            ids,
        )
        return [_row_to_item(r) for r in cur.fetchall()]
    finally:
        conn.close()


# 读回一场评审会的质询记录，按 id 升序 —— 也就是发言顺序
def get_questions(session_id: str) -> list:
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"select {_QUESTION_SELECT} from review_question "
            f"where session_id=%s order by id",
            (session_id,),
        )
        return [_row_to_item(r) for r in cur.fetchall()]
    finally:
        conn.close()


# 清掉一场评审会的质询记录。重开一场会时先清，避免新旧记录叠在一起
def delete_questions(session_id: str) -> int:
    if not session_id:
        return 0
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute("delete from review_question where session_id=%s", (session_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# 删除一场评审会：两张表一起清（删会话时用），返回各自删了几行
# review_question 先删：它挂着 session_id，语义上属于 review_session 的下属记录
def delete_session(session_id: str) -> dict:
    if not session_id:
        return {"review_question": 0, "review_session": 0}
    conn = get_mysql_conn()
    try:
        cur = conn.cursor()
        cur.execute("delete from review_question where session_id=%s", (session_id,))
        n_question = cur.rowcount
        cur.execute("delete from review_session where session_id=%s", (session_id,))
        n_session = cur.rowcount
        conn.commit()
        return {"review_question": int(n_question or 0), "review_session": int(n_session or 0)}
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
