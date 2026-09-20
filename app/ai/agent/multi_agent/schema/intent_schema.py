from  pydantic import BaseModel,Field
"""
意图识别格式化输出响应结果
"""
class IntentSchema(BaseModel):
    course:str = Field(...,description="课程类型")
    num:int = Field(...,description="题目数量")


"""
路由分类格式化输出响应结果
"""

class RouteSchema(BaseModel):
    route:str = Field(...,description="路由分类，只在exam,answer,chat中选择")

