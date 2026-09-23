from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import AIMessage, HumanMessage

from app.ai.agent.review_agent import events
from app.ai.agent.review_agent.node.extract_node import format_elements
from app.ai.agent.review_agent.node.speaker_node import (
    PROMPTS,
    REVIEWER_CONCERNS,
    REVIEWER_NAMES,
    first_question,
)
from app.ai.agent.review_agent.schema.review_schema import CrossSchema
from app.ai.agent.review_agent.state.review_state import ReviewState
from app.ai.model.my_model import MyModel
from app.ai.prompt.builder_prompt import BuilderPromptYaml
from app.ai.utils.text_util import similarity

"""
交叉质询节点
上一位评审说完话之后，问四位评审"你想不想接话"，挑严重度最高的一位发言

判定只做一次模型调用，一次拿到四个人的意愿：
逐个去问要四次调用，而且每个人都只看自己那一份，判断不出"谁最该说话"
"""
# 读取外部配置文件
prompt = BuilderPromptYaml.get_prompt("review/crosstalk.yaml")
# 接话时的表达规则：人设提示词里写着"只提一个问题、不评价别人"，
# 那套要求会和接话冲突，所以接话时在它后面追加这一段，把格式要求改过来
speak_prompt = BuilderPromptYaml.get_prompt("review/crosstalk_speak.yaml")


# 把最近的发言拼成文本，让判定的模型知道刚才发生了什么
def _recent_transcript(question_log: list, limit: int = 6) -> str:
    lines = []
    for item in (question_log or [])[-limit:]:
        who = REVIEWER_NAMES.get(item["speaker_role"], item["speaker_role"])
        if item.get("question_type") == "cross":
            target = REVIEWER_NAMES.get(item.get("target_speaker", ""), "学生")
            lines.append(f"【{who}】接{target}的话：{item['question']}")
        else:
            lines.append(f"【{who}】{item['question']}")
    return "\n".join(lines) if lines else "（还没有人发言）"


# 一次调用，拿到四位评审各自的接话意愿
# 必须是 async：cross_node 是异步节点，在里面调用同步阻塞的 invoke 会卡住事件循环，
# 后续的流式发言一个 chunk 都收不到（实测踩过这个坑）
async def judge_cross(plan_elements: dict, question_log: list, spoke_in_issue: list) -> list:
    content = (
        f"以下是学生提交的方案要素表：\n{format_elements(plan_elements)}\n\n"
        f"以下是本场评审会刚才的发言：\n{_recent_transcript(question_log)}\n\n"
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
def pick_reaction(reactions: list, spoke_in_issue: list, order: list, question_log: list = None):
    candidates = [
        r for r in reactions
        if r.get("want") and r.get("role") in order and r.get("role") not in spoke_in_issue
    ]
    if not candidates:
        return None

    # 统计每个人整场已经接过几次话
    cross_count = {}
    for item in (question_log or []):
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


# 判断一句接话是不是在炒冷饭：抄了对方原话，或者重复自己之前问过的
# 本地模型实测两个毛病都会犯，抄对方的话等于没接，重复自己的话更难看
def _too_repetitive(text: str, target_question: str, question_log: list, role: str) -> bool:
    if not text:
        return False
    if target_question and similarity(text, target_question) >= 0.7:
        return True
    for item in question_log or []:
        if item.get("speaker_role") == role and similarity(text, item.get("question", "")) >= 0.7:
            return True
    return False


# 图节点：判定 + 接话发言
async def cross_node(state: ReviewState):
    plan_elements = state.get("plan_elements") or {}
    question_log = list(state.get("question_log") or [])
    spoke = list(state.get("spoke_in_issue") or [])
    order = state.get("speaker_order") or ["tech", "cost", "compliance", "user"]

    # 判定失败不能让会议中断，当作"没人接话"继续往下走
    try:
        reactions = await judge_cross(plan_elements, question_log, spoke)
    except Exception as e:
        print(f"-----------接话判定失败，本议题不接话：{e}------------")
        return {"cross_checked": True}

    pick = pick_reaction(reactions, spoke, order, question_log)
    if not pick:
        print("本议题没有人需要接话")
        return {"cross_checked": True}

    role = pick["role"]
    # 接话针对的是上一位主问题发言者
    # next() + 生成器表达式 + reversed() 组合，实现"从后往前找第一个满足条件的元素"
    last_main = next(
        (q for q in reversed(question_log) if q.get("question_type") in ("main", "followup")),
        None,
    )
    target = last_main["speaker_role"] if last_main else ""
    target_question = last_main["question"] if last_main else ""

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

    # 质量兜底：接话不能是炒冷饭。抄对方原话等于没接，重复自己之前问过的更难看
    # （本地模型实测两个毛病都会犯）重试一次，还是不行就放弃这次接话
    who = REVIEWER_NAMES.get(role, role)
    if _too_repetitive(text, target_question, question_log, role):
        print(f"-----------{who}接话在炒冷饭，重试一次：{text[:40]}------------")
        try:
            rs = await agent.ainvoke({"messages": [HumanMessage(content=(
                user_msg["messages"][0].content
                + "\n\n注意：你上一次的回答要么重复了对方的话，要么重复了你自己之前问过的问题。"
                  "这次必须针对对方那句话里的一个具体点，换一个角度发问。"
            ))]})
            retry_msgs = rs.get("messages") or []
            text = first_question(str(retry_msgs[-1].content or "")) if retry_msgs else ""
        except Exception as e:
            print(f"-----------{who}接话重试失败：{e}------------")
        if _too_repetitive(text, target_question, question_log, role):
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
    log = list(question_log)
    log.append({
        "round": state.get("round", 1),
        "speaker_role": role,
        "question_type": "cross",
        "target_speaker": target,
        "question": text,
        "verdict": "",
        "severity": pick.get("severity", 0),
        "followup_depth": 0,
        "ctype": pick.get("ctype", "rebut"),
    })
    if role not in spoke:
        spoke.append(role)

    return {
        "messages": [AIMessage(content=f"\n【{who}】⟶ 接{target_name}的话：{text}\n")],
        "pending_question": text,
        "pending_type": "cross",
        "meeting_phase": "cross",
        # 置位后 manager 才会推进到下一位，避免在 cross 上打转
        "cross_checked": True,
        "cross_in_issue": state.get("cross_in_issue", 0) + 1,
        "cross_total": state.get("cross_total", 0) + 1,
        "spoke_in_issue": spoke,
        "question_log": log,
    }
