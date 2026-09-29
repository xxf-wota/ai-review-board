# -*- coding: utf-8 -*-
"""
启动验收（真起服务，不是 TestClient）
这一层专门抓 TestClient 抓不到的坑：事件循环、uvicorn 参数、静态目录、端口。

为什么要单独验：
  uvicorn 从 0.36 起不再读 asyncio 的事件循环策略，而是自己挑 loop_factory，
  Windows 上默认给 ProactorEventLoop，psycopg 的异步模式用不了它。
  TestClient 走的是 anyio 自己的 loop，完全绕过这条路径，所以怎么测都是绿的，
  但 `python -m app.main` 一跑就崩。这里就是补这一刀。

流程：
  1. 8000 端口必须是空的（否则验的不是本次启动）
  2. 子进程起 `python -m app.main`
  3. 轮询直到服务可用，检查启动日志里没有 Traceback
  4. HTTP 走一遍：跳转 / 页面 / 会话新建 / 会话复用 / 别人拿去用被拒 / 登录
  5. 收尾，把子进程干掉

登录这一层特意绕过发信：验证码直接写进 Redis 再调 /auth/login
（真发一封邮件只为了跑一次检查，不合适；发信本身由人在页面上验）
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import redis as redis_sync

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "data", "_startup_check.txt")
LOG = os.path.join(ROOT, ".git", "_startup_server.log")
BASE = "http://127.0.0.1:8000"
PORT = 8000
TIMEOUT = 60  # 秒。PostgreSQL 检查点首次 setup 建表会慢一点

# 登录用的假邮箱：格式合法就行，登录检查不会真的发信
CHECK_EMAIL = "startup_check@example.com"

# 页面里必须出现的东西：两个智能体在同一个页面上，历史存在 localStorage，刷新才接得回去
PAGE_MARKERS = [
    "模拟面试",
    "交叉质询评审团",
    "exam_agent.ui.v1",
    "/create_session",
]

# 登录页必须有的东西
LOGIN_MARKERS = [
    "exam_agent.auth.v1",
    "/auth/send_code",
    "/auth/login",
]


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.status, r.geturl(), r.read().decode("utf-8", "replace")


def http_post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post_raw(path, payload):
    """POST，返回 (状态码, json)。4xx/5xx 也要把内容拿到，不能让它直接抛"""
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"detail": body}


def main():
    lines = []

    def log(text=""):
        lines.append(str(text))
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(str(text)[:200].encode("ascii", "replace").decode("ascii"))

    log("=" * 70)
    log("启动验收（python -m app.main）")
    log("=" * 70)

    if port_open(PORT):
        log(f"\n  ✗ {PORT} 端口已被占用。请先关掉正在跑的服务再看这个脚本，")
        log("    否则验到的是别人起的进程，本次启动根本没被检查。")
        return 1

    # 子进程的 stdio 全部落文件：沙箱不允许开管道
    logf = open(LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=logf,
        stderr=logf,
    )
    log(f"\n  已拉起子进程 pid={proc.pid}，最多等 {TIMEOUT} 秒")

    up = False
    boot_err = ""
    try:
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                boot_err = f"进程提前退出，exit={proc.returncode}"
                break
            try:
                urllib.request.urlopen(BASE + "/openapi.json", timeout=2).read()
                up = True
                break
            except Exception:
                time.sleep(0.5)

        log(f"\n  服务可用                      -> {'是' if up else '否'}")
        if not up:
            log(f"    {boot_err or '超时'}")

        # 启动日志：崩没崩一眼就能看出来
        logf.flush()
        with open(LOG, encoding="utf-8", errors="replace") as f:
            serverlog = f.read()
        ok_log = "Traceback" not in serverlog
        log(f"  启动日志无 Traceback          -> {'是' if ok_log else '否'}")
        if not ok_log:
            idx = serverlog.find("Traceback")
            log("    " + serverlog[idx:idx + 600].replace("\n", "\n    "))
        if "ProactorEventLoop" in serverlog:
            log("    ⚠ 日志里出现 ProactorEventLoop，说明 loop 又没指定对")

        ok_route = ok_login_route = ok_review_route = ok_page = ok_login_page = False
        ok_reuse = ok_deny = False
        ok_bad_email = ok_wrong_code = ok_login = ok_login_session = ok_code_once = False
        if up:
            # 1) 三个入口：/ 和 /review 都先落到登录页（登录页看本地状态决定放不放行）
            status, url, _ = http_get("/")
            ok_route = status == 200 and url.endswith("/static/login.html")
            log(f"\n  GET /                         -> {status} -> {url}")

            status_l, url_l, _ = http_get("/login")
            ok_login_route = status_l == 200 and url_l.endswith("/static/login.html")
            log(f"  GET /login                    -> {status_l} -> {url_l}")

            status_r, url_r, _ = http_get("/review")
            ok_review_route = status_r == 200 and url_r.endswith("/static/login.html?tab=review")
            log(f"  GET /review                   -> {status_r} -> {url_r}")

            # 2) 两个页面都取得到，该有的标记都在
            status, _, body = http_get("/static/app.html")
            missing = [m for m in PAGE_MARKERS if m not in body]
            ok_page = status == 200 and not missing
            log(f"  GET /static/app.html          -> {status}，{len(body)} 字节")
            log(f"    页面标记齐全                -> {'是' if not missing else '缺 ' + str(missing)}")

            status_p, _, lbody = http_get("/static/login.html")
            lmissing = [m for m in LOGIN_MARKERS if m not in lbody]
            ok_login_page = status_p == 200 and not lmissing
            log(f"  GET /static/login.html        -> {status_p}，{len(lbody)} 字节")
            log(f"    登录页标记齐全              -> {'是' if not lmissing else '缺 ' + str(lmissing)}")

            # 3) 会话：新建 -> 页面把本地存的带回来 -> 复用
            r1 = http_post("/create_session", {"user_id": "startup_check", "session_id": ""})
            sid = r1["data"]
            r2 = http_post("/create_session", {"user_id": "startup_check", "session_id": sid})
            ok_reuse = r2.get("data") == sid
            log(f"\n  新建会话                      -> {r1['msg']} / {sid}")
            log(f"  带上本地会话再来一次          -> {r2['msg']} / {r2['data']}")
            log(f"  复用了同一个会话              -> {'是' if ok_reuse else '否'}")

            # 4) 别人的会话该被拒
            r3 = http_post("/create_session", {"user_id": "someone_else", "session_id": sid})
            ok_deny = r3.get("data") != sid
            log(f"  换个用户拿去用                -> {r3['msg']} / {r3['data']}")
            log(f"  被拒并换了新会话              -> {'是' if ok_deny else '否'}")

            # 5) 登录。验证码直接写进 Redis，绕开发信（检查不该往人邮箱里发东西）
            st_bad, body_bad = http_post_raw("/auth/send_code", {"email": "not-an-email"})
            ok_bad_email = st_bad == 400
            log(f"\n  /auth/send_code 邮箱乱填      -> {st_bad} {body_bad.get('detail')}")
            log(f"    邮箱格式校验生效            -> {'是' if ok_bad_email else '否'}")

            rc = redis_sync.Redis(host="localhost", port=6379, db=0, decode_responses=True)
            rc.set(f"auth:code:{CHECK_EMAIL}",
                   json.dumps({"code": "246810", "tries": 0}), ex=300)

            st_wrong, body_wrong = http_post_raw(
                "/auth/login", {"email": CHECK_EMAIL, "code": "000000"})
            ok_wrong_code = st_wrong == 401
            log(f"  验证码填错                    -> {st_wrong} {body_wrong.get('detail')}")

            st_ok, body_ok = http_post_raw(
                "/auth/login", {"email": CHECK_EMAIL, "code": "246810"})
            ldata = body_ok.get("data") or {}
            login_sid = ldata.get("session_id") or ""
            ok_login = (st_ok == 200 and bool(login_sid)
                        and ldata.get("user_id") == CHECK_EMAIL)
            log(f"  验证码填对                    -> {st_ok} {body_ok.get('msg')}")
            log(f"    登录时就把会话建出来了      -> {login_sid or '（没给）'}")

            # 登录给的这个会话必须真能用 —— 它就是"登录"和"记忆"之间的那根线
            if login_sid:
                r4 = http_post("/create_session",
                               {"user_id": CHECK_EMAIL, "session_id": login_sid})
                ok_login_session = r4.get("data") == login_sid
            log(f"    拿它回 /create_session 复用 -> {'是' if ok_login_session else '否'}")

            # 验证码用过一次就得作废
            st_reuse, _ = http_post_raw(
                "/auth/login", {"email": CHECK_EMAIL, "code": "246810"})
            ok_code_once = st_reuse == 401
            log(f"    同一个验证码不能再登一次    -> {'是' if ok_code_once else '否'}")
            rc.delete(f"auth:code:{CHECK_EMAIL}")

    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        logf.close()
        log(f"\n  子进程已收掉                  -> exit={proc.returncode}")

    log("\n" + "=" * 70)
    log("启动验收结论")
    log("=" * 70)
    checks = [
        ("服务能起来（= loop 指定对了）", up and ok_log),
        ("/ 落到登录页                  ", ok_route),
        ("/login 落到登录页             ", ok_login_route),
        ("/review 带 tab 落到登录页     ", ok_review_route),
        ("登录页可取且标记齐全          ", ok_login_page),
        ("主页面可取且标记齐全          ", ok_page),
        ("会话复用                      ", ok_reuse),
        ("会话归属校验                  ", ok_deny),
        ("邮箱格式校验                  ", ok_bad_email),
        ("验证码错误被拒                ", ok_wrong_code),
        ("登录成功（并顺手建了会话）    ", ok_login),
        ("登录给的会话能接着用          ", ok_login_session),
        ("验证码一次性                  ", ok_code_once),
    ]
    for title, ok in checks:
        log(f"  {title}：{'通过' if ok else '不通过'}")

    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
