from langgraph.graph import END
from langgraph.types import Command

from app.ai.agent.review_agent.state.review_state import ReviewState

"""
评审会调度中枢
会议不像出题那样有固定顺序：一次发言之后可能有人接话，也可能直接轮下一位
所以这里用一张决策表，按 meeting_phase 决定下一步去哪
"""
# 默认发言顺序，按技术评审、成本与进度评审、合规评审、用户与价值评审
DEFAULT_ORDER = ["tech", "cost", "compliance", "user"]
# 防跑偏第一层：每个议题最多接话几次
MAX_CROSS_PER_ISSUE = 1
# 防跑偏第二层：整场会议最多接话几次
MAX_CROSS_TOTAL = 6


# 推进到下一位评审；本轮说完就进下一轮，轮次用完就散会
def _next_speaker(state: ReviewState):
    order = state.get("speaker_order") or DEFAULT_ORDER
    nxt = state.get("speaker_index", -1) + 1
    round_no = state.get("round", 1) or 1
    max_round = state.get("max_round", 1) or 1

    # 本轮还有人没发言，换下一位，并把接话相关的计数清零
    if nxt < len(order):
        return Command(goto="speaker", update={
            "speaker_index": nxt,
            "current_speaker": order[nxt],
            "meeting_phase": "main",
            "cross_in_issue": 0,
            "cross_checked": False,
            "spoke_in_issue": [],
            # 这两个必须一起清，否则新的主问题会沿用上一轮的 cross 标记
            "pending_type": "",
            "pending_question": "",
        })

    # 本轮发言完毕，还有下一轮
    if round_no < max_round:
        return Command(goto="speaker", update={
            "round": round_no + 1,
            "speaker_index": 0,
            "current_speaker": order[0],
            "meeting_phase": "main",
            "cross_in_issue": 0,
            "cross_checked": False,
            "spoke_in_issue": [],
            "pending_type": "",
            "pending_question": "",
        })

    # 所有轮次都开完了
    print("所有轮次已完成，评审会结束")
    return Command(goto=END, update={"meeting_phase": "done"})


def manager_node(state: ReviewState):
    # 还没有要素表，先做抽取
    if not state.get("plan_elements"):
        return Command(goto="extract")

    phase = state.get("meeting_phase") or "main"

    # 轮到当前评审发言
    if phase == "main":
        return Command(goto="speaker")

    # 刚才有人发言，看看有没有人要接话
    if phase == "cross":
        # 上限允许被 state 覆盖，方便验收时单独关掉接话、专心验证调度
        max_per_issue = state.get("max_cross_per_issue", MAX_CROSS_PER_ISSUE)
        max_total = state.get("max_cross_total", MAX_CROSS_TOTAL)
        # 本议题已经判定过一轮了，不再重复判定
        if state.get("cross_checked"):
            return _next_speaker(state)
        # 额度用完就直接轮下一位，连判定都不做，省一次模型调用
        if state.get("cross_in_issue", 0) >= max_per_issue:
            return _next_speaker(state)
        if state.get("cross_total", 0) >= max_total:
            return _next_speaker(state)
        return Command(goto="cross")

    if phase == "done":
        return Command(goto=END)

    # 兜底：遇到不认识的阶段也不能把会议卡死
    print(f"未知的会议阶段：{phase}，直接散会")
    return Command(goto=END, update={"meeting_phase": "done"})
