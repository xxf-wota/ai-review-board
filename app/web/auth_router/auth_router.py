"""
登录：邮箱 + 验证码

身份只有一个来源 —— 邮箱。登录成功的那一刻就把业务会话建出来（就是模拟面试
那边一直在用的那套 session：存在 Redis、带过期时间、记着属于谁），
前端拿着它进来，后面说的话就都接在同一条记忆上了。

所以"新建会话"这件事从前端进入聊天界面时，挪到了登录时。

验证码放 Redis，两个键：
  auth:code:<邮箱>  ->  {"code": "123456", "tries": 0}   5 分钟过期
  auth:cd:<邮箱>    ->  "1"                              60 秒内不许重复发

发信交给 app.ai.tool.email_tool，真发不出去就报错（用户明确要的：不做兜底）。
"""
import json
import random

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.ai.tool.email_tool import send_mail

auth_router = APIRouter(prefix="/auth", tags=["登录"])

CODE_TTL = 300          # 验证码有效期（秒）
SEND_COOLDOWN = 60      # 同一个邮箱两次发送之间的间隔（秒）
MAX_TRIES = 5           # 一个验证码最多允许试错几次


class EmailSchema(BaseModel):
    email: str = None


class LoginSchema(BaseModel):
    email: str = None
    code: str = None


def _clean_email(email: str) -> str:
    """校验并规范化邮箱，不合法直接 400"""
    try:
        # check_deliverability=False：只做格式校验，不去查 DNS，省一次网络往返
        return validate_email((email or "").strip(), check_deliverability=False).normalized
    except EmailNotValidError as e:
        raise HTTPException(status_code=400, detail=f"邮箱格式不对：{e}")


def _code_key(email: str) -> str:
    return f"auth:code:{email}"


def _cd_key(email: str) -> str:
    return f"auth:cd:{email}"


# 发验证码
@auth_router.post("/send_code")
async def send_code(request: Request, body: EmailSchema):
    email = _clean_email(body.email)
    r = request.app.state.redis
    if r is None:
        raise HTTPException(status_code=503, detail="Redis 没连上，发不了验证码")

    # 冷却：防止有人拿这个接口当免费短信/邮件炮台
    if await r.get(_cd_key(email)):
        left = await r.ttl(_cd_key(email))
        raise HTTPException(status_code=429, detail=f"发得太快了，{max(left, 1)} 秒后再试")

    code = f"{random.randint(0, 999999):06d}"
    try:
        await send_mail(
            email,
            "【AI 答辩陪练】登录验证码",
            f"你的登录验证码是 {code}，{CODE_TTL // 60} 分钟内有效。\n\n"
            f"如果不是你本人在登录，忽略这封邮件就行。",
        )
    except Exception as e:
        print(f"验证码邮件发送失败：{e}")
        raise HTTPException(status_code=502, detail=f"邮件发送失败：{e}")

    # 确认发出去了才落 Redis：发失败就不该占着冷却时间
    await r.set(_code_key(email), json.dumps({"code": code, "tries": 0}), ex=CODE_TTL)
    await r.set(_cd_key(email), "1", ex=SEND_COOLDOWN)
    print(f"验证码已发往 {email}")
    return {"code": 200, "msg": "验证码已发送", "data": {"email": email, "ttl": CODE_TTL}}


# 登录（顺便把会话建出来）
@auth_router.post("/login")
async def login(request: Request, body: LoginSchema):
    email = _clean_email(body.email)
    code = (body.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="请输入验证码")

    r = request.app.state.redis
    if r is None:
        raise HTTPException(status_code=503, detail="Redis 没连上，登不了")

    raw = await r.get(_code_key(email))
    if not raw:
        raise HTTPException(status_code=401, detail="验证码不存在或已过期，请重新获取")

    saved = json.loads(raw)
    if saved["code"] != code:
        saved["tries"] += 1
        if saved["tries"] >= MAX_TRIES:
            await r.delete(_code_key(email))
            raise HTTPException(status_code=401, detail="错误次数太多，验证码作废了，请重新获取")
        # 按剩下的有效期续上，别因为一次试错就重置成 5 分钟
        left = await r.ttl(_code_key(email))
        await r.set(_code_key(email), json.dumps(saved), ex=max(left, 1))
        raise HTTPException(
            status_code=401,
            detail=f"验证码不对，还能试 {MAX_TRIES - saved['tries']} 次",
        )

    # 用掉了就删，同一个验证码不能登两次
    await r.delete(_code_key(email))

    # 登录即建会话。user_id 直接用邮箱：答辩时翻 Redis / Chroma 一眼能看出是谁的
    session_id = await request.app.state.exam_agent.create_session(email)
    print(f"登录成功：{email} -> {session_id}")
    return {
        "code": 200,
        "msg": "登录成功",
        "data": {"email": email, "user_id": email, "session_id": session_id},
    }
