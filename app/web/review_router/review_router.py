from uuid import uuid4

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from app.ai.agent.review_agent.node.extract_node import extract_elements, format_elements
from app.ai.tool.plan_parser import parse_plan_bytes
from app.ai.tool.review_dao import get_session, save_session

"""
评审会接口
M1 阶段只做两件事：收方案（文件或文本）、抽出要素表并落库
会议过程（评审发言、交叉质询）在后面阶段接
"""
review_router = APIRouter(prefix="/review", tags=["评审会"])

# 方案长度上限，超过就拦掉，避免把模型上下文撑爆
MAX_PLAN_CHARS = 20000


# 取标题：优先用前端传的，其次取正文第一行，都没有就给个默认名
def _guess_title(plan_title: str, plan_text: str) -> str:
    if plan_title and plan_title.strip():
        return plan_title.strip()[:255]
    for line in plan_text.splitlines():
        line = line.strip()
        if line:
            # 常见写法是"项目名称：xxx"，把前缀去掉更像标题
            for prefix in ("项目名称：", "项目名称:", "题目：", "题目:"):
                if line.startswith(prefix):
                    line = line[len(prefix):]
            return line[:255]
    return "未命名方案"


# 上传方案文件，解析成纯文本返回，此时还不落库
@review_router.post("/upload")
async def upload_plan(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传的文件是空的")
    try:
        plan_text = parse_plan_bytes(file.filename or "", data)
    except ValueError as e:
        # 格式不支持、扫描件取不到字，都属于用户能自己解决的问题，返回 400 带上原因
        raise HTTPException(status_code=400, detail=str(e))
    if len(plan_text) > MAX_PLAN_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"方案太长（{len(plan_text)} 字），请精简到 {MAX_PLAN_CHARS} 字以内",
        )
    return {
        "filename": file.filename,
        "chars": len(plan_text),
        "plan_text": plan_text,
    }


# 提交方案文本：抽要素 + 落库，返回结构化要素表
@review_router.post("/submit")
async def submit_plan(payload: dict = Body(...)):
    plan_text = (payload.get("plan_text") or "").strip()
    if not plan_text:
        raise HTTPException(status_code=400, detail="方案内容不能为空")
    if len(plan_text) > MAX_PLAN_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"方案太长（{len(plan_text)} 字），请精简到 {MAX_PLAN_CHARS} 字以内",
        )

    session_id = (payload.get("session_id") or "").strip() or uuid4().hex
    student_id = (payload.get("student_id") or "").strip()
    plan_title = _guess_title(payload.get("plan_title") or "", plan_text)

    # 抽要素，模型都不可用时 extract_elements 内部会降级，不会抛异常
    elements = extract_elements(plan_text)
    save_session(session_id, plan_title, plan_text, elements, student_id)

    return {
        "session_id": session_id,
        "plan_title": plan_title,
        "plan_elements": elements,
        "elements_text": format_elements(elements),
    }


# 读回一次评审会，用于前端刷新后恢复现场和排查问题
@review_router.get("/session/{session_id}")
async def read_session(session_id: str):
    data = get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"没有找到评审会 {session_id}")
    return data
