from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage
from app.ai.agent.multi_agent.schema.intent_schema import RouteSchema
from app.ai.model.my_model import MyModel
from app.ai.prompt.builder_prompt import BuilderPromptYaml
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
"""
路由判断节点
"""
#读取外部配置文件
prompt = BuilderPromptYaml.get_prompt("router_agent.yaml")
def router_agent(question: str):
    try:
        #获取本地模型
        model = MyModel.get_local_model()
        #创建智能体
        agent = create_agent(
            model=model,
            system_prompt=prompt,
            response_format=ProviderStrategy(schema=RouteSchema),
            middleware=[
                ModelCallLimitMiddleware(
                    thread_limit=3,
                    exit_behavior="end"
                )
            ]
        )
        #提问
        user_msg = {"messages":[HumanMessage(content=question)]}
        rs = agent.invoke(user_msg)
        #把输入结果转换成字典
        data = rs["structured_response"].model_dump()
        #更新状态，这里也是一个兜底，如果模型识别不出来，就默认是聊天
        return data.get("route", "chat")
    except Exception as e:
        try:
            print("-----------路由判断节点异常------------")
            # 获取商用模型
            model = MyModel.get_model()
            # 创建智能体
            agent = create_agent(
                model=model,
                system_prompt=prompt,
                response_format=ProviderStrategy(schema=RouteSchema),
                middleware = [
                    ModelCallLimitMiddleware(
                        thread_limit=3,
                        exit_behavior="end"
                    )
                ]
            )
            # 提问
            user_msg = {"messages": [HumanMessage(content=question)]}
            rs = agent.invoke(user_msg)
            # 把输入结果转换成字典
            data = rs["structured_response"].model_dump()
            # 更新状态，这里也是一个兜底，如果模型识别不出来，就默认是聊天
            return data.get("route", "chat")
        except Exception as e:
            print("-----------路由判断节点异常，降级处理------------")
            return "chat"
