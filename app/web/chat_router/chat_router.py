from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import json

from app.test.test_agent import test_agent
from app.ai.agent.vosk_agent import VostAgent

chat_router = APIRouter()

# 创建语音识别实例
# vosk_agent = VostAgent.get_vosk()


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


# 语音识别接口
# @chat_router.websocket("/vosk")
# async def vosk(ws: WebSocket):
#     try:
#         # 第一次握手
#         await ws.accept()
#         print("第一次握手，创建链接")
#         # 开启语音
#         await vosk_agent.speak(ws)
#     except Exception as e:
#         print("异常:", e)
