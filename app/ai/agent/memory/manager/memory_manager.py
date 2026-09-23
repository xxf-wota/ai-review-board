from app.ai.agent.memory.retrieval.SummaryAgent import SummaryAgent
from  app.ai.agent.memory.manager.session_mananger import SessionManager
from  app.ai.agent.memory.retrieval.long_agent import LongAgent
from  app.ai.agent.memory.retrieval.profile_agent import ProfileAgent

"""
记忆管理器，管理记忆的更新
"""
class MemoryManager:

   def __init__(self,sessionManger:SessionManager):
       #摘要智能体
       self.summary_agent = SummaryAgent(sessionManger.summary_memory)
       #获取窗口记忆对象
       self.window_memory = sessionManger.window_memory
       #创建长期记忆智能体
       self.long_agent = LongAgent(sessionManger.long_memory)
       #创建用户画像智能体
       self.profile_agent = ProfileAgent(sessionManger.profile_memory)

   async def update(self,user_id,question):
       print("===更新长期记忆")
       #获取查询的窗口记忆
       query_window = await self.window_memory.query()
       #取出用户上下信息
       prompt =""
       if query_window:
           for i in query_window:
               if i["role"] == "user":
                   prompt += f"用户提问：{i['content']}"
               else:
                   prompt += f"AI回复：{i['content']}"
       #更新长期记忆
       await self.long_agent.update(user_id,prompt)
       #更新用户画像记忆
       await self.profile_agent.update(prompt)
       print(f"===窗口记忆长度：{len(query_window)}")
       if len(query_window) >=2:
           print("===更新摘要记忆")
           await self.summary_agent.update(query_window)