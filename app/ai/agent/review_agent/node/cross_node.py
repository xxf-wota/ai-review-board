from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage
import numpy as np

from app.ai.agent.review_agent import events, reviewers
from app.ai.agent.review_agent.node.extract_node import format_elements
from app.ai.agent.review_agent.node.speaker_node import first_question
from app.ai.agent.review_agent.reviewers import PROMPTS, REVIEWER_CONCERNS, REVIEWER_NAMES
from app.ai.agent.review_agent.schema.review_schema import CrossSchema
from app.ai.agent.review_agent.state.review_state import ReviewState
from app.ai.agent.review_agent.store import question_store as store
from app.ai.model.my_model import MyModel
from app.ai.prompt.builder_prompt import BuilderPromptYaml
from app.ai.utils import embed_util
from app.ai.utils.text_util import similarity

"""
交叉质询节点
上一位评审说完话之后，问四位评审"你想不想接话"，挑严重度最高的一位发言

判定只做一次模型调用，一次拿到四个人的意愿：
逐个去问要四次调用，而且每个人都只看自己那一份，判断不出"谁最该说话"

发言正文不在状态里：判定要看的"刚才的发言"、接话针对的那句话，以及
"这句接话是不是把整场里已经问过的某个问题又说了一遍"，都按索引去库里取
（store/question_store.py）
"""
# 读取外部配置文件
prompt = BuilderPromptYaml.get_prompt("review/crosstalk.yaml")
# 接话时的表达规则：人设提示词里写着"只提一个问题、不评价别人"，
# 那套要求会和接话冲突，所以接话时在它后面追加这一段，把格式要求改过来
speak_prompt = BuilderPromptYaml.get_prompt("review/crosstalk_speak.yaml")
# 接话判定只看最近几条发言：给多了模型挑花眼，给少了它不知道刚才在吵什么
RECENT_LIMIT = 6


# 一次调用，拿到四位评审各自的接话意愿。
# recent_rows 是最近几条发言的正文（从库里按索引取的，不是状态里带的）
# 必须是 async：cross_node 是异步节点，在里面调用同步阻塞的 invoke 会卡住事件循环，
# 后续的流式发言一个 chunk 都收不到（实测踩过这个坑）
async def judge_cross(plan_elements: dict, recent_rows: list, spoke_in_issue: list) -> list:
    content = (
        f"以下是学生提交的方案要素表：\n{format_elements(plan_elements)}\n\n"
        f"以下是本场评审会刚才的发言：\n{store.recent_transcript(recent_rows)}\n\n"
        f"本议题已经发过言的评审是：{'、'.join(spoke_in_issue) if spoke_in_issue else '无'}\n"
        "请判断每一位评审是否要接话。"
    )
    user_msg = {"messages": [HumanMessage(content=content)]}

    def build(model):
        return create_agent(
            model=model,
            system_prompt=prompt,
            response_format=ProviderStrategy(schema=CrossSchema),
        )

    try:
        rs = await build(MyModel.get_local_model()).ainvoke(user_msg)
    except Exception as e:
        print(f"-----------本地模型接话判定失败，降级商业模型：{e}------------")
        rs = await build(MyModel.get_model()).ainvoke(user_msg)
    return rs["structured_response"].model_dump().get("reactions") or []


# 挑出该接话的那位：想接话、本议题还没发过言、严重度最高
def pick_reaction(reactions: list, spoke_in_issue: list, order: list, index: list = None):
    candidates = [
        r for r in reactions
        if r.get("want") and r.get("role") in order and r.get("role") not in spoke_in_issue
    ]
    if not candidates:
        return None

    # 统计每个人整场已经接过几次话。看索引就够，不用把发言正文取回来
    cross_count = {}
    for item in (index or []):
        if item.get("question_type") == "cross":
            who = item.get("speaker_role")
            cross_count[who] = cross_count.get(who, 0) + 1

    # 排序依据：严重度高的优先；严重度相同时，让还没怎么接过话的人先说，避免同一个人反复插话
    """
    第一优先级：严重度高的优先
    第二优先级：还没怎么接过话的人先说
    第三优先级：按名单顺序说，这是因为上面两种都满足才需要这个判定，而名单索引是唯一的
    """
    return max(candidates, key=lambda r: (
        r.get("severity", 0),
        -cross_count.get(r.get("role"), 0),
        -order.index(r["role"]),
    ))


