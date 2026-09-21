from langgraph.config import get_stream_writer

"""
会议事件出口
前端要渲染"成本评审 ⟶ 接话 → 技术评审"这种箭头，就必须知道谁在说话、
这是主问题还是接话、接的是谁，所以往外推的是结构化字典，不是纯文本

事件类型：
    speaker_start  有人开始发言，前端据此开一个气泡
    token          流式吐出来的一个字/词
    speaker_end    发言结束，带上完整问题
    elements       方案要素表抽取完成
    meeting_end    会议结束
"""


def emit(event: dict) -> None:
    # 不在流式上下文里时（例如脚本直接 ainvoke）写不出去，静默忽略即可
    try:
        get_stream_writer()(event)
    except Exception:
        pass


# 发言开始
def speaker_start(role: str, name: str, question_type: str, target: str = "", target_name: str = ""):
    emit({
        "event": "speaker_start",
        "role": role,
        "name": name,
        "question_type": question_type,
        "target": target,
        "target_name": target_name,
    })


# 流式吐字
def token(role: str, text: str):
    emit({"event": "token", "role": role, "text": text})


# 发言结束
def speaker_end(role: str, name: str, question: str, question_type: str,
                target: str = "", target_name: str = ""):
    emit({
        "event": "speaker_end",
        "role": role,
        "name": name,
        "question": question,
        "question_type": question_type,
        "target": target,
        "target_name": target_name,
    })
