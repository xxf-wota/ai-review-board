from langgraph.graph import END
from langgraph.types import Command

from app.ai.agent.review_agent.state.review_state import ReviewState

"""
评审会调度中枢
会议不像出题那样有固定顺序：一次发言之后可能有人接话，也可能直接轮下一位
所以这里用一张决策表，按 meeting_phase 决定下一步去哪

一个议题（一轮里的一位评审）的完整生命周期：
    main  ->  主问题
    cross ->  （可选）别的评审接话，最多 1 次
    await_answer -> 停下等学生回答（这一步 goto=END，本次请求就结束了）
    judge ->  学生答完回来，由提问的那位评审判"我的疑问解决了吗"
    followup -> （可选）没答好就追一层
    advance -> 议题收尾，换下一位评审
"""
# 默认发言顺序，按技术评审、成本与进度评审、合规评审、用户与价值评审
DEFAULT_ORDER = ["tech", "cost", "compliance", "user"]
# 防跑偏第一层：每个议题最多接话几次
MAX_CROSS_PER_ISSUE = 1
# 防跑偏第二层：整场会议最多接话几次
MAX_CROSS_TOTAL = 6
# 防跑偏第三层：同一个问题最多追问几层。定成 1 层是演示时长考虑，
# 4 位评审各追一层就要多答 4 轮，答辩现场拖太久
MAX_FOLLOWUP = 1

# 换议题时要清掉的当前议题状态。这几项必须一起清，
# 漏一个就会出现"新问题沿用上一题的标记"这类串味
_ISSUE_RESET = {
    "cross_in_issue": 0,
    "cross_checked": False,
    "spoke_in_issue": [],
    "pending_type": "",
    "pending_question": "",
    "pending_from": "",
    "followup_depth": 0,
    "followup_hint": "",
    "student_answer": "",
}


# 推进到下一位评审；本轮说完就进下一轮，轮次用完就散会
def _next_speaker(state: ReviewState):
    order = state.get("speaker_order") or DEFAULT_ORDER
    nxt = state.get("speaker_index", -1) + 1
    round_no = state.get("round", 1) or 1
    max_round = state.get("max_round", 1) or 1

    # 本轮还有人没发言，换下一位
    if nxt < len(order):
        return Command(goto="speaker", update=dict(
            _ISSUE_RESET,
            speaker_index=nxt,
            current_speaker=order[nxt],
            meeting_phase="main",
        ))

    # 本轮发言完毕，还有下一轮
    if round_no < max_round:
        return Command(goto="speaker", update=dict(
            _ISSUE_RESET,
            round=round_no + 1,
            speaker_index=0,
            current_speaker=order[0],
            meeting_phase="main",
        ))

    # 所有轮次都开完了
    print("所有轮次已完成，评审会结束")
    return Command(goto=END, update={"meeting_phase": "done"})


# 把图停在"等学生回答"上。goto=END 意味着这一轮请求到此为止，
# 前端拿到 await_answer 事件后弹出答题框，学生答完再发一次请求把图唤醒
def _await_answer():
    return Command(goto=END, update={"meeting_phase": "await_answer"})


def manager_node(state: ReviewState):
    # 还没有要素表，先做抽取
    if not state.get("plan_elements"):
        return Command(goto="extract")

    phase = state.get("meeting_phase") or "main"

    # 该某位评审说话了。主问题和追问都走这个节点，由 pending_type 区分
    if phase in ("main", "followup"):
        return Command(goto="speaker")

    # 刚才有人发言，看看有没有人要接话
    if phase == "cross":
        # 上限允许被 state 覆盖，方便验收时单独关掉接话、专心验证调度
        max_per_issue = state.get("max_cross_per_issue", MAX_CROSS_PER_ISSUE)
        max_total = state.get("max_cross_total", MAX_CROSS_TOTAL)
        # 本议题已经判定过一轮了，不再重复判定；接话环节结束，去等学生回答
        if state.get("cross_checked"):
            return _await_answer()
        # 额度用完就不再判定，省一次模型调用，直接去等学生回答
        if state.get("cross_in_issue", 0) >= max_per_issue:
            return _await_answer()
        if state.get("cross_total", 0) >= max_total:
            return _await_answer()
        return Command(goto="cross")

    # 学生答完了，让提问的评审判定"我的疑问解决了吗"
    if phase == "judge":
        return Command(goto="judge")

    # 议题收尾，换下一位评审
    if phase == "advance":
        return _next_speaker(state)

    # 正等着学生回答。图本来就已经停了，这里只是防"不带回答就重新唤起"把会议跑飞
    if phase == "await_answer":
        return Command(goto=END)

    if phase == "done":
        return Command(goto=END)

    # 兜底：遇到不认识的阶段也不能把会议卡死
    print(f"未知的会议阶段：{phase}，直接散会")
    return Command(goto=END, update={"meeting_phase": "done"})
