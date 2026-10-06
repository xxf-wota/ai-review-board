from typing import Any

from typing_extensions import TypedDict

"""
模拟评审会状态
会议推进靠 meeting_phase：问到 await_answer 就停下等学生回答，学生答完回来跑 judge

注意：LangGraph 只会保留这里声明过的字段，节点里 return 的新字段如果没写进来会被直接丢掉
（M3 踩过这个坑：max_cross_total 没声明，传进去 0 也不生效）

状态里为什么不放问答正文
------------------------
全场的问题原文、学生回答、判定评语都放在 MySQL 的 review_question 表里，
这里只留索引（库里的 id + 调度要用的几个字段）。
原因有两个：
  1. 检查点是"每个超步把状态整份写一遍"，正文放进来会让体积随问答条数平方级增长；
  2. 正文只要在状态里，就随时可能被某个节点整段拼进提示词，商业模型下很贵。
取值入口统一在 store/question_store.py，谁要用正文谁拿 id 去取。
"""


class ReviewState(TypedDict):
    # ---------- 方案 ----------
    plan_title: str
    # 方案全文。只有"要素表还没抽"的时候才留在状态里，抽完就清掉（见 extract_node）
    plan_text: str
    # 方案要素表，抽取一次后全场复用
    plan_elements: dict[str, Any]
    # ---------- 会话 ----------
    # 数据库里的会话号。问答记录按它落库，索引里的 id 就是这张表的主键
    session_id: str
    # ---------- 会议控制 ----------
    # 会议推进的唯一开关，取值与去向见 manager_node 的决策表：
    # extract 抽取 / main 提主问题 / cross 接话 / await_answer 停下等学生
    # judge 判定 / followup 追问 / advance 议题收尾 / done 散会
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
    # 正在等学生回答的那条记录在库里的 id。judge 用它精确定位是哪一条
    pending_id: int
    # main / followup / cross，标记这个问题的来路
    pending_type: str
    # 抛出这个问题的评审。学生答完后由他来判定，也只由他追问
    pending_from: str
    # 追问层数，上一轮答不好才追，最多 1 层
    followup_depth: int
    # 判定说该追问时，把"往哪个点追"记下来，交给发言节点用这位评审的口吻重新组织成问题
    followup_hint: str
    # ---------- 学生回答 ----------
    # 学生最新一次回答。前端点"跳过"会写入 SKIP_MARK。
    # 落库到对应记录后立刻清空，不在状态里过夜
    student_answer: str
    # ---------- 交叉质询防跑偏 ----------
    # 本议题已接话次数，上限 1
    cross_in_issue: int
    # 全会话已接话次数，上限 6
    cross_total: int
    # 本议题已发过言的评审，防同一人连说
    spoke_in_issue: list[str]
    # 本议题是否已经做过接话判定，防止没人接话时在 manager 和 cross 之间死循环
    cross_checked: bool
    # 接话上限，允许按场次覆盖。注意：没在这里声明的字段会被 LangGraph 直接丢掉
    max_cross_per_issue: int
    max_cross_total: int
    # ---------- 产出：全场问答的索引（不是正文） ----------
    # 每条只有 {id, round, speaker_role, question_type, target_speaker, question,
    #           verdict, severity, followup_depth, followup_hint}
    # question 只在这条还没判过时留着（同步的 manager 要拿它去问学生），判完就 pop
    question_index: list[dict[str, Any]]
