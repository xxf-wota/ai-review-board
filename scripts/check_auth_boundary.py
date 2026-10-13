"""
评审会接口的"身份边界"自检（离线，不调大模型）

验的是一条规矩：**接口不接收前端自报的账号**，只接收登录时拿到的 `login_session_id`，
服务端拿它去 Redis 反查 `user_id`（`ExamGraphAgent.session_owner()`）——
会话不存在／已过期／Redis 不可用，一律 401。归属校验则统一走 `app/web/identity.py`。

十五项：
1. `/review/sessions` 不带凭据 → 401
2. `/review/sessions` 假凭据 → 401
3. `/review/sessions` 真凭据 → 200，且账号由服务端反查（不是前端传什么就是什么）
4. `limit=0` / `limit=99999` 都不崩（夹在 1–200）
5. `/review/submit` 没凭据 → 401，**且不落库**（身份不过关不能留下半条数据）
6. `/review/submit` 在 body 里冒名别人的 `student_id` → 不被采信，库里写的是登录账号
7. 刚提交的这场会，确实出现在自己的历史里
8. `GET /review/session/{id}` 不带凭据 → 401
9. `GET /review/session/{id}` 读别人开的会 → 403（内容里有方案正文和学生的原话）
10. `GET /review/session/{id}` 读自己开的会 → 200
11. `POST /review/meeting/start` 开别人的会 → 403
12. `POST /review/meeting/answer` 答别人的会 → 403
13. `DELETE /session/{id}` 删别人的会 → 403，且那行还在（评审会不进 Redis，
    光靠面试那半边的 `check_session` 看不到归属，所以这条单拎出来验）
14. `DELETE /session/{id}` 不带凭据 → 401，且那行还在
15. `DELETE /session/{id}` 归属者自己删 → 200，且那行真没了

三处刻意做成"不依赖外部状态"：
- 登录会话直接用 Redis 写好（键名与 `/auth/login` 成功后那套一致：`session:<hex>` → `{user_id, create_time}`），
  这样不必真的走一遍发信收码
- 抽要素打桩成空字典（`format_elements` 对缺项本来就有兜底），所以不碰模型、秒级跑完；
  本脚本验的是身份，不是抽取质量
- 全程不碰 PostgreSQL 检查点：`/meeting/*` 的用例都停在归属校验这一步，
  走不到图里（那张表交给 check_startup.py 去验）

跑完会把造的数据清掉（评审会两张表 + Redis 里的两条会话）
"""
import asyncio
import json
import os
import sys
import time
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis.asyncio as aioredis
from fastapi.testclient import TestClient

from app.ai.tool import review_dao
from app.main import app
from app.web.review_router import review_router as review_router_module

# 登录账号：用配置里的发信邮箱不行（那是机器人），这里随便造一个
EMAIL = "boundary-check@example.com"
OTHER = "someone-else@example.com"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PROBE_OK = "authcheck-ok"      # 这一场会被真写进库，既当"自己的会"也当"别人的会"
PROBE_401 = "authcheck-401"    # 这一场只该收到 401，库里不该有它

fails = []


def log(msg: str) -> None:
    print(msg)


