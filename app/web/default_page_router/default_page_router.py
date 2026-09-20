from fastapi import APIRouter
from fastapi.responses import RedirectResponse


"""
默认页面路由
"""
default_page_router = APIRouter()
# 重定向
@default_page_router.get("/")
def default_page():
    return RedirectResponse(url="/static/chat.html")
