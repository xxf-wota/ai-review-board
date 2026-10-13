"""把归属校验上线之前提交的评审会补上归属。

背景：`review_session.student_id` 是加了"按账号拉历史"之后才开始写的，
更早提交的会这一列是空串。`app/web/identity.py` 的规则是"有主且不是你 -> 403"，
空归属的会按幂等放行 —— 好处是老会话连开过它的人也能进，代价是
知道那个 session_id 的人仍能读 / 删它。

这个脚本把空归属的行补成一个指定账号，把这条缝收掉。

默认只打印不改。要真写库必须同时给 --apply 和 --email。

    python scripts/backfill_review_owner.py --email 1359525405@qq.com
    python scripts/backfill_review_owner.py --email 1359525405@qq.com --apply
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))
from app.ai.utils.mysql_util import get_mysql_conn

REPORT = os.path.join(ROOT, "data", "_backfill_owner.txt")

# 空归属的两种写法都算：老数据可能是 NULL，也可能是空串
OWNERLESS = "student_id is null or student_id = ''"


def main() -> int:
    ap = argparse.ArgumentParser(description="给无主的评审会补归属")
    ap.add_argument("--email", required=True, help="把这些无主的评审会补到这个账号下")
    ap.add_argument("--apply", action="store_true", help="真的写库；不加这一项只打印")
    args = ap.parse_args()

    email = (args.email or "").strip()
    lines = []
    if not email or "@" not in email:
        lines.append(f"--email 不像一个邮箱：{email!r}，什么都没做")
        return _finish(lines, 2)

    conn = get_mysql_conn()
    try:
        cur = conn.cursor()

        cur.execute(f"select session_id, plan_title, status, created_at from review_session where {OWNERLESS} order by created_at, session_id")
        rows = cur.fetchall()

        cur.execute("select count(*) from review_session")
        total = cur.fetchone()[0]
        cur.execute(f"select count(*) from review_session where {OWNERLESS}")
        ownerless = cur.fetchone()[0]

        lines.append(f"评审会总行数 {total}，其中无归属 {ownerless} 行")

        if not rows:
            lines.append("没有无主的评审会，什么都不用做")
            return _finish(lines, 0)

        lines.append(f"待补归属的评审会 {len(rows)} 场：")
        for sid, title, status, created in rows:
            lines.append(f"  {created}  {sid}  status={status or '':<9}  {title or ''}")

        if not args.apply:
            lines.append("")
            lines.append(f"[dry-run] 只是打印，没有写库。加 --apply 才会把它们补到 {email} 名下")
            return _finish(lines, 0)

        cur.execute(f"update review_session set student_id=%s where {OWNERLESS}", (email,))
        changed = cur.rowcount
        conn.commit()

        lines.append("")
        lines.append(f"已把 {changed} 场补到 {email} 名下")

        cur.execute("select count(*) from review_session where student_id=%s", (email,))
        lines.append(f"{email} 名下现在共 {cur.fetchone()[0]} 场")
        cur.execute(f"select count(*) from review_session where {OWNERLESS}")
        lines.append(f"仍无归属的还有 {cur.fetchone()[0]} 行")
    finally:
        conn.close()
    return _finish(lines, 0)


def _finish(lines: list, code: int) -> int:
    text = "\n".join(lines) + "\n"
    try:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"report -> {REPORT}")
    except OSError as e:  # 只读环境里也要能看到结果
        print(f"写报告失败（{e}），直接打印：")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
