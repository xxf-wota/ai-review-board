# -*- coding: utf-8 -*-
"""
建评审会三张表：review_session / review_question / review_score
读取同目录下的 init_review_tables.sql 执行，全部 IF NOT EXISTS，可重复运行
执行后打印每张表的字段，确认真的建好了
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.utils.mysql_util import get_mysql_conn

SQL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "init_review_tables.sql")
TABLES = ["review_session", "review_question", "review_score"]

lines = []


def main():
    with open(SQL_FILE, encoding="utf-8") as f:
        sql_text = f.read()

    # 按分号拆成一条条语句，去掉注释行
    statements = []
    for chunk in sql_text.split(";"):
        body = "\n".join(
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ).strip()
        if body:
            statements.append(body)

    conn = get_mysql_conn()
    cur = conn.cursor()
    for stmt in statements:
        cur.execute(stmt)
    conn.commit()
    lines.append(f"已执行 {len(statements)} 条建表语句")

    # 验证：表在不在、字段对不对
    cur.execute("show tables")
    existing = {r[0] for r in cur.fetchall()}
    lines.append("")
    for table in TABLES:
        if table not in existing:
            lines.append(f"【{table}】不存在，建表失败")
            continue
        cur.execute(f"desc `{table}`")
        cols = cur.fetchall()
        cur.execute(f"select count(*) from `{table}`")
        rows = cur.fetchone()[0]
        lines.append(f"【{table}】已建好，{len(cols)} 个字段，当前 {rows} 行")
        for c in cols:
            lines.append(f"    {c[0]:<16}{c[1]:<14}{c[2]}")

    cur.close()
    conn.close()

    with open("data/_m1_tables.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("init review tables done")


if __name__ == "__main__":
    main()
