# -*- coding: utf-8 -*-
"""
会话管理 + 记忆基础设施验收

验证内容：
  1. 应用能启动（PostgreSQL 检查点 + 摘要记忆连接池 + Redis）
  2. POST /create_session 能建会话
  3. 同用户复用会话、换用户抢同一会话会被拒绝
  4. 摘要表 conversation_summary 由 ensure_table() 自动创建
  5. 检查点表由 saver.setup() 自动创建
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# psycopg 的异步模式跑不了 Windows 默认的 ProactorEventLoop，必须换成 Selector
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

REPORT = os.path.join(ROOT, "data", "_memory_check.txt")
_lines = []


def log(text=""):
    _lines.append(str(text))
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    print(str(text)[:170].encode("ascii", "replace").decode("ascii"))


async def pg_tables():
    import psycopg
    url = os.getenv("POSTGRESQL_URL")
    async with await psycopg.AsyncConnection.connect(url) as con:
        async with con.cursor() as cur:
            await cur.execute(
                "select tablename from pg_tables where schemaname='public' order by tablename"
            )
            return [r[0] for r in await cur.fetchall()]


async def main():
    log("=" * 70)
    log("会话管理 + 记忆基础设施验收")
    log("=" * 70)

    # 先删掉摘要表，验证 ensure_table() 真的会把它建回来
    import psycopg
    async with await psycopg.AsyncConnection.connect(os.getenv("POSTGRESQL_URL")) as con:
        async with con.cursor() as cur:
            await cur.execute("drop table if exists conversation_summary")
        await con.commit()
    log("\n已手动删除 conversation_summary 表，看启动时会不会自动建回来")

    # 启动应用（lifespan 会做：建检查点、开连接池、建摘要表）
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        log("\n--- 启动完成 ---")
        tables = await pg_tables()
        log(f"数据库里的表：{tables}")

        ok_summary = "conversation_summary" in tables
        log(f"  ensure_table() 自动建了摘要表：{'通过' if ok_summary else '不通过'}")

        ckpt = [t for t in tables if t.startswith("checkpoint")]
        log(f"  saver.setup() 建了检查点表：{'通过' if ckpt else '不通过'}（{ckpt}）")

        # 会话
        log("\n--- 会话 ---")
        r1 = client.post("/create_session", json={"user_id": "u1"}).json()
        sid = r1["data"]
        log(f"  {r1['msg']}：{sid}")

        r2 = client.post("/create_session", json={"user_id": "u1", "session_id": sid}).json()
        log(f"  同一用户带上会话再请求：{r2['msg']}")
        ok_reuse = r2["data"] == sid

        r3 = client.post("/create_session", json={"user_id": "u2", "session_id": sid}).json()
        log(f"  换个用户抢同一会话：{r3['msg']}")
        ok_deny = r3["data"] != sid

        # 确认会话真的落在 Redis 里、带过期时间
        import redis.asyncio as aioredis
        rr = aioredis.StrictRedis(host="localhost", port=6379, db=0)
        raw = await rr.get(sid)
        ttl = await rr.ttl(sid)
        log(f"\n  Redis 里的会话内容：{raw.decode() if raw else '（没有）'}")
        log(f"  剩余有效期：{ttl} 秒")

        log("\n--- 检查项 ---")
        log(f"  应用启动（PostgreSQL 检查点 + 连接池）：通过")
        log(f"  摘要表自动创建：{'通过' if ok_summary else '不通过'}")
        log(f"  检查点表存在：{'通过' if ckpt else '不通过'}")
        log(f"  同用户复用会话：{'通过' if ok_reuse else '不通过'}")
        log(f"  换用户被拒绝：{'通过' if ok_deny else '不通过'}")
        log(f"  会话写入 Redis 且带 TTL：{'通过' if raw and ttl > 0 else '不通过'}")


if __name__ == "__main__":
    asyncio.run(main())
