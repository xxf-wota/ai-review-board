from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.ai.agent.review_agent.node.cross_node import cross_node
from app.ai.agent.review_agent.node.extract_node import extract_node
from app.ai.agent.review_agent.node.judge_node import judge_node
from app.ai.agent.review_agent.node.manager_node import DEFAULT_ORDER, manager_node
from app.ai.agent.review_agent.node.speaker_node import REVIEWER_NAMES, speaker_node
from app.ai.agent.review_agent.state.review_state import ReviewState

"""
AI 交叉质询评审团
与模拟面试图同一个骨架：START 进 manager，每个节点干完都回 manager，由它决定下一步

和 M3 的区别：会议不再一口气跑完，而是"问一句、停一下、等学生答"。
manger 走到 await_answer 就 goto=END，这次请求结束；学生答完再发一次请求，
带上同一个 thread_id，图从检查点里把状态捞回来接着跑
"""

# 验收脚本用 run_to_end 自动答题时的兜底回答
AUTO_ANSWER = "我们按 5000 名用户做了测算，主要是服务器和接口调用两部分，具体数字还在核。"


def build_init_state(plan_text: str, max_round: int = 1, max_cross_total=None,
                     plan_elements: dict = None) -> dict:
    """会议开场时的初始状态，所有计数器显式清零，不依赖默认值"""
    # 提交方案时已经抽过要素了，这里直接复用，省掉一次十几秒的抽取
    has_elements = bool(plan_elements)
    state = {
        "messages": [HumanMessage(content=plan_text)],
        "plan_text": plan_text,
        "plan_elements": plan_elements or {},
        "meeting_phase": "main" if has_elements else "extract",
        "round": 1,
        "max_round": max_round,
        "speaker_order": list(DEFAULT_ORDER),
        "speaker_index": 0,
        "current_speaker": DEFAULT_ORDER[0],
        "pending_question": "",
        "pending_type": "",
        "pending_from": "",
        "followup_depth": 0,
        "followup_hint": "",
        "student_answer": "",
        "cross_in_issue": 0,
        "cross_total": 0,
        "spoke_in_issue": [],
        "cross_checked": False,
        "question_log": [],
        "unresolved": [],
        "minutes": "",
        "diagnosis": {},
    }
    # 不传就用节点里的默认上限，传 0 可以关掉接话
    if max_cross_total is not None:
        state["max_cross_total"] = max_cross_total
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
        yield self._tail(values, session_id)

    # 收尾事件
    def _tail(self, values, session_id):
        phase = values.get("meeting_phase") or ""
        if phase == "await_answer":
            role = values.get("pending_from") or values.get("current_speaker") or ""
            return {
                "event": "await_answer",
                "session_id": session_id,
                "role": role,
                "name": REVIEWER_NAMES.get(role, role),
                "question": values.get("pending_question") or "",
                "question_type": values.get("pending_type") or "main",
            }
        return {
            "event": "meeting_end",
            "session_id": session_id,
            "cross_total": values.get("cross_total", 0),
            "question_log": values.get("question_log") or [],
            "unresolved": values.get("unresolved") or [],
        }

    # 开一场评审会：跑到"第一个提问等学生回答"或者会议结束为止
    async def start(self, plan_text, session_id, max_round=1, max_cross_total=None,
                    plan_elements=None):
        init_state = build_init_state(plan_text, max_round, max_cross_total, plan_elements)
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

    # 一口气把整场会议跑完，每次提问都用同一句兜底回答顶上。给验收脚本用，前端不走这里。
    # 走的是真实的 start/resume 路径，所以验的是真流程，不是另写一套
    async def run_to_end(self, plan_text, session_id, answer=AUTO_ANSWER, max_round=1,
                         max_cross_total=None, plan_elements=None):
        events = []
        async for e in self.start(plan_text, session_id, max_round, max_cross_total, plan_elements):
            events.append(e)

        # 一步步答下去。加个上限，图哪天写错了也不会在这儿转死
        guard = 0
        while events and events[-1].get("event") == "await_answer" and guard < 50:
            guard += 1
            async for e in self.resume(session_id, answer):
                events.append(e)

        values = (await self.agent.aget_state(self._config(session_id))).values or {}
        return events, values

    # 画图，排查调度问题时很有用
    def draw(self):
        data = self.agent.get_graph().draw_mermaid_png()
        with open("评审会图.png", "wb") as f:
            f.write(data)


if __name__ == '__main__':
    checkpoint = InMemorySaver()
    graph = ReviewGraph(checkpoint)
    graph.draw()