# 接话去重的判据：本地向量余弦，线取 0.80（app/ai/utils/embed_util.py）
#
# 为什么不用字面相似度：8 场评审会、26 条真实接话里，字面 Jaccard >= 0.7 一条都挡不住
#   （最高只有 0.593）。"换个说法把同一个问题再问一遍"它根本认不出来 —— 标定数据见
#   scripts/calibrate_dedup.py 与 data/_dedup_calibration.txt
# 为什么是 0.80 而不是 0.70：0.70 在真数据上会挡掉 18/26，其中 #202(0.856)、#221(0.767)、
#   #199(0.779) 是"不同评审从各自角度追问同一主题"，那正是交叉质询的设计意图，不该当冷饭
# 为什么比全场之前问过的每一句：只比"对方原话 + 自己问过的"会漏掉 #176 ——
#   它把别的议题里的"每月调用量预计多少次"逐字复述了一遍（字面 1.000），
#   而那句既不是它接的话，也不是它自己问的。放宽到全场，0.80 挡掉 13/26
DEDUP_COS = 0.80
# 向量模型不可用时的兜底：退回字面相似度（弱得多，但总比完全不判好）
DEDUP_BIGRAM = 0.7


# 这句接话是不是在炒冷饭。是的话返回被重复的那一句，不是则返回空串。
# 返回原句而不是 True/False，是为了让重试时能把它念给模型听 ——
# "你重复了自己之前问过的「……」，换一个角度"比"你重复了"有用得多（实测有效）
async def _repeated_question(text: str, session_id: str) -> str:
    if not text:
        return ""
    rows = await store.all_rows(session_id)
    others = [r.get("question", "") for r in rows if (r.get("question") or "").strip()]
    if not others:
        return ""

    if embed_util.available():
        vectors = await embed_util.aencode([text] + others)
        if vectors is not None:
            hits = [embed_util.cosine(vectors[0], v) for v in vectors[1:]]
            best = int(np.argmax(hits))
            return others[best] if hits[best] >= DEDUP_COS else ""

    # 兜底路径：字面相似度。逐句算，挑最高的那句
    worst, top = "", 0.0
    for question in others:
        score = similarity(text, question)
        if score >= DEDUP_BIGRAM and score > top:
            worst, top = question, score
    return worst


