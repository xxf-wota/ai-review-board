import asyncio
import os
import sys
from contextlib import asynccontextmanager

import redis.asyncio as redis
import uvicorn as uv
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.ai.agent.memory.save.summary_memory import ensure_table, pool
from app.ai.agent.multi_agent.graph.exam_graph_agent import ExamGraphAgent
from app.ai.agent.review_agent.graph.review_graph import ReviewGraph
from app.web.chat_router.chat_router import chat_router
from app.web.default_page_router.default_page_router import default_page_router
from app.web.review_router.review_router import review_router
from app.web.websocket_router.websocket_router import wb_router

load_dotenv()
DB_URI = os.getenv("POSTGRESQL_URL")

# psycopg 的异步模式跑不了 Windows 默认的 ProactorEventLoop，必须换成 Selector
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# 生命周期，用于在服务器启动时就创建好对象
@asynccontextmanager
async def content_manager(app: FastAPI):
    # 图状态检查点存 PostgreSQL：重启不丢，多进程也能共享
    async with AsyncPostgresSaver.from_conn_string(DB_URI) as saver:
        await saver.setup()  # 幂等，首次会建表

        # Redis：会话、会话锁、窗口记忆、用户画像
        r = redis.StrictRedis(host="localhost", port=6379, db=0)

        # 模拟面试：带会话管理、异步会话锁、四层记忆
        app.state.exam_agent = ExamGraphAgent(saver, r)
        print("AI模拟面试智能体启动成功")

        # 评审会：图状态先用内存，等 M4 要跨请求续接时再换
        app.state.review_agent = ReviewGraph(InMemorySaver())
        print("AI交叉质询评审团启动成功")

        # 摘要记忆的连接池
        await pool.open()
        await ensure_table()

        yield

        # 消耗对象
        await pool.close()
        app.state.exam_agent = None
        app.state.review_agent = None
        print("AI模拟面试智能体关闭成功")


app = FastAPI(lifespan=content_manager)
# 配置默认页面路由
app.include_router(default_page_router)
# 配置子路由
app.include_router(chat_router)
app.include_router(wb_router)
# 配置评审会路由
app.include_router(review_router)

# 配置静态文件
app.mount("/static", StaticFiles(directory="app/html"), name="static")


if __name__ == '__main__':
    uv.run(app, host="localhost", port=8000)
