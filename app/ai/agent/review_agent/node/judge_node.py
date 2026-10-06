from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage

from app.ai.agent.review_agent import events, reviewers
from app.ai.agent.review_agent.node.extract_node import format_elements
from app.ai.agent.review_agent.schema.review_schema import JudgeSchema
from app.ai.agent.review_agent.state.review_state import ReviewState
from app.ai.agent.review_agent.store import question_store as store
from app.ai.model.my_model import MyModel
from app.ai.prompt.builder_prompt import BuilderPromptYaml

"""
回答判定节点
学生答完一道题后，由提问的那位评审来判断"我的疑问解决了吗"

一个议题里可能有好几位评审提问（主问题 + 接话），学生一条一条答，
每答完一条都由**那条问题的提问者**来判 —— 谁的疑问谁负责，判定标准才自洽。

这个节点只负责判定，不决定下一步去哪儿：
"还有没有没答的问题""要不要追问"由 manager 看着索引决定。
调度和判定分开，是因为"下一步"要参考整个议题，不是单个判定能决定的

正文的去向：学生的回答和判定结果都写回库里那条记录（store.save_verdict），
状态里的索引只留下 id、verdict、severity 和 followup_hint
"""
# 读取外部配置文件
prompt = BuilderPromptYaml.get_prompt("review/judge.yaml")
# 前端"跳过"按钮发过来的标记。学生明说不会的时候，追问没有意义
SKIP_MARK = "（跳过）"


def is_skip(answer: str) -> bool:
    """学生没答上来：空回答，或者点了跳过"""
    text = (answer or "").strip()
    return (not text) or text.startswith(SKIP_MARK)


# 找到这次回答对应的是哪条记录：优先按 pending_id 认。
# 万一对不上（旧检查点、或者调用方直接 resume 没带 id），
# 退回"本议题第一条还没判过的"，总比把这次回答丢掉强
def _pending_entry(index: list, pending_id: int):
    issue = store.current_issue(index)
    if pending_id:
        for item in issue:
            if item.get("id") == pending_id:
                return item
    return store.first_unjudged(issue)


# 让模型判定一次回答。必须是 async：judge_node 是异步节点，
# 在里面调用同步阻塞的 invoke 会卡住事件循环（cross_node 踩过这个坑）
async def judge_answer(plan_elements: dict, role: str, question: str,
                       answer: str, issue_rows: list) -> dict:
    content = (
        f"以下是学生提交的方案要素表：\n{format_elements(plan_elements)}\n\n"
        f"以下是本议题已经发生的问答：\n{store.issue_transcript(issue_rows)}\n\n"
        f"现在要判定的是【{reviewers.REVIEWER_NAMES.get(role, role)}】提的这个疑问：\n「{question}」\n\n"
        f"这位评审的关注点是：{reviewers.REVIEWER_CONCERNS.get(role, '')}\n\n"
        f"学生刚才的回答是：\n「{answer}」\n\n"
        "请判断这个回答有没有解决他的疑问。"
    )
    user_msg = {"messages": [HumanMessage(content=content)]}

    def build(model):
        return create_agent(
            model=model,
            system_prompt=prompt,
            response_format=ProviderStrategy(schema=JudgeSchema),
        )

    try:
        rs = await build(MyModel.get_local_model()).ainvoke(user_msg)
    except Exception as e:
        print(f"-----------本地模型回答判定失败，降级商业模型：{e}------------")
        rs = await build(MyModel.get_model()).ainvoke(user_msg)
    return rs["structured_response"].model_dump()


# 图节点：判定刚答完的这个问题
async def judge_node(state: ReviewState):
    role = state.get("pending_from") or state.get("current_speaker") or "tech"
    answer = (state.get("student_answer") or "").strip()
    index = [dict(item) for item in (state.get("question_index") or [])]
    name = reviewers.REVIEWER_NAMES.get(role, role)

    # 这次回答对应哪一条：manager 填的 pending_id 就是它
    entry = _pending_entry(index, state.get("pending_id") or 0)
    question = state.get("pending_question") or (entry or {}).get("question") or ""

    if is_skip(answer):
        # 学生明说不会：直接记成未解决，追问方向留空。
        # 追问一个已经承认不会的问题没有信息量；顺带这条路径不调模型，结果完全确定，
        # 验收脚本能稳定复现
        verdict, severity, hint, comment = "unresolved", 3, "", "学生没有回答这个问题"
    else:
        # 判定要看本议题已经发生的问答，正文按索引去库里取
        issue_rows = await store.rows_of(store.current_issue(index))
        try:
            r = await judge_answer(state.get("plan_elements") or {}, role,
                                   question, answer, issue_rows)
            verdict = str(r.get("verdict") or "unresolved")
            severity = int(r.get("severity") or 3)
            hint = str(r.get("followup_question") or "").strip() if r.get("need_followup") else ""
            comment = str(r.get("comment") or "")
        except Exception as e:
            print(f"-----------回答判定失败，按未解决处理：{e}------------")
            verdict, severity, hint, comment = "unresolved", 3, "", "判定失败，按未解决处理"

    # 只认这三个值，模型抽风给了别的就当未解决
    if verdict not in ("resolved", "partial", "unresolved"):
        verdict = "unresolved"
    severity = max(1, min(5, severity))

    if entry is not None:
        # 判定结果留在索引里（manager 要看着它决定追不追、追哪个），
        # 正文（学生的回答、判定评语）写回库里的那条记录，索引里不留
        entry["verdict"] = verdict
        entry["severity"] = severity
        entry["followup_hint"] = hint
        await store.save_verdict(entry.get("id") or 0, answer, verdict, severity, comment)
        # 这一条已经答完判完，问题原文不用再占着状态了
        entry.pop("question", None)

    events.emit({
        "event": "verdict",
        "role": role,
        "name": name,
        "question": question,
        "verdict": verdict,
        "severity": severity,
        "comment": comment,
    })

    return {
        "question_index": index,
        # 交给 manager 看着整个议题决定下一步
        "meeting_phase": "advance",
        "student_answer": "",
    }
