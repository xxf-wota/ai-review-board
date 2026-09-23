import contextlib
import json
import time
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.ai.agent.memory.manager.memory_manager import MemoryManager
from app.ai.agent.memory.manager.session_mananger import SessionManager
from app.ai.agent.multi_agent.node.answer_node import answer_node
from app.ai.agent.multi_agent.node.chat_node import chat_node
from app.ai.agent.multi_agent.node.evaluate_node import evaluate_node
from app.ai.agent.multi_agent.node.intent_node import intent_node
from app.ai.agent.multi_agent.node.manager_node import manager_node
from app.ai.agent.multi_agent.node.question_node import question_node
from app.ai.agent.multi_agent.state.exam_state import ExamState

"""
模拟面试智能体（带会话管理版）

相比 exam_graph.py，多了三件事：
1. 会话创建与校验：session_id 存 Redis 带过期时间，并校验归属，防止伪造别人的会话
2. 异步会话锁：同一个会话同一时刻只允许一个请求进来，避免并发把记忆和图状态写乱
3. 四层记忆接入：每轮开始拼记忆提示词、结束后更新长期记忆/画像/摘要
"""

# 会话在 Redis 里的存活时间，1 天
SESSION_TTL = 60 * 60 * 24
# 会话锁的持有上限，5 分钟。超过自动释放，避免上一个请求崩了把会话永久锁死
LOCK_TTL_MS = 5 * 60 * 1000
# 释放锁的 Lua 脚本：先比对 token 再删，保证原子性，防止误删别人的锁
UNLOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


class ExamGraphAgent:

    def __init__(self, checkpoint, redis):
        self.memory = checkpoint
        # Redis 客户端，会话、锁、窗口记忆、画像都靠它
        self.redis = redis
        self.agent = self.get_agent()

    # 构建图：和 exam_graph.py 完全一样，只是外面包了会话和记忆
    def get_agent(self):
        graph = StateGraph(ExamState)
        graph.add_node("intent", intent_node)
        graph.add_node("question", question_node)
        graph.add_node("manager", manager_node)
        graph.add_node("answer", answer_node)
        graph.add_node("evaluate", evaluate_node)
        graph.add_node("chat", chat_node)

        graph.add_edge(START, "manager")
        graph.add_edge("intent", "manager")
        graph.add_edge("question", "manager")
        graph.add_edge("answer", "manager")
        graph.add_edge("evaluate", "manager")
        graph.add_edge("chat", "manager")

        self.agent = graph.compile(checkpointer=self.memory)
        return self.agent

    # ---------- 会话 ----------
    async def create_session(self, user_id) -> str:
        """新建一个会话，返回 session_id"""
        # 用 uuid 做键，保证唯一
        session_id = f"session:{uuid.uuid4().hex}"
        if self.redis:
            data = {"user_id": user_id, "create_time": time.time()}
            # 存 Redis 并设置 1 天过期
            await self.redis.set(session_id, json.dumps(data), ex=SESSION_TTL)
        print(f"新建会话：{session_id}（用户 {user_id}）")
        return session_id

    async def check_session(self, user_id, session_id):
        """
        校验会话：存在 + 属于这个用户
        返回 (是否通过, 原因)。校验归属是为了防止有人拿别人的 session_id 接着聊
        """
        if not session_id:
            return False, "会话ID为空"
        if not self.redis:
            return False, "Redis 不可用，无法校验会话"
        data = await self.redis.get(session_id)
        if not data:
            return False, "会话不存在或已过期"
        info = json.loads(data)
        if str(info.get("user_id")) != str(user_id):
            return False, "会话不属于该用户"
        return True, "会话有效"

    # ---------- 异步会话锁 ----------
    @contextlib.asynccontextmanager
    async def _session_lock(self, session_id: str):
        """
        同一个 session 同一时刻只允许一个请求进入
        用 SET NX PX 抢锁，抢不到直接报"会话忙"，而不是排队等（等下去会拖垮连接）
        """
        key = f"lock:session:{session_id}"
        token = uuid.uuid4().hex
        # nx=True：key 不存在才设置成功；px：毫秒级过期
        ok = await self.redis.set(key, token, nx=True, px=LOCK_TTL_MS)
        if not ok:
            raise RuntimeError("会话忙，请稍后再试")
        try:
            yield
        finally:
            # 用 Lua 保证"比对 + 删除"是原子的，不会误删别人的锁
            try:
                await self.redis.eval(UNLOCK_LUA, 1, key, token)
            except Exception:
                pass

    # ---------- 对话 ----------
    async def chat(self, question, user_id, session_id):
        print(f"进入聊天：问题={question} 用户={user_id} 会话={session_id}")

        # 1. 校验会话存在且归属正确
        ok, reason = await self.check_session(user_id, session_id)
        if not ok:
            print(f"会话校验失败：{reason}")
            yield reason
            return

        # 2. 建会话管理器（四层记忆对象），把用户这一问先记进窗口记忆
        session_manager = SessionManager(session_id, user_id)
        await session_manager.save("user", question)

        # 3. 把四层记忆拼成一段提示词，作为 system 消息喂给图
        memory = await session_manager.build_prompt(user_id, question)
        # build_prompt 返回的是字典，转成 SystemMessage，别直接塞 dict 进 messages
        memory_prompt = SystemMessage(content=memory["content"])

        user_msg = {"messages": [memory_prompt, HumanMessage(content=question)]}
        config = {"configurable": {"thread_id": session_id}}

        ai_answer = ""
        try:
            # 4. 整个处理过程放在会话锁里，同一会话的并发请求会被挡在外面
            async with self._session_lock(session_id):
                async for mode, chunk in self.agent.astream(
                    user_msg, config, stream_mode=["messages", "custom"]
                ):
                    if mode == "messages":
                        message, meta = chunk
                        if message.content:
                            ai_answer += message.content
                            yield message.content
                    else:
                        # custom 推出来的可能是字典，统一转成字符串再累加
                        # （原代码直接 +=，推 dict 时会 TypeError）
                        text = chunk if isinstance(chunk, str) else json.dumps(chunk, ensure_ascii=False)
                        ai_answer += text
                        yield text

                # 5. 记忆更新也放在锁里：放到锁外面的话，
                # 两个并发请求会同时改同一份记忆，互相覆盖
                await session_manager.save("ai", ai_answer)
                await MemoryManager(session_manager).update(user_id, question)
        except Exception as e:
            print(f"会话处理异常：{e}")
            yield f"处理失败：{e}"

    # 画图，排查调度问题时很有用
    def draw(self):
        data = self.agent.get_graph().draw_mermaid_png()
        with open("考试图.png", "wb") as f:
            f.write(data)
