import operator
from typing import Any

from typing_extensions import TypedDict, Annotated
from langchain.messages import AnyMessage

"""
模拟评审会状态
会议推进靠 meeting_phase，等学生回答靠 pending_question 是否为空
"""


class ReviewState(TypedDict):
    # AI 消息列表
    messages: Annotated[list[AnyMessage], operator.add]
    # ---------- 方案 ----------
    plan_title: str
    plan_text: str
    # 方案要素表，抽取一次后全场复用
    plan_elements: dict[str, Any]
    # ---------- 会议控制 ----------
    # extract / main / judge / cross / minutes / diagnose / done
    meeting_phase: str
    # 第几轮
    round: int
    # 发言顺序
    speaker_order: list[str]
    # 本轮轮到第几位评审
    speaker_index: int
    # 最大轮数，演示默认 1 轮
    max_round: int
    # ---------- 当前议题 ----------
    current_speaker: str
    # 非空代表已抛出问题，正在等学生回答
    pending_question: str
    # main / followup
    pending_type: str
    # 追问层数，最多 2 层
    followup_depth: int
    # ---------- 交叉质询防跑偏 ----------
    # 本议题已接话次数，上限 1
    cross_in_issue: int
    # 全会话已接话次数，上限 6
    cross_total: int
    # 本议题已发过言的评审，防同一人连说
    spoke_in_issue: list[str]
    # ---------- 产出 ----------
    # 全部质询记录
    question_log: list[dict[str, Any]]
    # 未答好的问题
    unresolved: list[dict[str, Any]]
    minutes: str
    diagnosis: dict[str, Any]
