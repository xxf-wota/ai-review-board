from langgraph.graph import END
from langgraph.types import Command

from app.ai.agent.review_agent.node.judge_node import current_issue, first_unjudged
from app.ai.agent.review_agent.state.review_state import ReviewState

"""
评审会调度中枢
会议不像出题那样有固定顺序：一次发言之后可能有人接话，也可能直接轮下一位
所以这里用一张决策表，按 meeting_phase 决定下一步去哪

一个议题的完整生命周期：
    main     ->  主问题
    cross    ->  （可选）别的评审接话，每个议题最多 1 次
    await_answer -> 停下等学生回答（goto=END，本次请求就结束了）
    judge    ->  学生答完回来，由**这条问题的提问者**判定
    advance  ->  看整个议题决定下一步（还有没答的问？要不要追？还是换人？）

一个议题里可能好几位评审都问了话，学生**按提问顺序一条一条答**，每条由那条问题的提问者判。
这样屏幕上永远只有一个"待你回答"，不会出现"三个人一起问我该答谁"
（这个坑是照着实际界面截图改的）
"""
# 默认发言顺序，按技术评审、成本与进度评审、合规评审、用户与价值评审
DEFAULT_ORDER = ["tech", "cost", "compliance", "user"]
# 防跑偏第一层：每个议题最多接话几次
MAX_CROSS_PER_ISSUE = 1
# 防跑偏第二层：整场会议最多接话几次
MAX_CROSS_TOTAL = 6
# 防跑偏第三层：每个议题最多追问几层。定成 1 层是演示时长考虑，
# 每个议题都追一层，学生就要多答好几轮，答辩现场拖太久
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


# 停在"等学生回答"上，等的是本议题里第一条还没判过的问题。
# goto=END 意味着这次请求到此为止，前端弹出答题框，学生答完再发一次请求把图唤醒
def _await_pending(state: ReviewState):
    item = first_unjudged(current_issue(state.get("question_log") or []))
    if not item:
        # 理论上到不了这儿（刚问完必定有一条没判）。兜底换下一位，别把会议卡死
        print("本议题没有待答的问题，直接换下一位")
        return _next_speaker(state)
    return Command(goto=END, update={
        "meeting_phase": "await_answer",
        # 这三个字段决定"这一问是谁问的、问的什么"，学生答完由他判定
        "pending_question": item.get("question") or "",
        "pending_type": item.get("question_type") or "main",
        "pending_from": item.get("speaker_role") or "",
        "student_answer": "",
    })


# 一次判定结束后，看着整个议题决定下一步：
#   1. 本议题还有没答的问题   -> 接着问下一条（主问题、接话按提问顺序来）
#   2. 都答完了，有人没被说服 -> 由他追一层（每个议题只追一次）
#   3. 否则                   -> 换下一位评审
def _route_after_judge(state: ReviewState):
    issue = current_issue(state.get("question_log") or [])
    if first_unjudged(issue):
        return _await_pending(state)

    depth = state.get("followup_depth", 0) or 0
    asked = any(x.get("question_type") == "followup" for x in issue)
    if depth < MAX_FOLLOWUP and not asked:
        # 按提问顺序找第一个"没被说服、而且给出了追问方向"的问题，由它的提问者来追
        for item in issue:
            if item.get("question_type") == "followup":
                continue
            if item.get("verdict") and item["verdict"] != "resolved" and item.get("followup_hint"):
                return Command(goto="speaker", update={
                    "meeting_phase": "followup",
                    "pending_type": "followup",
                    "current_speaker": item.get("speaker_role") or state.get("current_speaker"),
                    "followup_depth": depth + 1,
                    "followup_hint": item["followup_hint"],
                    "pending_question": "",
                    "student_answer": "",
                })
    return _next_speaker(state)


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
            return _await_pending(state)
        # 额度用完就不再判定，省一次模型调用，直接去等学生回答
        if state.get("cross_in_issue", 0) >= max_per_issue:
            return _await_pending(state)
        if state.get("cross_total", 0) >= max_total:
            return _await_pending(state)
        return Command(goto="cross")

    # 学生答完了，让这条问题的提问者判"我的疑问解决了吗"
    if phase == "judge":
        return Command(goto="judge")

    # 一次判定结束，看整个议题决定下一步
    if phase == "advance":
        return _route_after_judge(state)

    # 正等着学生回答。图本来就已经停了，这里只是防"不带回答就重新唤起"把会议跑飞
    if phase == "await_answer":
        return Command(goto=END)

    if phase == "done":
        return Command(goto=END)

    # 兜底：遇到不认识的阶段也不能把会议卡死
    print(f"未知的会议阶段：{phase}，直接散会")
    return Command(goto=END, update={"meeting_phase": "done"})
