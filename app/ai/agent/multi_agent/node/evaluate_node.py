from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_stream_writer

from app.ai.agent.multi_agent.state.exam_state import ExamState
from app.ai.model.my_model import MyModel
from app.ai.prompt.builder_prompt import BuilderPromptYaml
from langchain.agents import create_agent
"""
评价节点，根据用户答案，评价考试结果
"""
# 提取提示词
prompt = BuilderPromptYaml.get_prompt("evaluate_node.yaml")
async def evaluate_node(state:ExamState):
    # 获取问题列表
    question_list = state["questions"]
    # 获取用户答案
    user_answer_list = state["user_answer_list"]
    # 构建提问内容
    question = f"用户问题列表：{question_list}\n用户答案列表：{user_answer_list}"
    # 调用模型
    model = MyModel.get_model()
    agent = create_agent(
        model=model,
        system_prompt=prompt
    )
    # 提问
    user_msg = {"messages": [HumanMessage(content=question)]}
    # 使用一个结果列表存储模型回复
    result = []
    # 获取写入对象
    """
    每次调用writer(content)会将内容实时推送到SSE流或WebSocket中
    """
    writer = get_stream_writer()
    async for chunk, metadata in agent.astream(user_msg, stream_mode="messages"):
        if chunk.content:
            result.append(chunk.content)
            writer(chunk.content)
    ai_msg = ""
    return {"messages": [AIMessage(content=ai_msg)], "exam_step": "done"}