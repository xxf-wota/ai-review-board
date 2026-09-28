from fastapi import APIRouter
from fastapi.responses import RedirectResponse


"""
默认页面路由
两个智能体（模拟面试 / 交叉质询评审团）现在是同一个页面里的两个界面，
默认落在模拟面试那一侧
"""
default_page_router = APIRouter()
# 重定向
@default_page_router.get("/")
def default_page():
    return RedirectResponse(url="/static/app.html")
