from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from  app.ai.model.my_model import MyModel
from  app.ai.agent.memory.save.summary_memory import SummaryMemory
"""
摘要智能体
"""
class SummaryAgent:

    def __init__(self,summary_memory:SummaryMemory):
        self.model = MyModel.get_local_model()
        self.prompt = self.get_prompt()
        self.agent = self.get_agent()
        #获取摘要记忆的保存对象
        self.summary_memory = summary_memory
    def get_prompt(self):
        self.prompt ="""
一: 角色：你是面试摘要助手
二：任务：根据旧摘要和最新问答生成新摘要
三：规则：
  1、对出过的题目做个总结，对用户的答案做个总结
  2、保留：面试官（AI）问过的关键题目，避免重复问
  3、去掉闲聊
  4、避免重复，2000字以内
  5、使用第三人称
  6、只返回新摘要
        """
        return self.prompt
    def get_agent(self):
        self.agent = create_agent(
            model = self.model,
            system_prompt=self.prompt,
            tools=[],
            debug=True

        )
        return self.agent

    #记忆更新
    #messages 窗口记忆的消息
    async def update(self,messages):
        #查询旧摘要
        old_summary_memory = await self.summary_memory.query()
        #最新聊天记录
        prompt = ""
        for i in messages:
            if i["role"] == "user":
                prompt += f"用户提问：{i['content']}"

        question=f"旧摘要:{old_summary_memory}和最新聊天记录:{prompt}"
        print("开始更新摘要记忆")
        print(f"摘要问题:{question}")
        #提问
        rs = await self.agent.ainvoke({"messages":[HumanMessage(content=question)]})
        print("摘要记忆结束")
        #把新摘要存入到摘要记忆中
        await self.summary_memory.save(rs["messages"][-1].content)
if __name__ =="__main__":
    summary_memory = SummaryMemory("001")
    s = SummaryAgent(summary_memory)
    #最近聊天记录
    record = [{'role': 'user', 'content': '你好，我叫张胜男'}, {'role': 'ai', 'content': '你好，张胜男'}, {'role': 'user', 'content': 'langchain是什么'}, {'role': 'ai', 'content': 'langchain是一个智能体框架'}]
    s.update(record)




