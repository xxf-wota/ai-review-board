from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.ai.agent.review_agent.node.cross_node import cross_node
from app.ai.agent.review_agent.node.extract_node import extract_node
from app.ai.agent.review_agent.node.judge_node import judge_node
from app.ai.agent.review_agent.node.manager_node import DEFAULT_ORDER, manager_node
from app.ai.agent.review_agent.node.speaker_node import speaker_node
from app.ai.agent.review_agent.reviewers import REVIEWER_NAMES
from app.ai.agent.review_agent.state.review_state import ReviewState
from app.ai.agent.review_agent.store import question_store as store

"""
AI 交叉质询评审团
与模拟面试图同一个骨架：START 进 manager，每个节点干完都回 manager，由它决定下一步

和 M3 的区别：会议不再一口气跑完，而是"问一句、停一下、等学生答"。
manager 走到 await_answer 就 goto=END，这次请求结束；学生答完再发一次请求，
带上同一个 thread_id，图从检查点里把状态捞回来接着跑。

检查点由外面注入（`main.py` 里是 PostgreSQL 的 AsyncPostgresSaver），
这个类自己不关心存哪儿 —— 所以进程重启后会议照样接得上。

状态里不放问答正文（放的是索引，见 store/question_store.py）：
检查点是"每个超步把状态整份写一遍"，正文放进去会让它随问答条数平方级变胖，
而且随时可能被某个节点整段拼进提示词。散会时按 session_id 把整场记录取回来，
事件里的字段名和以前完全一样，前端不用改。

要看图别用 LangGraph 的 draw：manager 走的是 Command(goto=...) 动态跳转，
自动画出来的图只有「节点全回中枢」，分支一条都看不到。
准确的结构图在 docs/评审团流程图.drawio 和 docs/评审团流程图与状态说明.md。
"""


def build_init_state(plan_text: str, max_round: int = 1, max_cross_total=None,
                     plan_elements: dict = None, session_id: str = "") -> dict:
    """会议开场时的初始状态，所有计数器显式清零，不依赖默认值"""
    # 提交方案时已经抽过要素了，这里直接复用，省掉一次十几秒的抽取
    has_elements = bool(plan_elements)
    state = {
        "plan_text": plan_text,
        "plan_elements": plan_elements or {},
        # 问答记录按它落库，索引里的 id 就是 review_question 表的主键
        "session_id": session_id,
        "meeting_phase": "main" if has_elements else "extract",
        "round": 1,
        "max_round": max_round,
        "speaker_order": list(DEFAULT_ORDER),
        "speaker_index": 0,
        "current_speaker": DEFAULT_ORDER[0],
        "pending_question": "",
        "pending_id": 0,
        "pending_type": "",
        "pending_from": "",
        "followup_depth": 0,
        "followup_hint": "",
        "student_answer": "",
        "cross_in_issue": 0,
        "cross_total": 0,
        "spoke_in_issue": [],
        "cross_checked": False,
        # 全场问答的索引。正文在库里，只留 id 和调度要用的字段
        "question_index": [],
    }
    # 不传就用节点里的默认上限，传 0 可以关掉接话
    if max_cross_total is not None:
        state["max_cross_total"] = max_cross_total
    # 要素表已经有了就不会再走抽取节点，方案全文没有第二次用武之地，
    # 不进状态：一份方案可能两万字，每个超步都写一遍检查点太亏
    if has_elements:
        state["plan_text"] = ""
    return state


