import json
from uuid import uuid4

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse

from app.ai.agent.review_agent.node.extract_node import extract_elements, format_elements
from app.ai.tool.plan_parser import parse_plan_bytes
from app.ai.tool.review_dao import get_session, save_session

"""
评审会接口
收方案（文件或文本）、抽要素表并落库、把评审会过程用 SSE 推到前端
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


# 开一场评审会，用 SSE 把过程实时推到前端
# 用 POST 而不是 EventSource：方案正文可能上万字，塞不进 URL
# 前端要用 fetch + ReadableStream 来读
@review_router.post("/meeting/stream")
async def meeting_stream(request: Request, payload: dict = Body(...)):
    session_id = (payload.get("session_id") or "").strip() or uuid4().hex
    plan_text = (payload.get("plan_text") or "").strip()
    max_round = int(payload.get("max_round") or 1)

    # 方案正文和要素表都优先取库里的：提交阶段已经抽过要素，这里就不重复抽了
    elements = payload.get("plan_elements") or {}
    if not plan_text or not elements:
        row = get_session(session_id)
        if row:
            plan_text = plan_text or (row.get("plan_text") or "")
            elements = elements or (row.get("plan_elements") or {})

    if not plan_text:
        raise HTTPException(status_code=400, detail="没有找到方案正文，请先提交方案")

    graph = request.app.state.review_agent
    if graph is None:
        raise HTTPException(status_code=503, detail="评审会智能体还没就绪，请稍后再试")

    async def generate():
        try:
            async for event in graph.run(
                plan_text, session_id, max_round=max_round, plan_elements=elements
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            # 会议中途出错也要把错误推给前端，否则界面会一直转圈
            print(f"-----------评审会异常：{e}------------")
            err = {"event": "error", "message": str(e)[:200]}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        # 结束标记，事件源协议里前端靠它关连接
        yield f"data: {json.dumps({'event': 'done'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            # 关掉反向代理的缓冲，否则事件会被攒着一起发，看不到实时效果
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# 评审会页面。放在自己的路由里，就不用再去改默认页面路由那个文件
@review_router.get("")
def review_page():
    return RedirectResponse(url="/static/review.html")
