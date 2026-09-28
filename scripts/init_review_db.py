# -*- coding: utf-8 -*-
"""
建评审会三张表：review_session / review_question / review_score
读取同目录下的 init_review_tables.sql 执行，全部 IF NOT EXISTS，可重复运行
执行后打印每张表的字段，确认真的建好了

另外补一次增量迁移：CREATE TABLE IF NOT EXISTS 对已经建好的表不会加字段，
所以后面新加的列要在这里单独补，否则老库跑起来会报 Unknown column
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.utils.mysql_util import get_mysql_conn

SQL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "init_review_tables.sql")
TABLES = ["review_session", "review_question", "review_score"]

# 后加的字段: (表, 字段, 建列语句)。已经有的库靠这里补上
ADDED_COLUMNS = [
    ("review_question", "verdict_comment",
     "ALTER TABLE review_question ADD COLUMN verdict_comment VARCHAR(255) NOT NULL DEFAULT '' "
     "COMMENT '判定理由，未答好清单里要显示' AFTER verdict"),
]

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

    # 增量补列。MySQL 没有 ADD COLUMN IF NOT EXISTS（那是 MariaDB 的语法），
    # 所以先查 information_schema 再决定要不要 ALTER
    for table, column, ddl in ADDED_COLUMNS:
        cur.execute(
            "select count(*) from information_schema.columns "
            "where table_schema = database() and table_name = %s and column_name = %s",
            (table, column),
        )
        if cur.fetchone()[0]:
            lines.append(f"【{table}.{column}】已存在，跳过")
            continue
        cur.execute(ddl)
        conn.commit()
        lines.append(f"【{table}.{column}】老库缺这一列，已补上")

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
