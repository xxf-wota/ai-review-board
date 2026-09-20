from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_stream_writer
from app.ai.agent.multi_agent.state.exam_state import ExamState
from app.ai.model.my_model import MyModel
from app.ai.prompt.builder_prompt import BuilderPromptYaml
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
"""
聊天加记忆的节点
"""
"""
用本地模型重试不行就换商用模型，再不行就只能抛出超时异常，避免无限循环重试卡死
"""
# 提取提示词
prompt = BuilderPromptYaml.get_prompt("chat_node.yaml")
async def chat_node(state:ExamState):
    try:
        # 获取用户问题和记忆信息
        memory = state["messages"]
        # 调用模型
        model = MyModel.get_local_model()
        agent = create_agent(
            model=model,
            system_prompt=prompt,
            middleware=[
                ModelCallLimitMiddleware(
                    thread_limit=3, # 允许重试3次
                    exit_behavior="end", # 超过重试次数后，结束智能体
                )
            ]
        )
        user_msg = {"messages": memory}
        # 使用一个结果列表存储模型回复
        result = []
        # 获取写入对象
        writer = get_stream_writer()
        async for chunk, metadata in agent.astream(user_msg, stream_mode="messages"):
            if chunk.content:
                result.append(chunk.content)
                writer(chunk.content)
        ai_msg = ""
        return {"messages": [AIMessage(content=ai_msg)], "exam_step": "done"}
    except Exception as e:
        print("-----------重试兜底失败，降级处理------------")
        try:
            # 获取用户问题和记忆信息
            memory = state["messages"]
            # 调用模型
            model = MyModel.get_model()
            agent = create_agent(
                model=model,
                system_prompt=prompt,
                middleware=[
                    ModelCallLimitMiddleware(
                        thread_limit=3,  # 允许重试3次
                        exit_behavior="end",  # 超过重试次数后，结束智能体
                    )
                ]
            )
            user_msg = {"messages": memory}
            # 使用一个结果列表存储模型回复
            result = []
            # 获取写入对象
            writer = get_stream_writer()
            async for chunk, metadata in agent.astream(user_msg, stream_mode="messages"):
                if chunk.content:
                    result.append(chunk.content)
                    writer(chunk.content)
            ai_msg = ""
            return {"messages": [AIMessage(content=ai_msg)], "exam_step": "done"}
        except Exception as e:
            print("-----------超时------------")
            return {"messages": [AIMessage(content="\n请求超时，请稍后重试\n")], "exam_step": "done"}


