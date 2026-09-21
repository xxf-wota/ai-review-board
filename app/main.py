from fastapi import FastAPI
import uvicorn as uv
from app.web.chat_router.chat_router import chat_router
from fastapi.staticfiles import StaticFiles
from app.web.websocket_router.websocket_router import wb_router
from contextlib import asynccontextmanager
from app.ai.agent.multi_agent.graph.exam_graph import ExamGraph
from app.ai.agent.review_agent.graph.review_graph import ReviewGraph
from app.web.default_page_router.default_page_router import default_page_router
from app.web.review_router.review_router import review_router
from langgraph.checkpoint.memory import InMemorySaver

# 生命周期，用于在服务器启动时就创建好对象
@asynccontextmanager
async def content_manager(app: FastAPI):
    # 配置检查点
    memory = InMemorySaver()
    app.state.exam_agent = ExamGraph(memory)
    print("AI模拟面试智能体启动成功")
    # 评审会复用同一套检查点机制
    app.state.review_agent = ReviewGraph(InMemorySaver())
    print("AI交叉质询评审团启动成功")
    yield
    # 消耗对象
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