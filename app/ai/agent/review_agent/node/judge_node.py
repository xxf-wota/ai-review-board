from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import AIMessage, HumanMessage

from app.ai.agent.review_agent import events
from app.ai.agent.review_agent.node.extract_node import format_elements
from app.ai.agent.review_agent.node.manager_node import MAX_FOLLOWUP
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
学生答完一个问题后，由提问的那位评审来判断"我的疑问解决了吗"

判定结果决定两件事：
  1. 要不要追一层（最多 MAX_FOLLOWUP 层，且只由提问者追）
  2. 这个问题要不要进"未答好的问题清单"

判定失败不能中断会议：统一按"未解决但不追问"处理，宁可少问一句，也不能卡死
"""
# 读取外部配置文件
prompt = BuilderPromptYaml.get_prompt("review/judge.yaml")
# 前端"跳过"按钮发过来的标记。学生明说不会的时候，追问没有意义
SKIP_MARK = "（跳过）"


def is_skip(answer: str) -> bool:
    """学生没答上来：空回答，或者点了跳过"""
    text = (answer or "").strip()
    return (not text) or text.startswith(SKIP_MARK)


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
# 每次都整体重算而不是逐条追加：追问答好了要能把前面那条 partial 顶掉，
# 追加式的写法做不到这件事，还会重复
def collect_unresolved(question_log: list) -> list:
    issues, result = [], []
    for item in (question_log or []):
        # 每条主问题开一个新议题，后面的接话/追问都归到它名下
        if item.get("question_type") == "main" or not issues:
            issues.append([item])
        else:
            issues[-1].append(item)

    for group in issues:
        judged = [x for x in group if x.get("verdict")]
        if not judged:
            continue
        last = judged[-1]
        # 一个议题最后是答清楚了的，就不算未答好 —— 追问那次答好也算解决
        if last.get("verdict") == "resolved":
            continue
        result.append({
            "round": group[0].get("round", 1),
            "speaker_role": last.get("speaker_role", ""),
            "question": last.get("question", ""),
            "student_answer": last.get("student_answer", ""),
            "verdict": last.get("verdict", ""),
            "severity": max(int(x.get("severity") or 0) for x in judged),
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


# 图节点：判定 + 决定追不追问
async def judge_node(state: ReviewState):
    role = state.get("pending_from") or state.get("current_speaker") or "tech"
    question = state.get("pending_question") or ""
    answer = (state.get("student_answer") or "").strip()
    depth = state.get("followup_depth", 0) or 0
    log = [dict(item) for item in (state.get("question_log") or [])]
    name = REVIEWER_NAMES.get(role, role)

    if is_skip(answer):
        # 学生明说不会：不再追问，直接记成未解决。
        # 走这条短路还有个好处：结果完全确定，验收脚本能稳定复现
        verdict, severity, need, hint, comment = (
            "unresolved", 3, False, "", "学生没有回答这个问题",
        )
    else:
        try:
            r = await judge_answer(state.get("plan_elements") or {}, role, question, answer, log)
            verdict = str(r.get("verdict") or "unresolved")
            severity = int(r.get("severity") or 3)
            need = bool(r.get("need_followup"))
            hint = str(r.get("followup_question") or "").strip()
            comment = str(r.get("comment") or "")
        except Exception as e:
            print(f"-----------回答判定失败，按未解决处理：{e}------------")
            verdict, severity, need, hint, comment = (
                "unresolved", 3, False, "", "判定失败，按未解决处理",
            )

    # 只认这三个值，模型抽风给了别的就当未解决
    if verdict not in ("resolved", "partial", "unresolved"):
        verdict = "unresolved"
    severity = max(1, min(5, severity))

    # 把学生的回答和判定写回这条记录
    idx = find_entry(log, question)
    if idx is not None:
        log[idx]["student_answer"] = answer
        log[idx]["verdict"] = verdict
        log[idx]["verdict_comment"] = comment
        log[idx]["severity"] = severity

    # 追问的三个条件：判定说还有没问清的点、这一问还没追过、而且确实没答清楚
    do_followup = need and depth < MAX_FOLLOWUP and verdict != "resolved"

    events.emit({
        "event": "verdict",
        "role": role,
        "name": name,
        "question": question,
        "verdict": verdict,
        "severity": severity,
        "comment": comment,
        "followup": do_followup,
    })

    verdict_cn = {"resolved": "已答清楚", "partial": "答得不全", "unresolved": "未解决"}[verdict]
    ai_msg = f"\n【判定】{name} 的问题：{verdict_cn}（严重度 {severity}）。{comment}\n"

    update = {
        "messages": [AIMessage(content=ai_msg)],
        "question_log": log,
        # 每次整体重算，保证清单和判定结果永远一致
        "unresolved": collect_unresolved(log),
    }
    if do_followup:
        # 追问还是这位评审来问，把方向交给他，由发言节点用他的口吻组织成问题
        update.update({
            "meeting_phase": "followup",
            "pending_type": "followup",
            "followup_depth": depth + 1,
            "followup_hint": hint,
        })
    else:
        update.update({
            "meeting_phase": "advance",
            "followup_hint": "",
        })
    return update
