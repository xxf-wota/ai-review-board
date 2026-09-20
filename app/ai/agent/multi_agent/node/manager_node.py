from app.ai.agent.multi_agent.state.exam_state import ExamState
from langgraph.types import Command
from langgraph.graph import END
from langchain_core.messages import HumanMessage
from app.ai.agent.multi_agent.node.router_agent import router_agent
# 定义记忆关键词匹配，规则型路由，如转人工这种关键字
keyword = ["上一个问题","上一题"]

# 定义一个函数，判断问题是否在keyword中
def is_keyword(question:str):
    for k in keyword:
        if k in question:
            return True
    return False

"""
主管节点，中枢，负责调用其他节点
"""
# 主管每次调用时，来了几次就打印几次
# start、intent、question各打印一次
def manager_node(state:ExamState):
    # 获取是否继续出题，第一次进入时，默认继续出题
    continue_question = state.get("continue_question", True)
    # 获取考试信息
    exam_state = state.get("exam_step", "start")
    print(f"当前考试状态：{exam_state}")
    print("状态：",state)
    # 获取用户每次输入的问题
    last_msg = state["messages"][-1]
    if isinstance(last_msg, HumanMessage):
        user_input = last_msg.content
        if is_keyword(user_input):
            print("关键字命中，进入关键字匹配")
            return Command(goto="chat")
        print("意图识别，模型兜底")
        route = router_agent(user_input)
        print(f"模型识别路由：{route}")
        if route == "exam":
            return Command(goto="intent")
        elif route == "answer":
            return Command(goto="answer")
        elif route == "chat":
            return Command(goto="chat")
    else:
        user_input = ""
    print(f"用户输入问题：{user_input}")

    # 如果是第一次进入
    if exam_state == "start":
        # 调用意图识别节点
        return Command(goto="intent")
    elif exam_state == "intent":
        # 调用问题节点
        return Command(goto="question")
    elif exam_state == "question":
        # 判断用户输入是否是问题
        if user_input == "":
            print("等待用户输入答案")
            return Command(goto=END) # 直接结束，让用户输入答案
        else:
            # 调用答案节点，收集答案
            return Command(goto="answer")
    elif exam_state == "answer":
        if continue_question:
            print("继续出题")
            return Command(goto="question")
        else:
            print("所有题目已答完，进入评价")
            return Command(goto="evaluate")
    elif exam_state == "evaluate":
        return Command(goto="evaluate")
    elif exam_state == "done":
        return Command(goto=END, update={
            # 将信息初始化
            "exam_step": "start", # 从头开始
            "question": [],
            "user_answer_list": [],
            "current": 0,
        })
    else:
        return Command(goto=END)
