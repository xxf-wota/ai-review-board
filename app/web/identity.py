"""
接口里的"我是谁"

一条规矩：**身份只由服务端认定**。客户端把登录时拿到的 `session_id`（`session:<hex>`）
原样带回来，服务端拿它去 Redis 反查这份会话属于谁（`ExamGraphAgent.session_owner()`）——
客户端自称"我是某某"一律不算数，否则谁都能填别人的邮箱，把数据写到别人名下、再读走别人的东西。

登录会话本身就是凭据：32 位随机 hex、Redis 里带着 user_id、一天过期。
所以不需要另造一套 token —— `/auth/login` 建会话时就把它记下来了，这里只是把那半张表用起来。
"""
from fastapi import HTTPException, Request

from app.ai.tool.review_dao import session_owner as review_session_owner


async def caller_id(request: Request, login_session_id: str) -> str:
    """
    把"登录会话"换成"账号"。换不出来就 401。

    缺凭据和凭据过期不做区分：对调用方来说要做的事都一样（回登录页重新登录）
    """
    agent = getattr(request.app.state, "exam_agent", None)
    if agent is None:
        # Redis 没连上或面试智能体没就绪：这是服务端的问题，不该报 401 骗人去重新登录
        raise HTTPException(status_code=503, detail="登录校验暂时不可用，请稍后再试")
    user_id = await agent.session_owner((login_session_id or "").strip())
    if not user_id:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user_id


def deny_if_not_review_owner(session_id: str, caller: str) -> None:
    """
    评审会的归属校验：有主且不是这个账号 → 403。

    没有归属信息可查时放过（表里没这一行，或者 `student_id` 是空的 ——
    改动之前提交的会没写这一列）。那种情况下除了"知道 session_id"本身没有别的凭据可比，
    拦下来只会让老会话连开过它的人自己都进不去；所以按幂等放行，并在 README 的已知事项里记着
    """
    owner = review_session_owner(session_id)
    if owner and owner != caller:
        raise HTTPException(status_code=403, detail="这场评审会不是这个账号开的")
