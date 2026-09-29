from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import json

from app.ai.tool.review_dao import delete_session as delete_review_session

chat_router = APIRouter()


class SessionSchema(BaseModel):
    session_id: str = None
    user_id: str = None


# 创建 / 校验会话
@chat_router.post("/create_session")
async def create_session(request: Request, session: SessionSchema):
    print(f"用户ID:{session.user_id},会话ID:{session.session_id}")
    exam_agent = request.app.state.exam_agent
    user_id = session.user_id or "1"

    # 前端带了 session_id：校验存在且属于该用户，通过就直接复用
    if session.session_id:
        ok, reason = await exam_agent.check_session(user_id, session.session_id)
        if ok:
            return {"code": 200, "msg": "会话有效", "data": session.session_id}
        # 过期了或者不是这个用户的，另建一个新的，不让请求卡住
        new_id = await exam_agent.create_session(user_id)
        return {"code": 200, "msg": f"{reason}，已新建会话", "data": new_id}

    # 没带 session_id，直接建新的
    new_id = await exam_agent.create_session(user_id)
    return {"code": 200, "msg": "新建会话", "data": new_id}


# 删除一场会话：面试留下的（Redis 会话记录 / 窗口记忆 / 会话锁 / PostgreSQL 摘要 / 检查点）
# 和评审会留下的（MySQL 的 review_session + review_question）一起清，两边用同一个 session_id。
# 前端侧栏每条记录上的 ✕ 走的就是这里
@chat_router.delete("/session/{session_id}")
async def delete_session(request: Request, session_id: str, user_id: str = ""):
    session_id = (session_id or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")

    exam_agent = request.app.state.exam_agent

    # 归属校验：Redis 里还留着的会话记录能证明它是谁的，不是这个用户的直接拒。
    # 记录过期了、或者这本来就是一场评审会（它的 id 是裸 hex，从来不进 Redis），
    # 就没有归属信息可查，按幂等删除处理 —— 删一个已经不在的会话不该报错
    ok, reason = await exam_agent.check_session(user_id, session_id)
    if not ok and reason == "会话不属于该用户":
        raise HTTPException(status_code=403, detail=reason)

    report = await exam_agent.purge_session(session_id)
    report.update(delete_review_session(session_id))
    print(f"删除会话：{session_id}（用户 {user_id}）-> {report}")
    return {"code": 200, "msg": "会话已删除", "data": report}


# 聊天接口
@chat_router.get("/chat")
async def chat(request: Request, question: str, user_id: str = "1", session_id: str = None):
    print(f"用户问题:{question},用户ID:{user_id},会话ID:{session_id}")
    exam_agent = request.app.state.exam_agent

    async def generate():
        try:
            # 没带会话就现建一个，保证直接调这个接口也能用
            # （前端应该先调 /create_session 并把 session_id 存下来，否则每句话都是新会话，没有记忆）
            sid = session_id
            if not sid:
                sid = await exam_agent.create_session(user_id)
                print(f"请求没带 session_id，已自动新建：{sid}")
            async for x in exam_agent.chat(question, user_id, sid):
                # False 表示流式未结束
                data = {"data": x, "done": False}
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            # 流式结束
            data = {"data": "", "done": True}
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        except Exception as e:
            print(f"流式异常:{e}")
            # 流式结束
            data = {"data": "流式异常", "done": True}
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
