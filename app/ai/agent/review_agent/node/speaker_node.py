import re

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.messages import HumanMessage

from app.ai.agent.review_agent import events, reviewers
from app.ai.agent.review_agent.node.extract_node import format_elements
from app.ai.agent.review_agent.reviewers import PROMPTS, REVIEWER_NAMES
from app.ai.agent.review_agent.state.review_state import ReviewState
from app.ai.agent.review_agent.store import question_store as store
from app.ai.model.my_model import MyModel

"""
评审发言节点
由当前轮到的评审，针对方案要素提出一个主问题或追问
四位评审的关注点互相排他，靠各自的 yaml 保证问出来的视角不同

问答正文不在状态里，落库和取正文都走 store/question_store.py
（提示词里那份"本议题已经发生的问答"是临时取出来的，不进状态）
"""


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


# 拼接发言节点收到的输入：方案要素表 + 本议题已有的问答 + 追问方向
def _build_input(plan_elements: dict, history: str = "", hint: str = "",
                 is_followup: bool = False) -> str:
    content = f"以下是学生提交的方案要素表：\n{format_elements(plan_elements)}"
    if history:
        content += f"\n\n以下是本议题已经发生的问答：\n{history}"
    if hint:
        content += f"\n\n你这次要追的方向：{hint}"
    if is_followup:
        content += "\n\n学生刚才的回答没能打消你的疑问，请针对他答得含糊、或者没给依据的那个具体点，提出一个追问。"
    else:
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
    index = list(state.get("question_index") or [])
    # 主问题只看要素表：把前面的记录带进去，评审会忍不住去问别人议题的东西。
    # 追问则必须把本议题已经问过的、学生答过的都带上，否则他不知道学生刚才怎么答的。
    # 正文不在状态里，按索引去库里取本议题那几条
    is_followup = (state.get("pending_type") or "main") == "followup"
    history = ""
    if is_followup:
        issue_rows = await store.rows_of(store.current_issue(index))
        history = store.issue_transcript(issue_rows)
    hint = (state.get("followup_hint") or "") if is_followup else ""

    user_msg = {"messages": [HumanMessage(content=_build_input(
        plan_elements, history, hint, is_followup))]}
    name = REVIEWER_NAMES.get(role, role)
    # 追问在主问题基础上多带一个标记：前端据此在气泡上区别显示
    qtype = "followup" if is_followup else "main"
    # 先告诉前端"谁要开始说话了"，前端据此开一个气泡
    events.speaker_start(role, name, qtype)
    result = []
    try:
        agent = _build_agent(role, MyModel.get_local_model())
    except Exception as e:
        print(f"-----------本地模型不可用，降级商业模型：{e}------------")
        agent = _build_agent(role, MyModel.get_model())

    async for chunk, metadata in agent.astream(user_msg, stream_mode="messages"):
        if chunk.content:
            result.append(chunk.content)
            events.token(role, chunk.content)

    question = first_question("".join(result))
    # 模型没吐出内容时兜底，避免会议直接卡死
    if not question:
        question = "请补充说明方案中最关键的风险以及你的应对措施。"
    # 问题一抛出来就落库，拿回来的 id 就是它在这场里的索引。
    # 正文（问题原文）留在库里，状态里只留 id 和调度要用的字段
    entry = store.new_entry(state.get("round", 1) or 1, role, qtype,
                            question=question,
                            depth=state.get("followup_depth", 0) or 0)
    await store.ask(state.get("session_id") or "", entry)
    index.append(entry)
    # 登记本议题已发过言的人，接话判定时要把他排除掉，避免同一个人连说
    spoke = list(state.get("spoke_in_issue") or [])
    if role not in spoke:
        spoke.append(role)
    # 发言结束，把完整问题一次性告诉前端
    events.speaker_end(role, name, question, qtype)
    return {
        "pending_question": question,
        "pending_type": qtype,
        # 提问者记下来：学生答完之后由他判定，也只由他追问
        "pending_from": role,
        # 学生答的就是这一条，judge 按 id 找它
        "pending_id": entry["id"],
        # 主问题问完先过一轮接话（别的评审可能当场反驳），
        # 追问问完就直接等学生回答，不再接话，避免一个议题反复拉扯
        "meeting_phase": "await_answer" if is_followup else "cross",
        # 追问后不该再进接话判定，置 True 是双保险
        "cross_checked": is_followup,
        "spoke_in_issue": spoke,
        "question_index": index,
    }
