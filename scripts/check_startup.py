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
  4. HTTP 走一遍：跳转 / 页面 / 会话新建 / 会话复用 / 别人拿去用被拒
  5. 收尾，把子进程干掉
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "data", "_startup_check.txt")
LOG = os.path.join(ROOT, ".git", "_startup_server.log")
BASE = "http://127.0.0.1:8000"
PORT = 8000
TIMEOUT = 60  # 秒。PostgreSQL 检查点首次 setup 建表会慢一点

# 页面里必须出现的东西：两个智能体在同一个页面上，历史存在 localStorage，刷新才接得回去
PAGE_MARKERS = [
    "模拟面试",
    "交叉质询评审团",
    "exam_agent.ui.v1",
    "/create_session",
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

        ok_route = ok_review_route = ok_page = ok_reuse = ok_deny = False
        if up:
            # 1) 两个入口：/ 落到模拟面试，/review 带 tab 落到评审团，都在同一个页面上
            status, url, _ = http_get("/")
            ok_route = status == 200 and url.endswith("/static/app.html")
            log(f"\n  GET /                         -> {status} -> {url}")

            status_r, url_r, _ = http_get("/review")
            ok_review_route = status_r == 200 and url_r.endswith("/static/app.html?tab=review")
            log(f"  GET /review                   -> {status_r} -> {url_r}")

            # 2) 页面可取
            status, _, body = http_get("/static/app.html")
            missing = [m for m in PAGE_MARKERS if m not in body]
            ok_page = status == 200 and not missing
            log(f"  GET /static/app.html          -> {status}，{len(body)} 字节")
            log(f"    页面标记齐全                -> {'是' if not missing else '缺 ' + str(missing)}")

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
        ("/ 落到 app.html              ", ok_route),
        ("/review 带 tab 落到同一页    ", ok_review_route),
        ("页面可取且标记齐全            ", ok_page),
        ("会话复用                      ", ok_reuse),
        ("会话归属校验                  ", ok_deny),
    ]
    for title, ok in checks:
        log(f"  {title}：{'通过' if ok else '不通过'}")

    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