# 图节点：判定 + 接话发言
async def cross_node(state: ReviewState):
    plan_elements = state.get("plan_elements") or {}
    index = list(state.get("question_index") or [])
    spoke = list(state.get("spoke_in_issue") or [])
    order = state.get("speaker_order") or ["tech", "cost", "compliance", "user"]

    # 判定要看刚才的发言，正文在库里，按索引取最近几条
    recent_rows = await store.rows_of(index[-RECENT_LIMIT:])

    # 判定失败不能让会议中断，当作"没人接话"继续往下走
    try:
        reactions = await judge_cross(plan_elements, recent_rows, spoke)
    except Exception as e:
        print(f"-----------接话判定失败，本议题不接话：{e}------------")
        return {"cross_checked": True}

    pick = pick_reaction(reactions, spoke, order, index)
    if not pick:
        print("本议题没有人需要接话")
        return {"cross_checked": True}

    role = pick["role"]
    # 接话针对的是上一位主问题发言者。
    # 索引里只有 id，问题原文按 id 去库里取（判定过的条目状态里已经不存原文了）
    last = store.last_ref(index)
    target = (last or {}).get("speaker_role") or ""
    target_question = (await store.question_of(last)) if last else ""

    # 只把对方那一句话拎出来，不要把整段记录倒给它
    # 之前把全部发言记录给进去，模型会直接抄自己那句，完全不理对方
    # 同时把"我自己的关注点"写进去：不写的话，弱模型会问成别人的关注点（技术评审去问成本）
    content = (
        f"以下是学生提交的方案要素表：\n{format_elements(plan_elements)}\n\n"
        f"刚才 {REVIEWER_NAMES.get(target, target)} 说的是：\n「{target_question}」\n\n"
        f"你只能从自己的关注点提问，你的关注点只有：{REVIEWER_CONCERNS.get(role, '')}\n"
        f"你接这句话的理由（主持人整理）：{pick.get('point', '')}\n\n"
        f"请针对「{target_question}」这件事，从你自己的关注点提出一个问题。\n"
        "严禁问成上面关注点以外的方向。"
    )
    user_msg = {"messages": [HumanMessage(content=content)]}

    # 人设 + 接话表达规则，后者在后，用来覆盖人设里"只提一个问题"的格式要求
    system_prompt = f"{PROMPTS[role]}\n\n{speak_prompt}"
    # 这里用 ainvoke，不用 astream：
    # 一个节点里连续做两次模型调用时，内层 astream(stream_mode="messages") 一个 chunk 都收不到
    # （实测 iter=0），而 ainvoke 正常返回。接话本来就只有一句话，一次性推给前端没有损失
    try:
        agent = create_agent(
            model=MyModel.get_local_model(),
            system_prompt=system_prompt,
        )
    except Exception as e:
        print(f"-----------本地模型不可用，降级商业模型：{e}------------")
        agent = create_agent(
            model=MyModel.get_model(),
            system_prompt=system_prompt,
        )

    text = ""
    try:
        rs = await agent.ainvoke(user_msg)
        msgs = rs.get("messages") or []
        if msgs:
            text = first_question(str(msgs[-1].content or ""))
    except Exception as e:
        print(f"-----------接话发言失败：{e}------------")

    # 质量兜底：接话不能是炒冷饭 —— 抄对方原话等于没接，重复自己之前问过的更难看，
    # 把别的评审问过的问题再问一遍同样是白说。重试一次，还是不行就放弃这次接话
    who = REVIEWER_NAMES.get(role, role)
    session_id = state.get("session_id") or ""
    repeated = await _repeated_question(text, session_id)
    if repeated:
        print(f"-----------{who}接话在炒冷饭，重试一次：{text[:40]}------------")
        try:
            rs = await agent.ainvoke({"messages": [HumanMessage(content=(
                user_msg["messages"][0].content
                + f"\n\n注意：你上一次的回答和这场评审会里已经问过的这句话几乎一样：\n「{repeated}」\n"
                  "这次必须针对对方那句话里的一个具体点，换一个角度发问，不能再和它重复。"
            ))]})
            retry_msgs = rs.get("messages") or []
            text = first_question(str(retry_msgs[-1].content or "")) if retry_msgs else ""
        except Exception as e:
            print(f"-----------{who}接话重试失败：{e}------------")
        if await _repeated_question(text, session_id):
            print(f"-----------{who}接话仍在炒冷饭，本次放弃接话------------")
            return {"cross_checked": True}

    target_name = REVIEWER_NAMES.get(target, target)
    # 模型没吐出内容时兜底：拿判定阶段整理的理由顶上，并写清是针对谁说的
    if not text:
        text = f"{target_name}刚才的说法，{pick.get('point') or '还需要补充依据'}。"
    # 一次性推给前端；带上"接的是谁"，前端才能画出那个箭头
    events.speaker_start(role, who, "cross", target, target_name)
    events.token(role, text)
    events.speaker_end(role, who, text, "cross", target, target_name)
    # 接话也是学生要回答的一条：落库拿 id，正文进库、索引只留 id
    entry = store.new_entry(state.get("round", 1) or 1, role, "cross",
                            question=text, target=target,
                            severity=pick.get("severity", 0) or 0)
    await store.ask(state.get("session_id") or "", entry)
    index.append(entry)
    if role not in spoke:
        spoke.append(role)

    return {
        # 注意：不改 pending_question / pending_type / pending_from。
        # "学生接下来答哪一条"是 manager 从索引里推出来的（本议题第一条没判过的），
        # 接话节点在这儿写这三个字段只会被覆盖，还容易让人误以为接话抢了主问题的位置
        "meeting_phase": "cross",
        # 置位后 manager 才会推进到下一位，避免在 cross 上打转
        "cross_checked": True,
        "cross_in_issue": state.get("cross_in_issue", 0) + 1,
        "cross_total": state.get("cross_total", 0) + 1,
        "spoke_in_issue": spoke,
        "question_index": index,
    }
