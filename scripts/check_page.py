# -*- coding: utf-8 -*-
"""
页面验收（两个页面）
1. 路由是否能跳到页面
2. 页面文件是否能取到，关键标记是否齐全
3. 页面里的 JS 语法（node --check，抓白屏级的低级错误）
4. 模板变量对账：模板里引用的名字，Vue 实例里是否真的定义了
5. 前后端对账：页面 fetch 的后端接口是否真的注册了（防拼写不一致）
6. chat.html 的会话是否真的落到了 sessionStorage（刷新不丢）
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

REPORT = os.path.join(ROOT, "data", "_page_check.txt")
HTML_DIR = os.path.join(ROOT, "app", "html")
REVIEW_PAGE = os.path.join(HTML_DIR, "review.html")
CHAT_PAGE = os.path.join(HTML_DIR, "chat.html")

# ---------- review.html ----------
MARKERS = [
    "AI 交叉质询评审团",
    "⟶ 接",
    "质询",
    "方案要素表",
    "方案缺失项",
]
# 模板变量对账用的名单，要和 review.html 里 Vue 实例的定义保持一致
DATA_KEYS = ["reviewers", "planText", "elements", "sessionId", "utterances",
             "current", "running", "error", "status", "summary",
             "started", "awaiting", "awaitingName", "answerText"]
COMPUTED_KEYS = ["spokenRoles", "statusText"]
METHOD_KEYS = ["listText", "isAbsent", "onFile", "submitPlan", "startMeeting", "handleEvent",
               "submitAnswer", "pump", "verdictText", "restart"]

# ---------- chat.html ----------
# 会话必须落在 sessionStorage，刷新页面才接得上记忆（参考模板就是这么写的）
CHAT_MARKERS = [
    "sessionStorage.setItem('session_id'",
    "sessionStorage.getItem('session_id')",
    "user_id: this.userId, session_id: saved",
    "/create_session",
]

# v-for 里的循环变量
LOOP_VARS = ["r", "i", "u", "m"]
# 模板里合法出现的全局名字
GLOBALS = ["true", "false", "null", "JSON", "String", "Number", "Math", "Array", "Object"]
# JS 关键字，不是变量
KEYWORDS = ["in", "of", "new", "typeof", "return", "if", "else", "function", "var", "let", "const"]


def js_syntax(html, tag):
    """把页面里最后一段 <script> 抠出来交给 node --check 做语法校验（不执行）"""
    script = html.rsplit("<script>", 1)[1].split("</script>")[0]
    js_tmp = os.path.join(ROOT, ".git", f"_page_check_{tag}.js")
    err_tmp = os.path.join(ROOT, ".git", f"_page_check_{tag}.err")
    with open(js_tmp, "w", encoding="utf-8") as f:
        f.write(script)
    # stderr 重定向到文件而不是管道：沙箱不允许子进程走管道
    with open(err_tmp, "w", encoding="utf-8") as errf:
        proc = subprocess.run(["node", "--check", js_tmp], stdout=errf, stderr=errf)
    detail = ""
    if proc.returncode != 0:
        with open(err_tmp, encoding="utf-8") as f:
            detail = f.read().strip()[:500]
    return proc.returncode == 0, detail


def main():
    lines = []

    def log(text=""):
        lines.append(str(text))
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(str(text)[:200].encode("ascii", "replace").decode("ascii"))

    log("=" * 70)
    log("页面验收（review.html + chat.html）")
    log("=" * 70)

    with open(REVIEW_PAGE, encoding="utf-8") as f:
        html = f.read()
    with open(CHAT_PAGE, encoding="utf-8") as f:
        chat_html = f.read()
    called = sorted(set(re.findall(r"fetch\(\s*['\"](/review/[^'\"]*)['\"]", html)))
    chat_called = sorted(set(re.findall(r"fetch\(\s*['\"](/[^'\"]*)['\"]", chat_html)))

    with TestClient(app) as client:
        # ================= review.html =================
        resp = client.get("/review", follow_redirects=False)
        log(f"\n  GET /review                     -> {resp.status_code}")
        log(f"    跳转到                        -> {resp.headers.get('location')}")
        ok_route = resp.status_code in (301, 302, 307, 308) and \
            (resp.headers.get("location") or "").endswith("/static/review.html")

        resp2 = client.get("/static/review.html")
        log(f"\n  GET /static/review.html         -> {resp2.status_code}")
        log(f"    页面大小                      -> {len(resp2.text)} 字节")
        ok_file = resp2.status_code == 200
        if ok_file:
            missing = [m for m in MARKERS if m not in resp2.text]
            log(f"    关键标记齐全                  -> {'是' if not missing else '缺 ' + str(missing)}")
            ok_file = not missing

        # 模板变量对账：模板里引用的名字，Vue 实例里是否真的定义了
        # 手写页面最容易犯的错就是拼错一个名字，那一块直接渲染不出来还不报错
        known = set(DATA_KEYS) | set(COMPUTED_KEYS) | set(METHOD_KEYS) | set(LOOP_VARS) | set(GLOBALS)
        template = html.split("<script>")[0]
        exprs = []
        exprs += re.findall(r'(?:v-[\w:.-]+|[:@][\w.-]+)="([^"]*)"', template)
        exprs += re.findall(r"\{\{(.*?)\}\}", template, re.S)

        used = set()
        for expr in exprs:
            # 先把字符串字面量剔掉，否则 'cross'、's' 这种会被当成变量名
            expr = re.sub(r"'[^']*'|\"[^\"]*\"", " ", expr)
            for name in re.findall(r"[A-Za-z_$][\w$]*", expr):
                idx = expr.find(name)
                if idx > 0 and expr[idx - 1] == ".":
                    continue
                after = expr[idx + len(name):].lstrip()
                if after.startswith(":"):
                    continue
                used.add(name)

        used -= set(KEYWORDS)
        unknown = sorted(n for n in used if n not in known)
        log(f"\n  模板变量对账：")
        log(f"    模板里用了 {len(used)} 个根标识符：{sorted(used)}")
        log(f"    未定义的：{unknown if unknown else '无'}")

        ok_js, js_err = js_syntax(html, "review")
        log(f"\n  JS 语法检查（node --check）：{'通过' if ok_js else '不通过'}")
        if js_err:
            log("    " + js_err)

        spec = client.get("/openapi.json").json()
        paths = set(spec.get("paths", {}).keys())
        log(f"\n  页面调用的接口（共 {len(called)} 个）：")
        bad = []
        for url in called:
            hit = url in paths
            if not hit:
                bad.append(url)
            log(f"    {'✓' if hit else '✗'} {url}")
        log(f"\n  对账结果：{'全部存在' if not bad else '缺失 ' + str(bad)}")

        # ================= chat.html =================
        log("\n" + "-" * 70)
        log("chat.html（会话管理）")
        log("-" * 70)

        resp3 = client.get("/", follow_redirects=False)
        log(f"\n  GET /                           -> {resp3.status_code}")
        log(f"    跳转到                        -> {resp3.headers.get('location')}")
        ok_chat_route = resp3.status_code in (301, 302, 307, 308) and \
            (resp3.headers.get("location") or "").endswith("/static/chat.html")

        resp4 = client.get("/static/chat.html")
        log(f"\n  GET /static/chat.html           -> {resp4.status_code}")
        chat_missing = [m for m in CHAT_MARKERS if m not in resp4.text]
        log(f"    会话落地标记齐全              -> {'是' if not chat_missing else '缺 ' + str(chat_missing)}")
        ok_chat_file = resp4.status_code == 200 and not chat_missing

        ok_chat_js, chat_js_err = js_syntax(chat_html, "chat")
        log(f"\n  JS 语法检查（node --check）：{'通过' if ok_chat_js else '不通过'}")
        if chat_js_err:
            log("    " + chat_js_err)

        log(f"\n  页面调用的接口（共 {len(chat_called)} 个）：")
        chat_bad = []
        for url in chat_called:
            hit = url in paths
            if not hit:
                chat_bad.append(url)
            log(f"    {'✓' if hit else '✗'} {url}")
        log(f"\n  对账结果：{'全部存在' if not chat_bad else '缺失 ' + str(chat_bad)}")

        # 前端带上 session_id 调 /create_session，后端才有机会走"复用/校验"那条分支
        ok_reuse = True
        try:
            s1 = client.post("/create_session", json={"user_id": "page_check"}).json()["data"]
            s2 = client.post("/create_session",
                             json={"user_id": "page_check", "session_id": s1}).json()
            ok_reuse = s2.get("data") == s1
            log(f"\n  /create_session 复用（前端带回 session_id）："
                f"{'通过，同一个会话 ' + s1 if ok_reuse else '不通过，拿到 ' + str(s2)}")
        except Exception as e:  # Redis 没起来之类
            ok_reuse = False
            log(f"\n  /create_session 复用：不通过（{e}）")

    log("\n" + "=" * 70)
    log("页面验收结论")
    log("=" * 70)
    log(f"  /review 跳转          ：{'通过' if ok_route else '不通过'}")
    log(f"  页面可取到且标记齐全  ：{'通过' if ok_file else '不通过'}")
    log(f"  模板变量全部有定义    ：{'通过' if not unknown else '不通过，未定义 ' + str(unknown)}")
    log(f"  前后端接口对账        ：{'通过' if not bad else '不通过，缺失 ' + str(bad)}")
    log(f"  JS 语法（review）     ：{'通过' if ok_js else '不通过'}")
    log(f"  / 跳转 chat.html      ：{'通过' if ok_chat_route else '不通过'}")
    log(f"  会话存 sessionStorage ：{'通过' if ok_chat_file else '不通过'}")
    log(f"  JS 语法（chat）       ：{'通过' if ok_chat_js else '不通过'}")
    log(f"  chat 前后端对账       ：{'通过' if not chat_bad else '不通过，缺失 ' + str(chat_bad)}")
    log(f"  会话复用分支被走到    ：{'通过' if ok_reuse else '不通过'}")


if __name__ == "__main__":
    main()
