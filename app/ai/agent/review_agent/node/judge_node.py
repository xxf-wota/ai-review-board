from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import AIMessage, HumanMessage

from app.ai.agent.review_agent import events
from app.ai.agent.review_agent.node.extract_node import format_elements
from app.ai.agent.review_agent.node.speaker_node import (
    REVIEWER_CONCERNS,
    REVIEWER_NAMES,
    issue_transcript,
)
from app.ai.agent.review_agent.schema.review_schema import JudgeSchema
from app.ai.agent.review_agent.state.review_state import ReviewState
from app.ai.model.my_model import MyModel
from app.ai.prompt.builder_prompt import BuilderPromptYaml

"""
回答判定节点
学生答完一道题后，由提问的那位评审来判断"我的疑问解决了吗"

一个议题里可能有好几位评审提问（主问题 + 接话），学生一条一条答，
每答完一条都由**那条问题的提问者**来判 —— 谁的疑问谁负责，判定标准才自洽。

这个节点只负责判定，不决定下一步去哪儿：
"还有没有没答的问题""要不要追问"由 manager 看着 question_log 决定。
调度和判定分开，是因为"下一步"要参考整个议题，不是单个判定能决定的
"""
# 读取外部配置文件
prompt = BuilderPromptYaml.get_prompt("review/judge.yaml")
# 前端"跳过"按钮发过来的标记。学生明说不会的时候，追问没有意义
SKIP_MARK = "（跳过）"


def is_skip(answer: str) -> bool:
    """学生没答上来：空回答，或者点了跳过"""
    text = (answer or "").strip()
    return (not text) or text.startswith(SKIP_MARK)


# 把发言记录按议题切开：每条主问题开一个新议题，后面的接话/追问都归到它名下
def split_issues(question_log: list) -> list:
    issues = []
    for item in (question_log or []):
        if item.get("question_type") == "main" or not issues:
            issues.append([item])
        else:
            issues[-1].append(item)
    return issues


# 当前议题 = 最后一条主问题开始往后那一段
def current_issue(question_log: list) -> list:
    issues = split_issues(question_log)
    return issues[-1] if issues else []


# 本议题里第一条还没判过的问题。学生要答的就是它，答完再轮到下一条
def first_unjudged(issue: list):
    for item in (issue or []):
        if not item.get("verdict"):
            return item
    return None


# 找到这次回答对应的是哪条记录：从后往前找第一条"还没判过、问题一样"的
def find_entry(question_log: list, question: str):
    log = question_log or []
    for i in range(len(log) - 1, -1, -1):
        if log[i].get("verdict"):
            continue
        if log[i].get("question") == question:
            return i
    # 问题文本对不上时退回"最后一条没判过的"，总比丢掉这次回答强
    for i in range(len(log) - 1, -1, -1):
        if not log[i].get("verdict"):
            return i
    return None


# 把"未答好的问题清单"从发言记录里推出来
# 每次都整体重算而不是逐条追加，保证清单和判定结果永远一致
def collect_unresolved(question_log: list) -> list:
    """一个议题里每位评审只问一次（主问题或接话），追问是这个人的第二次。

    所以按提问者归拢、以**最后一次判定**为准：
      - 追问答好了      -> 这个问题不算未答好，从清单里消失
      - 追了还是没答好  -> 进清单，严重度取两次里更高的那个

    清单里显示这位评审**最先问的那个问题**（议题的主线），追问单独附在后面，
    这样学生看到的是"我当时被问的是什么"，而不是被追问绕晕
    """
    result = []
    for group in split_issues(question_log):
        by_role = {}
        for item in group:
            by_role.setdefault(item.get("speaker_role"), []).append(item)
        for role, items in by_role.items():
            first, last = items[0], items[-1]
            if not last.get("verdict") or last["verdict"] == "resolved":
                continue
            followup = items[1] if len(items) > 1 else {}
            result.append({
                "round": group[0].get("round", 1),
                "speaker_role": role,
                "question": first.get("question", ""),
                "student_answer": first.get("student_answer", ""),
                "followup_question": followup.get("question", ""),
                "followup_answer": followup.get("student_answer", ""),
                "verdict": last.get("verdict", ""),
                "severity": max(int(x.get("severity") or 0) for x in items),
                "comment": last.get("verdict_comment", ""),
            })
    # 严重度高的排前面，会议纪要按这个顺序念
    result.sort(key=lambda x: -x["severity"])
    return result


# 让模型判定一次回答。必须是 async：judge_node 是异步节点，
# 在里面调用同步阻塞的 invoke 会卡住事件循环（cross_node 踩过这个坑）
async def judge_answer(plan_elements: dict, role: str, question: str,
                       answer: str, question_log: list) -> dict:
    content = (
        f"以下是学生提交的方案要素表：\n{format_elements(plan_elements)}\n\n"
        f"以下是本议题已经发生的问答：\n{issue_transcript(question_log)}\n\n"
        f"现在要判定的是【{REVIEWER_NAMES.get(role, role)}】提的这个疑问：\n「{question}」\n\n"
        f"这位评审的关注点是：{REVIEWER_CONCERNS.get(role, '')}\n\n"
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
    question = state.get("pending_question") or ""
    answer = (state.get("student_answer") or "").strip()
    log = [dict(item) for item in (state.get("question_log") or [])]
    name = REVIEWER_NAMES.get(role, role)

    if is_skip(answer):
        # 学生明说不会：直接记成未解决，追问方向留空。
        # 追问一个已经承认不会的问题没有信息量；顺带这条路径不调模型，结果完全确定，
        # 验收脚本能稳定复现
        verdict, severity, hint, comment = "unresolved", 3, "", "学生没有回答这个问题"
    else:
        try:
            r = await judge_answer(state.get("plan_elements") or {}, role, question, answer, log)
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

    # 把学生的回答、判定、以及"该往哪儿追"都写回这条记录
    idx = find_entry(log, question)
    if idx is not None:
        log[idx]["student_answer"] = answer
        log[idx]["verdict"] = verdict
        log[idx]["verdict_comment"] = comment
        log[idx]["severity"] = severity
        log[idx]["followup_hint"] = hint

    events.emit({
        "event": "verdict",
        "role": role,
        "name": name,
        "question": question,
        "verdict": verdict,
        "severity": severity,
        "comment": comment,
    })

    verdict_cn = {"resolved": "已答清楚", "partial": "答得不全", "unresolved": "未解决"}[verdict]
    ai_msg = f"\n【判定】{name} 的问题：{verdict_cn}（严重度 {severity}）。{comment}\n"

    return {
        "messages": [AIMessage(content=ai_msg)],
        "question_log": log,
        # 每次整体重算，保证清单和判定结果永远一致
        "unresolved": collect_unresolved(log),
        # 交给 manager 看着整个议题决定下一步
        "meeting_phase": "advance",
        "student_answer": "",
    }
