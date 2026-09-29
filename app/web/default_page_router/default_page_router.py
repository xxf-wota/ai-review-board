from fastapi import APIRouter
from fastapi.responses import RedirectResponse


"""
默认页面路由
两个智能体（模拟面试 / 交叉质询评审团）现在是同一个页面里的两个界面，
默认落在模拟面试那一侧。

进主界面之前先过登录页：登录页自己会看本地有没有登录状态，登过了就直接放行，
所以这里无脑往 login.html 跳就行，不会来回打转。
"""
default_page_router = APIRouter()


# 重定向
@default_page_router.get("/")
def default_page():
    return RedirectResponse(url="/static/login.html")


# 显式要登录页的时候（比如退出登录）
@default_page_router.get("/login")
def login_page():
    return RedirectResponse(url="/static/login.html")
