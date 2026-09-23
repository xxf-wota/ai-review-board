from  app.ai.agent.memory.save.window_memory import WindowMemory
from  app.ai.agent.memory.save.summary_memory import SummaryMemory
from  app.ai.agent.memory.manager.prompt_builder import PromptBuilder
from  app.ai.agent.memory.save.long_memory import LongMemory
from  app.ai.agent.memory.save.profile_memory import ProfileMemory
"""
 会话管理器:主要负责四层记忆的对象创建和提示词的生成
"""
class SessionManager:

     def __init__(self,session_id:str,user_id):
         self.window_memory = WindowMemory(session_id)
         self.session_id = session_id
         self.prompt_builder = PromptBuilder(self.session_id,user_id)
         self.summary_memory = SummaryMemory(self.session_id)
         self.long_memory = LongMemory()
         self.profile_memory =ProfileMemory(user_id)
         #self.agent = SummaryAgent(elf.summary_memory )
     #添加窗口记忆
     async def save(self,role:str,content:str):
         await  self.window_memory.save(role,content)
     #构建提示词
     async def build_prompt(self,user_id,question):
         print("构建提示词")
         prompt = await self.prompt_builder.builder_prompt(user_id,question)
         print("提示词构建完成")
         return {"role":"system","content":prompt}