def check(name, ok, detail=""):
    log(f"  {'通过' if ok else '不通过'}  {name}{('  —— ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


async def _close(client):
    # redis-py 新旧版本一个叫 aclose 一个叫 close，都试一下
    for attr in ("aclose", "close"):
        fn = getattr(client, attr, None)
        if fn is None:
            continue
        ret = fn()
        if asyncio.iscoroutine(ret):
            await ret
        return


async def seed_login_session(session_id: str, user_id: str) -> None:
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await client.set(
        session_id,
        json.dumps({"user_id": user_id, "create_time": time.time()}),
        ex=3600,
    )
    await _close(client)


async def drop_login_session(session_id: str) -> None:
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await client.delete(session_id)
    await _close(client)


def fake_extract(plan_text: str) -> dict:
    """打桩：要素表给个空字典就够 —— 这里不验抽取，只验身份"""
    return {}


def main() -> int:
    login_session_id = f"session:{uuid4().hex}"
    other_login_session_id = f"session:{uuid4().hex}"
    asyncio.run(seed_login_session(login_session_id, EMAIL))
    asyncio.run(seed_login_session(other_login_session_id, OTHER))

    # 把 router 命名空间里的 extract_elements 换掉（它是在 import 时绑定进去的）
    review_router_module.extract_elements = fake_extract

    log("评审会接口身份边界自检")
    log(f"  登录会话：{login_session_id[:18]}…（属于 {EMAIL}）")
    log(f"  另一个账号的会话：{other_login_session_id[:18]}…（属于 {OTHER}）")
    log("")

    try:
        with TestClient(app) as client:
            r = client.get("/review/sessions")
            check("GET /review/sessions 不带凭据 → 401", r.status_code == 401, str(r.status_code))

            r = client.get("/review/sessions", params={"login_session_id": "session:deadbeef"})
            check("GET /review/sessions 假凭据 → 401", r.status_code == 401, str(r.status_code))

            r = client.get("/review/sessions", params={"login_session_id": login_session_id})
            body = r.json() if r.status_code == 200 else {}
            check(
                "GET /review/sessions 真凭据 → 200 且账号由服务端反查",
                r.status_code == 200 and body.get("student_id") == EMAIL,
                f"{r.status_code} student_id={body.get('student_id')!r}",
            )

            r0 = client.get("/review/sessions", params={"login_session_id": login_session_id, "limit": 0})
            r9 = client.get("/review/sessions", params={"login_session_id": login_session_id, "limit": 99999})
            check(
                "limit=0 / 99999 都不崩（夹到 1–200）",
                r0.status_code == 200 and r9.status_code == 200,
                f"{r0.status_code}/{r9.status_code}",
            )

            r = client.post("/review/submit", json={"session_id": PROBE_401, "plan_text": "项目名称：自检用方案"})
            row = review_dao.get_session(PROBE_401)
            check(
                "POST /review/submit 没凭据 → 401 且不落库",
                r.status_code == 401 and row is None,
                f"{r.status_code} 库里有这行={row is not None}",
            )

            r = client.post(
                "/review/submit",
                json={
                    "session_id": PROBE_OK,
                    "plan_text": "项目名称：自检用方案\n目标用户：本校学生",
                    "login_session_id": login_session_id,
                    "student_id": OTHER,   # 冒名：必须被忽略
                },
            )
            row = review_dao.get_session(PROBE_OK) or {}
            check(
                "POST /review/submit 冒名 student_id 不被采信",
                r.status_code == 200 and row.get("student_id") == EMAIL,
                f"{r.status_code} 库里 student_id={row.get('student_id')!r}",
            )

            r = client.get("/review/sessions", params={"login_session_id": login_session_id})
            ids = [x["session_id"] for x in ((r.json() if r.status_code == 200 else {}).get("sessions") or [])]
            check("刚提交的会出现在自己的历史里", PROBE_OK in ids, f"count={len(ids)}")

            # ---- 读回一场会：不带凭据 401 / 别人的 403 / 自己的 200 ----
            r = client.get(f"/review/session/{PROBE_OK}")
            check("GET /review/session/{id} 不带凭据 → 401", r.status_code == 401, str(r.status_code))

            r = client.get(
                f"/review/session/{PROBE_OK}",
                params={"login_session_id": other_login_session_id},
            )
            check(
                "GET /review/session/{id} 读别人的会 → 403",
                r.status_code == 403,
                f"{r.status_code} {r.json().get('detail') if r.status_code == 403 else ''}",
            )

            r = client.get(
                f"/review/session/{PROBE_OK}",
                params={"login_session_id": login_session_id},
            )
            ok_read = r.status_code == 200 and (r.json() or {}).get("session_id") == PROBE_OK
            check("GET /review/session/{id} 读自己的会 → 200", ok_read, str(r.status_code))

            # ---- 开会 / 答题：别人的会一律 403（校验在归属这一步就停住，走不到图里）----
            r = client.post(
                "/review/meeting/start",
                json={"session_id": PROBE_OK, "login_session_id": other_login_session_id},
            )
            check("POST /review/meeting/start 开别人的会 → 403", r.status_code == 403, str(r.status_code))

            r = client.post(
                "/review/meeting/answer",
                json={"session_id": PROBE_OK, "answer": "随口答一句", "login_session_id": other_login_session_id},
            )
            check("POST /review/meeting/answer 答别人的会 → 403", r.status_code == 403, str(r.status_code))

            # ---- 删会话：这一场的归属只写在 MySQL 里（进不了 Redis 的面试那半边）----
            r = client.delete(
                f"/session/{PROBE_OK}",
                params={"login_session_id": other_login_session_id},
            )
            check(
                "DELETE /session/{id} 删别人的会 → 403 且那行还在",
                r.status_code == 403 and review_dao.get_session(PROBE_OK) is not None,
                f"{r.status_code} 库里有这行={review_dao.get_session(PROBE_OK) is not None}",
            )

            r = client.delete(f"/session/{PROBE_OK}")
            check(
                "DELETE /session/{id} 不带凭据 → 401 且那行还在",
                r.status_code == 401 and review_dao.get_session(PROBE_OK) is not None,
                f"{r.status_code}",
            )

            r = client.delete(
                f"/session/{PROBE_OK}",
                params={"login_session_id": login_session_id},
            )
            check(
                "DELETE /session/{id} 归属者自己删 → 200 且那行没了",
                r.status_code == 200 and review_dao.get_session(PROBE_OK) is None,
                f"{r.status_code}",
            )
    finally:
        # 收尾：探针造的数据全清掉（评审会两张表 + Redis 里的两条会话）
        review_dao.delete_session(PROBE_OK)
        review_dao.delete_session(PROBE_401)
        asyncio.run(drop_login_session(login_session_id))
        asyncio.run(drop_login_session(other_login_session_id))

    log("")
    if fails:
        log(f"{len(fails)} 项不通过：" + "、".join(fails))
        return 1
    log("全部通过（15/15）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
