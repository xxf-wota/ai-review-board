from fastapi.responses import StreamingResponse
from fastapi import APIRouter, WebSocket, Request
import json
from app.test.test_agent import test_agent
from app.ai.agent.vosk_agent import VostAgent

chat_router = APIRouter()

# 创建语音识别实例
# vosk_agent = VostAgent.get_vosk()

#聊天接口
@chat_router.get("/chat")
async def chat(request:Request,question:str,user_id:str=None):
    print(f"用户问题:{question},用户ID:{user_id}")
    exam_agent = request.app.state.exam_agent
        # 定义一个异步迭代七
    async def generate(question, user_id, session_id):
        try:

            async for x in exam_agent.chat(question,user_id,"001"):
                # False 流式未结束
                data = {"data": x, "done": False}
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            # 流式结束
            data = {"data": "", "done": True}
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        except Exception as e:
            # 流式结束
            data = {"data": "流式异常", "done": True}
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(question,user_id, "001"),media_type="text/event-stream")


# 语音识别接口
# @chat_router.websocket("/vosk")
# async def vosk(ws: WebSocket):
#     try:
#         # 第一次握手
#         await ws.accept()
#         print("第一次握手，创建链接")
#         # 开启语音
#         await vosk_agent.speak(ws)
#         # while True:
#         #     # 接收客户端发送的数据
#         #     await ws.receive_text()
#     except Exception as e:
#         print("异常:", e)