class ReviewGraph:

    def __init__(self, checkpoint):
        self.memory = checkpoint
        self.agent = self.get_agent()

    # 构建图
    def get_agent(self):
        graph = StateGraph(ReviewState)
        # 添加节点
        graph.add_node("manager", manager_node)
        graph.add_node("extract", extract_node)
        graph.add_node("speaker", speaker_node)
        graph.add_node("cross", cross_node)
        graph.add_node("judge", judge_node)

        # 画边：全部回到中枢，由中枢决定下一个是谁
        graph.add_edge(START, "manager")
        graph.add_edge("extract", "manager")
        graph.add_edge("speaker", "manager")
        graph.add_edge("cross", "manager")
        graph.add_edge("judge", "manager")

        self.agent = graph.compile(checkpointer=self.memory)
        return self.agent

    def _config(self, session_id):
        return {"configurable": {"thread_id": session_id}}

    # 取检查点里的状态。用来判断"这场会议开过没有""现在能不能接学生回答"，
    # 不然一个不存在的 session_id 打进来，图会凭空从 START 开一场要素表为空的会
    async def snapshot_values(self, session_id) -> dict:
        snapshot = await self.agent.aget_state(self._config(session_id))
        return snapshot.values or {}

    # 推一段，然后按图停在哪补一个收尾事件告诉前端下一步该干什么
    # 不推 messages 流：节点里已经把每个字都包成 token 事件了，再推一遍就重复了
    async def _drive(self, input_state, session_id):
        config = self._config(session_id)
        async for chunk in self.agent.astream(input_state, config=config, stream_mode="custom"):
            # 节点里通过 events.emit 推出来的字典
            if isinstance(chunk, dict):
                yield chunk

        # 从检查点里取停下来的状态，判断是"等学生回答"还是"真的开完了"
        values = (await self.agent.aget_state(config)).values or {}
        # _tail 要取整场记录，是异步的，这里等它
        yield await self._tail(values, session_id)

    # 收尾事件
    async def _tail(self, values, session_id):
        phase = values.get("meeting_phase") or ""
        if phase == "await_answer":
            role = values.get("pending_from") or values.get("current_speaker") or ""
            # 本议题一共几条问题、现在要答的是第几条。前端拿它显示"第 2/3 问"，
            # 学生才知道后面还有没有（一个议题里可能好几位评审都问了话）
            # 看索引就够，判定过的那几条正文不用取回来
            issue = store.current_issue(values.get("question_index") or [])
            unjudged = [x for x in issue if not x.get("verdict")]
            return {
                "event": "await_answer",
                "session_id": session_id,
                "role": role,
                "name": REVIEWER_NAMES.get(role, role),
                "question": values.get("pending_question") or "",
                "question_type": values.get("pending_type") or "main",
                "question_index": len(issue) - len(unjudged) + 1,
                "issue_total": len(issue),
            }
        # 散会：把整场质询记录取回来推给前端。
        # 状态里只有索引，正文按 session_id 从库里读 —— 前端拿到的字段和以前一模一样
        rows = await store.all_rows(session_id)
        return {
            "event": "meeting_end",
            "session_id": session_id,
            "cross_total": values.get("cross_total", 0),
            "question_log": rows,
            "unresolved": store.collect_unresolved(rows),
        }

    # 开一场评审会：跑到"第一个提问等学生回答"或者会议结束为止
    async def start(self, plan_text, session_id, max_round=1, max_cross_total=None,
                    plan_elements=None):
        init_state = build_init_state(plan_text, max_round, max_cross_total, plan_elements,
                                      session_id)
        # 记录是边开边写透的（发言落库、判定回填），重开同一场会先清掉上一次的记录，
        # 否则新旧记录会叠在一起，索引里的 id 也会指错
        await store.reset(session_id)
        yield {"event": "meeting_start", "session_id": session_id, "max_round": max_round}
        async for event in self._drive(init_state, session_id):
            yield event

    # 学生答完一题，把图唤醒继续开
    # 直接 astream 一个 dict：LangGraph 会把它当状态更新合到检查点上，从 START 重新进 manager，
    # 而 manager 看到 meeting_phase=judge 就知道该去判定了（和模拟面试续多轮是同一个套路）
    async def resume(self, session_id, answer):
        async for event in self._drive(
            {"student_answer": answer, "meeting_phase": "judge"}, session_id
        ):
            yield event
