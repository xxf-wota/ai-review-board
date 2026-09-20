from fastapi import APIRouter, WebSocket

wb_router = APIRouter()

"""
在websocket中没有返回值，只能通过send_text()或其他方法发送文本
"""
@wb_router.websocket("/ws")
async def ws(websocket: WebSocket):
    # 接受websocket连接
    await websocket.accept()
    print("客户端已连接")
    while True:
        # 接收客户端发送的消息
        message = await websocket.receive_text()
        print(f"收到客户端消息: {message}")
        # 发送消息给客户端
        await websocket.send_text(f"服务器消息: {message}")
