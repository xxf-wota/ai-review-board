from pydantic import BaseModel, Field

"""
方案要素抽取结果
missing 是本系统提问的依据：评审优先追问方案里压根没写的东西
"""


class PlanElementsSchema(BaseModel):
    goal: str = Field(..., description="一句话项目目标：要解决什么问题、给谁用")
    tech: list[str] = Field(default_factory=list, description="技术方案要点")
    budget: str = Field(..., description="预算与资源说明，原文没写填 未提及")
    schedule: str = Field(..., description="进度安排，原文没写填 未提及")
    risks: list[str] = Field(default_factory=list, description="方案中已识别的风险")
    compliance: str = Field(..., description="数据来源、隐私、授权说明，原文没写填 未提及")
    users: str = Field(..., description="目标用户与使用场景")
    missing: list[str] = Field(default_factory=list, description="方案明显缺失的要素")


"""
回答判定结果
学生答完一个问题后，由提问的那位评审给出判定
"""


class JudgeSchema(BaseModel):
    verdict: str = Field(..., description="判定结果，只在 resolved、partial、unresolved 三者中选择")
    severity: int = Field(..., description="这个问题的严重度，1 到 5，5 表示致命")
    need_followup: bool = Field(..., description="是否需要追问第二层")
    followup_question: str = Field(default="", description="需要追问时的问题，不需要则为空字符串")
    comment: str = Field(..., description="一句话说明判定理由")


"""
交叉质询意愿：一次调用拿到四位评审各自的接话意愿
ctype 只在 rebut（反驳其他评审）、query（质询学生）两者中选择
"""


class ReactionSchema(BaseModel):
    role: str = Field(..., description="评审角色，只在 tech、cost、compliance、user 中选择")
    want: bool = Field(..., description="是否想接话")
    severity: int = Field(default=1, description="接话的严重度，1 到 5")
    point: str = Field(default="", description="接话理由，必须指向对方发言与自己关注点的冲突")
    ctype: str = Field(default="rebut", description="接话类型，只在 rebut、query 中选择")


class CrossSchema(BaseModel):
    reactions: list[ReactionSchema] = Field(default_factory=list, description="四位评审的接话意愿")
