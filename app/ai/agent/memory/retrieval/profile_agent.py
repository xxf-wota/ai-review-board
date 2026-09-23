from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from  app.ai.model.my_model import MyModel
from  app.ai.agent.memory.save.profile_memory import ProfileMemory
from pydantic import BaseModel,Field

from pydantic import BaseModel, Field
from typing import List

class ProfileParams(BaseModel):
    name: str = Field(default="", description="候选人姓名")
    age: int = Field(default=0, description="年龄，未提及填0")
    education: str = Field(default="", description="学历，如本科、硕士")
    major: str = Field(default="", description="所学专业")
    target_position: str = Field(default="", description="目标岗位")
    target_city: str = Field(default="", description="期望工作城市")
    skills: str = Field(default="", description="技能，逐项拆分")
    experiences: str = Field(default="", description="项目/工作经历，每条一句")
    weaknesses: str = Field(default="", description="自述短板")
    interview_preference: str = Field(default="", description="面试偏好")

"""
用户画像智能体
"""
class ProfileAgent:

    def __init__(self, profile_memory: ProfileMemory):
        self.model = MyModel.get_model()
        self.prompt = self.get_prompt()
        self.agent = self.get_agent()
        # 获取用户画像记忆的保存对象
        self.profile_memory = profile_memory

    def get_prompt(self):
        self.prompt = """
        一、角色：
            你是「AI模拟面试系统」中的面试官画像提取助手。
            系统会随机出题，用户（候选人）正在回答这些题目。
            你的任务是从候选人的「回答内容」中，抽取与面试相关的关键信息，用于更新用户画像。

        二、任务：
            从候选人对面试题的回答中，提取结构化的面试画像信息。
            注意：候选人不是在自我介绍，而是在答题。你需要从答案中「间接推断」画像，而不是要求他直接说出。

        三、字段定义：
            1. name：姓名
            2. age：年龄（未提及填 0）
            3. education：学历（本科/硕士/博士等）
            4. major：专业
            5. target_position：目标岗位
            6. target_city：期望工作城市
            7. skills：已掌握的技能/技术栈（列表，逐项拆分）
            8. experiences：项目或工作经历（列表，每条一句话）
            9. weaknesses：候选人自述的短板（列表）
            10. interview_preference：面试偏好（如"多问项目""别问算法"）

        四、提取规则：
            1. 只提取能从回答中明确得到的信息，不要编造、不要推测、不要补全。
            2. 未提及的字段：字符串填 ""，整型填 0。
            3. 使用第三人称客观陈述（如"候选人掌握 Java"）。
            4. 同一信息只记录一次，避免重复。
            5. 如果回答只是单纯答题、没有暴露任何画像信息，则所有字段留空。
            6. 不要做总结、不要加解释、不要追问。
        五、输出要求：
            1. 只输出结构化结果本身，不要追加任何"如果需要……""希望有帮助"等收尾语。
            2. 输出完立即停止。
        六、示例：
            输入（题目：请介绍一个你参与过的项目）：
                我在上一家公司做过一个订单系统，用 Spring Boot + MySQL + Redis，主要负责下单和库存模块。
            提取：
                skills = "Spring Boot", "MySQL", "Redis"
                experiences = "在上一家公司做过订单系统，负责下单和库存模块"

            输入（题目：谈谈你的缺点）：
                我算法基础比较弱，刷题不多。
            提取：
                weaknesses = "算法基础比较弱，刷题不多"

            输入（题目：你为什么想来我们公司）：
                我觉得你们公司技术氛围好。
            提取：无画像相关信息，所有字段留空。
        """
        return self.prompt


    def get_agent(self):
        self.agent = create_agent(
            model=self.model,
            system_prompt=self.prompt,
            tools=[],
            debug=True,
            response_format=ProfileParams
        )
        return self.agent

    # 记忆更新
    async def update(self, question):
        try:
            print("开始用户画像记忆")
            # 提问
            rs = await self.agent.ainvoke({"messages": [HumanMessage(content=question)]})
            print("用户画像记忆结束")
            data = rs["structured_response"].model_dump()
            print(data)
            # 查询出是否有新的画像信息
            for key, value in data.items():
                if value:
                    await  self.profile_memory.save(key, value)
        except Exception as e:
            print(e)

if __name__ == "__main__":
    Long_memory =  ProfileMemory(1)
    agent = ProfileAgent(Long_memory)
    q1 = "我喜欢打游戏"
    q2 = "我擅长编程"
    q3="我是李四"
    q4="我今年24岁"
    agent.update( q2)








