import re

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_stream_writer

from app.ai.agent.review_agent.node.extract_node import format_elements
from app.ai.agent.review_agent.state.review_state import ReviewState
from app.ai.model.my_model import MyModel
from app.ai.prompt.builder_prompt import BuilderPromptYaml

"""
评审发言节点
由当前轮到的评审，针对方案要素提出一个主问题或追问
四位评审的关注点互相排他，靠各自的 yaml 保证问出来的视角不同
"""
# 评审角色 -> 提示词文件
REVIEWER_FILES = {
    "tech": "review/tech.yaml",
    "cost": "review/cost.yaml",
    "compliance": "review/compliance.yaml",
    "user": "review/user.yaml",
}
# 评审角色 -> 中文名，前端气泡上显示
REVIEWER_NAMES = {
    "tech": "技术评审",
    "cost": "成本与进度评审",
    "compliance": "合规与伦理评审",
    "user": "用户与价值评审",
}
# 各评审的关注点。接话时要把它直接写进提示，否则弱模型容易问成别人的关注点
REVIEWER_CONCERNS = {
    "tech": "技术可行性、是否过度设计、有没有更简单的替代方案、关键难点能否落地",
    "cost": "预算是否够、接口调用费用、人力是否够、周期是否现实",
    "compliance": "数据来源是否合法、用户隐私、是否取得授权、算法偏见",
    "user": "谁真的会用、相比现有方案的优势、是不是伪需求",
}
# 提示词只读一次，避免每次发言都读盘
PROMPTS = {role: BuilderPromptYaml.get_prompt(f) for role, f in REVIEWER_FILES.items()}


# 创建某位评审的智能体，先本地模型，失败降级商业模型
def _build_agent(role: str, model):
    return create_agent(
        model=model,
        system_prompt=PROMPTS[role],
        middleware=[
            ModelCallLimitMiddleware(
                thread_limit=3,
                exit_behavior="end",
            )
        ],
    )


# 拼接发言节点收到的输入：方案要素表 + 已有问答记录
def _build_input(plan_elements: dict, history: str = "") -> str:
    content = f"以下是学生提交的方案要素表：\n{format_elements(plan_elements)}"
    if history:
        content += f"\n\n以下是本场评审会已经发生的问答：\n{history}"
    content += "\n\n请提出你的质询问题。"
    return content


# 只保留第一个问题：判定是一问一判，一句话里塞进多个问题会导致判不清楚
def first_question(text: str) -> str:
    text = (text or "").strip().replace("?", "？")
    if "？" in text:
        text = text.split("？")[0] + "？"
    # 模型偶发吐出"。。""！！"这类重复标点，压成一个
    text = re.sub(r"([。！？])[。！？]+", r"\1", text)
    return text.strip()


# 核心函数：某位评审针对方案要素提出一个问题（非流式，供验证脚本直接调用）
def reviewer_question(role: str, plan_elements: dict, history: str = "") -> str:
    user_msg = {"messages": [HumanMessage(content=_build_input(plan_elements, history))]}
    try:
        rs = _build_agent(role, MyModel.get_local_model()).invoke(user_msg)
    except Exception as e:
        print(f"-----------本地模型发言失败，降级商业模型：{e}------------")
        rs = _build_agent(role, MyModel.get_model()).invoke(user_msg)
    # 取最后一条AI消息作为问题，并裁掉附带的多余提问
    return first_question(rs["messages"][-1].content)


# 图节点：当前评审发言，流式推到前端
async def speaker_node(state: ReviewState):
    role = state.get("current_speaker") or "tech"
    plan_elements = state.get("plan_elements") or {}
    # 追问时把本议题已有的问答带上，让评审知道学生刚才是怎么答的
    history = state.get("pending_question", "")
    if state.get("pending_type") == "followup":
        history = f"你上一轮已经问过：{history}"

    user_msg = {"messages": [HumanMessage(content=_build_input(plan_elements, history))]}
    writer = get_stream_writer()
    result = []
    try:
        agent = _build_agent(role, MyModel.get_local_model())
    except Exception as e:
        print(f"-----------本地模型不可用，降级商业模型：{e}------------")
        agent = _build_agent(role, MyModel.get_model())

    async for chunk, metadata in agent.astream(user_msg, stream_mode="messages"):
        if chunk.content:
            result.append(chunk.content)
            writer(chunk.content)

    question = first_question("".join(result))
    # 模型没吐出内容时兜底，避免会议直接卡死
    if not question:
        question = "请补充说明方案中最关键的风险以及你的应对措施。"
    # 登记本议题已发过言的人，接话判定时要把他排除掉，避免同一个人连说
    spoke = list(state.get("spoke_in_issue") or [])
    if role not in spoke:
        spoke.append(role)
    # 登记发言记录，前端时间线和后续落库都靠它
    log = list(state.get("question_log") or [])
    log.append({
        "round": state.get("round", 1),
        "speaker_role": role,
        "question_type": state.get("pending_type") or "main",
        "target_speaker": "",
        "question": question,
        "verdict": "",
        "severity": 0,
        "followup_depth": state.get("followup_depth", 0),
    })
    return {
        "messages": [AIMessage(content=f"\n【{REVIEWER_NAMES.get(role, role)}】{question}\n")],
        "pending_question": question,
        "pending_type": "main",
        # 发言完进入接话判定；M4 接上学生回答后，这里会先转到"等学生回答"
        "meeting_phase": "cross",
        "cross_checked": False,
        "spoke_in_issue": spoke,
        "question_log": log,
    }
