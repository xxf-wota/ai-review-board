from pydantic import BaseModel, Field
from app.ai.agent.multi_agent.schema.intent_schema import IntentSchema
from app.ai.agent.multi_agent.state.exam_state import ExamState
from app.ai.model.my_model import MyModel
from app.ai.prompt.builder_prompt import BuilderPromptYaml
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain.agents.structured_output import ProviderStrategy
"""
意图识别节点
"""
#读取外部配置文件
prompt = BuilderPromptYaml.get_prompt("intent_node.yaml")
def intent_node(state:ExamState):
    #获取用户输入问题
    user_input = state["messages"][0].content
    #获取本地模型
    model = MyModel.get_local_model()
    #创建智能体
    agent = create_agent(
        model =model,
        system_prompt=prompt,
        response_format=ProviderStrategy(schema=IntentSchema)
    )
    #提问
    user_msg = {"messages":[HumanMessage(content=user_input)]}
    rs = agent.invoke(user_msg)
    #把输入结果转换成字典
    data = rs["structured_response"].model_dump()
    #自定义AI回复消息
    ai_msg =f"\n意图识别成功,课程名称:{data["course"]},课程数量:{data["num"]}\n"
    #更新状态
    return {
         "messages":[AIMessage(content=ai_msg)],
         "course":data["course"],
         "total":data["num"],
         "exam_step":"intent",
    }