from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.ai.agent.review_agent.node.cross_node import cross_node
from app.ai.agent.review_agent.node.extract_node import extract_node
from app.ai.agent.review_agent.node.manager_node import DEFAULT_ORDER, manager_node
from app.ai.agent.review_agent.node.speaker_node import speaker_node
from app.ai.agent.review_agent.state.review_state import ReviewState

"""
AI 交叉质询评审团
与模拟面试图同一个骨架：START 进 manager，每个节点干完都回 manager，由它决定下一步
"""


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
        "followup_depth": 0,
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

        # 画边：全部回到中枢，由中枢决定下一个是谁
        graph.add_edge(START, "manager")
        graph.add_edge("extract", "manager")
        graph.add_edge("speaker", "manager")
        graph.add_edge("cross", "manager")

        self.agent = graph.compile(checkpointer=self.memory)
        return self.agent

    # 开一场评审会，只往外推结构化事件
    # 不推 messages 流：节点里已经把每个字都包成 token 事件了，再推一遍就重复了
    async def run(self, plan_text, session_id, max_round=1, max_cross_total=None,
                  plan_elements=None):
        init_state = build_init_state(plan_text, max_round, max_cross_total, plan_elements)
        config = {"configurable": {"thread_id": session_id}}

        yield {"event": "meeting_start", "session_id": session_id, "max_round": max_round}

        async for chunk in self.agent.astream(init_state, config=config, stream_mode="custom"):
            # 节点里通过 events.emit 推出来的字典
            if isinstance(chunk, dict):
                yield chunk

        # 会议结束后从检查点里取最终状态，把纪要需要的数据一并交给前端
        snapshot = await self.agent.aget_state(config)
        values = snapshot.values or {}
        yield {
            "event": "meeting_end",
            "cross_total": values.get("cross_total", 0),
            "question_log": values.get("question_log") or [],
        }

    # 跑完整场会议并返回最终状态，供验收脚本检查调度结果
    async def run_get_state(self, plan_text, session_id, max_round=1, max_cross_total=None,
                            plan_elements=None):
        init_state = build_init_state(plan_text, max_round, max_cross_total, plan_elements)
        config = {"configurable": {"thread_id": session_id}}
        return await self.agent.ainvoke(init_state, config=config)

    # 画图，排查调度问题时很有用
    def draw(self):
        data = self.agent.get_graph().draw_mermaid_png()
        with open("评审会图.png", "wb") as f:
            f.write(data)
