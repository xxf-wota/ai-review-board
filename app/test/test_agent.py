from app.ai.tool.send_email_tool import send_email_tool
from app.ai.model.my_model import MyModel
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
import asyncio
# 创建智能体
async def test_agent(question: str):
    # 使用的大模型
    model = MyModel.get_local_model()
    # 使用的工具
    tools = [send_email_tool]
    # 系统提示词
    prompt = """
        一 角色： 你是一个邮件发送助手
    """

    # 创建智能体
    agent = create_agent(
        tools=tools,
        model=model,
        system_prompt=prompt,
        # debug=True, # 开启调试模式，打印智能体的思考过程
    )

    # 提问方式，两种
    # message = {"messages": [{"role": "user", "content": question}]}
    message = {"messages": [HumanMessage(content=question)]}
    async for chunk, metadata in agent.astream(message, stream_mode="messages"):
        if chunk.content:
            yield chunk.content


# 测试大模型的astream方法，是否可以正常工作
async def test_create_model(question: str):
    model = MyModel.get_local_model()
    async for chunk in model.astream(question):
        if chunk.content:
            yield chunk.content


if __name__ == '__main__':
    question = "发送邮件给3538247253@qq.com，主题为你在干嘛，内容自行填写"


    # 测试智能体
    async def test_create_email_tool():
        async for chunk in create_email_tool(question):
            print(chunk, end="")
    # 调用此方法必须时函数
    asyncio.run(test_create_email_tool())


