import operator

from typing_extensions import TypedDict,Annotated
from langchain.messages import AnyMessage

"""
考试节点状态
"""
class ExamState(TypedDict):
    #AI 消息列表
    messages:Annotated[list[AnyMessage], operator.add]
    #----------意图识别节点-----------
    #课程
    course:str
    #题目数量
    total:int
    # ----------出题节点-----------
    #题库列表
    questions:list[dict[str, any]]
    #当前题目
    current_question:str
    # ----------收集用户答案节点-----------
    # 用户答案列表
    user_answer_list: list[dict[str, any]]
    #-------------控制出题节奏---------
    #当前答题的题目索引,索引是自己写的
    current: int
    #-----------------评价节点---
    #评价结果
    evaluation:str
    # 考试状态
    exam_step:str
    # 当前题目的编号，是数据库中的id
    question_id:int
    # 是否继续出题
    continue_question:bool






