from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from  app.ai.model.my_model import MyModel
from  app.ai.agent.memory.save.long_memory import LongMemory

"""
长期记忆智能体
"""
class LongAgent:

    def __init__(self, long_memory: LongMemory):
        self.model = MyModel.get_local_model()
        self.prompt = self.get_prompt()
        self.agent = self.get_agent()
        # 获取长期记忆的保存对象
        self.long_memory = long_memory

    def get_prompt(self):
        self.prompt = """
     一: 角色：你是面试者的能力画像助手
二：任务：提取面试者稳定的能力特征与面试表现
三：规则：
  1、记录：擅长的技术/领域、做过的项目亮点、表达与沟通特点
  2、记录：本轮暴露的薄弱点
  3、去掉闲聊和一次性的寒暄
  4、避免重复，200字以内
  5、没有信息就返回空字符串/0
    
        """
        return self.prompt

    def get_agent(self):
        self.agent = create_agent(
            model=self.model,
            system_prompt=self.prompt,
            tools=[],
            debug=True

        )
        return self.agent

    # 记忆更新
    async def update(self, user_id,question):
        print("开始更新长期记忆")
        # 提问
        rs = await self.agent.ainvoke({"messages": [HumanMessage(content=question)]})
        print("更新长期记忆结束")
        # 把新摘要存入到摘要记忆中
        await self.long_memory.save(user_id,rs["messages"][-1].content)


if __name__ == "__main__":
    Long_memory =LongMemory()
    agent = LongAgent(Long_memory)
    q1="我喜欢打游戏"
    q2="我擅长编程"
    agent.update(1,q2)








