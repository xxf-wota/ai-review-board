import re

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import AIMessage, HumanMessage

from app.ai.agent.review_agent import events
from app.ai.agent.review_agent.schema.review_schema import PlanElementsSchema
from app.ai.agent.review_agent.state.review_state import ReviewState
from app.ai.model.my_model import MyModel
from app.ai.prompt.builder_prompt import BuilderPromptYaml

"""
方案要素抽取节点
把学生提交的方案全文，拆成结构化要素表 + 一份"方案没写什么"的缺失清单
评审提问时只基于要素表，控制 token 也避免评审离题
"""
# 读取外部配置文件
prompt = BuilderPromptYaml.get_prompt("review/extract.yaml")


# 把要素表拼成可读文本，既用于展示，也作为评审提问的输入
def format_elements(elements: dict) -> str:
    # 逐项取值，缺项统一兜底，避免模型返回不全时直接报错
    goal = elements.get("goal") or "未提及"
    tech = elements.get("tech") or []
    budget = elements.get("budget") or "未提及"
    schedule = elements.get("schedule") or "未提及"
    risks = elements.get("risks") or []
    compliance = elements.get("compliance") or "未提及"
    users = elements.get("users") or "未提及"
    missing = elements.get("missing") or []

    lines = [
        f"【项目目标】{goal}",
        f"【技术方案】{'；'.join(_clean(i) for i in tech) if tech else '未提及'}",
        f"【预算与资源】{budget}",
        f"【进度安排】{schedule}",
        f"【已识别风险】{'；'.join(_clean(i) for i in risks) if risks else '未提及'}",
        f"【数据合规】{compliance}",
        f"【目标用户】{users}",
        f"【方案缺失项】{'、'.join(missing) if missing else '无'}",
    ]
    return "\n".join(lines)


# 模型给出的要点常带句号分号，拼接后会出现"。；"这种脏标点，统一去掉尾部标点
def _clean(text: str) -> str:
    return str(text).strip().rstrip("。；;，,、")


# 创建抽取用的智能体
def _build_agent(model):
    return create_agent(
        model=model,
        system_prompt=prompt,
        # 与项目原有节点保持一致的写法，本地 qwen2.5:7b 支持这种原生结构化输出，qwen3.5:9b 不支持
        response_format=ProviderStrategy(schema=PlanElementsSchema),
        middleware=[
            ModelCallLimitMiddleware(
                thread_limit=3,
                exit_behavior="end",
            )
        ],
    )


# 字段值清洗
# 实测本地模型会把提示词里的说明文字抄进字段值，例如
#   budget = "未提及，在 missing 中加入 预算。"
# 这类尾巴必须裁掉，否则要素表面板上会显示出提示词，很难看
_CONTAMINATION = ("missing", "加入", "填入", "应填", "记入")


def _normalize_fields(elements: dict) -> dict:
    for key in ("goal", "budget", "schedule", "compliance", "users"):
        value = str(elements.get(key) or "").strip()
        # 先裁掉说明文字的尾巴
        for marker in _CONTAMINATION:
            idx = value.find(marker)
            if idx > 0:
                value = value[:idx]
        # 值里出现"未提及"却还带着别的内容，说明模型没抽出来，只保留它前面那段
        if "未提及" in value:
            head = value.split("未提及")[0]
            value = re.sub(r"[\s，,；;。、：:]+$", "", head).strip() or "未提及"
        # 去掉尾部多余标点
        value = re.sub(r"[\s，,；;。、]+$", "", value)
        elements[key] = value or "未提及"
    return elements


# 缺失项兜底：某一项是不是缺，从字段本身就能直接判断，不该交给模型自由发挥
def _normalize_missing(elements: dict) -> dict:
    missing = list(elements.get("missing") or [])

    # 模型自己写的缺失项措辞不固定（预算 / 预算说明 / 风险列表），按关键词判断是否已经覆盖，避免列表重复
    def covered(*keys: str) -> bool:
        return any(any(k in item for k in keys) for item in missing)

    # 字段为 未提及 或 空，就说明方案确实没写，必须进入缺失清单
    if (elements.get("budget") or "未提及") == "未提及" and not covered("预算", "经费", "成本"):
        missing.append("预算")
    if (elements.get("schedule") or "未提及") == "未提及" and not covered("进度", "周期", "排期"):
        missing.append("进度安排")
    if not elements.get("risks") and not covered("风险"):
        missing.append("风险应对")
    if (elements.get("compliance") or "未提及") == "未提及" and not covered("合规", "隐私", "数据"):
        missing.append("数据合规")
    # 去重并保持原有顺序
    seen, result = set(), []
    for item in missing:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    elements["missing"] = result
    return elements


# 两个模型都抽不出来时的兜底要素表
# 抽取失败不能让会议开不下去：把已知缺失项列全，评审照样能问
def _degraded_elements(plan_text: str) -> dict:
    return {
        "goal": plan_text.strip()[:120] or "未提及",
        "tech": [],
        "budget": "未提及",
        "schedule": "未提及",
        "risks": [],
        "compliance": "未提及",
        "users": "未提及",
        "missing": ["预算", "进度安排", "风险应对", "数据合规", "量化指标"],
    }


# 核心函数：方案全文 -> 要素表字典
def extract_elements(plan_text: str) -> dict:
    user_msg = {"messages": [HumanMessage(content=plan_text)]}
    try:
        # 优先本地模型，省额度
        rs = _build_agent(MyModel.get_local_model()).invoke(user_msg)
    except Exception as e:
        print(f"-----------本地模型抽取失败，降级商业模型：{e}------------")
        try:
            rs = _build_agent(MyModel.get_model()).invoke(user_msg)
        except Exception as e2:
            # 两个模型都不可用时不能抛异常，否则整场会议直接死掉，演示就砸了
            print(f"-----------商业模型也不可用，降级为兜底要素表：{e2}------------")
            return _degraded_elements(plan_text)
    return _normalize_missing(_normalize_fields(rs["structured_response"].model_dump()))


# 图节点：抽取要素并推进会议阶段
def extract_node(state: ReviewState):
    plan_text = state.get("plan_text", "")
    elements = extract_elements(plan_text)
    elements_text = format_elements(elements)
    # 要素表单独作为一条事件推给前端，界面上要把它展示成一个面板
    events.emit({"event": "elements", "text": elements_text})
    # 自定义AI回复消息，把要素表回显给前端
    ai_msg = f"\n方案要素抽取完成：\n{elements_text}\n"
    return {
        "messages": [AIMessage(content=ai_msg)],
        "plan_elements": elements,
        "meeting_phase": "main",
    }
