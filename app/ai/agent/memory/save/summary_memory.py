import os

from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool

load_dotenv()

"""
短期记忆之二：摘要记忆
把越来越长的对话压成一段摘要，避免窗口记忆丢掉早期信息
一个会话一行，存在 PostgreSQL 的 conversation_summary 表里
"""

# 连接池，由 main.py 启动时 open()，关闭时 close()
# 连接串从环境变量读（原参考代码把密码写死在文件里，和 .env 对不上，所以改成读配置）
pool = AsyncConnectionPool(
    conninfo=os.getenv("POSTGRESQL_URL"),
    max_size=20,
    min_size=1,
    open=False,
    timeout=5,  # 从池里取连接的等待超时
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_summary (
    session_id  VARCHAR(128) PRIMARY KEY,
    summary     TEXT,
    create_time TIMESTAMP DEFAULT NOW(),
    update_time TIMESTAMP DEFAULT NOW()
)
"""


# 建摘要表，幂等，main.py 启动时调一次
async def ensure_table():
    async with pool.connection() as con:
        async with con.cursor() as cur:
            await cur.execute(CREATE_TABLE_SQL)
        await con.commit()


class SummaryMemory:

    def __init__(self, session_id: str):
        self.session_id = session_id

    async def save(self, summary: str):
        async with pool.connection() as con:
            async with con.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO conversation_summary(session_id, summary)
                    VALUES(%s, %s)
                    ON CONFLICT(session_id)
                    DO UPDATE SET summary = EXCLUDED.summary, update_time = NOW()
                    """,
                    (self.session_id, summary),
                )
            await con.commit()

    async def query(self):
        async with pool.connection() as con:
            async with con.cursor() as cur:
                await cur.execute(
                    "SELECT summary FROM conversation_summary WHERE session_id = %s",
                    (self.session_id,),
                )
                rs = await cur.fetchone()
                return rs[0] if rs else ""
