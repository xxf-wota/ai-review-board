# -*- coding: utf-8 -*-
"""
页面验收（现在只有一个页面 app.html，里面装着两个智能体）
1. 两个入口路由是否都能落到页面上（/ -> 模拟面试，/review -> 评审团）
2. 页面文件是否能取到，关键标记是否齐全
3. 页面里的 JS 语法（node --check，抓白屏级的低级错误）
4. 模板变量对账：模板里引用的名字，Vue 实例里是否真的定义了
5. 前后端对账：页面调用的后端接口是否真的注册了（防拼写不一致）
6. 学生气泡靠右的那套 CSS 不变式
7. 旧的两个页面确实删掉了（不是"新页面放着、旧页面还留着"）
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
PAGE = os.path.join(ROOT, "app", "html", "app.html")
# 换新页面之后这两个文件不该还存在
OLD_PAGES = ["/static/chat.html", "/static/review.html"]

# 页面里必须出现的标记
MARKERS = [
    "模拟面试",
    "交叉质询评审团",
    "方案要素表",
    "未答好的问题清单",
    "会议纪要",
    "⟶ 接",
]

# 模板变量对账用的名单，要和 app.html 里 Vue 实例的定义保持一致
DATA_KEYS = ["userId", "mode",
             "chatList", "chatActive", "chatInput", "chatStreaming", "chatSource",
             "reviewList", "reviewActive", "rv",
             "reviewRunning", "reviewError", "reviewStatus", "reviewCurrent",
             "reviewSource", "scrollTimer", "reviewers"]
COMPUTED_KEYS = ["historyList", "activeIndex", "chatMessages", "modeSub", "rvStatusText"]
METHOD_KEYS = ["save", "restore", "switchMode", "newSession", "openHistory",
               "jumpToBottom", "jumpToBottomSoon", "formatMessage", "ensureSession", "sendChat",
               "listText", "isAbsent", "nameOf", "verdictText", "summaryOf", "loadRecord",
               "onFile", "submitPlan", "pump", "startMeeting", "submitAnswer", "handleReviewEvent"]
# 模板里合法出现的全局名字
GLOBALS = ["true", "false", "null", "JSON", "String", "Number", "Math", "Array", "Object"]
# JS 关键字，不是变量
KEYWORDS = ["in", "of", "new", "typeof", "return", "if", "else", "function", "var", "let", "const"]
# v-for 的循环变量不在这里手工维护，直接从模板里抠（手工名单漏一个就误报，踩过两次）


def js_syntax(html, tag):
    """把页面里那段内联 <script> 抠出来交给 node --check 做语法校验（不执行）"""
    # CDN 那两个是 <script src=...>，不匹配字面量 "<script>"，所以 rsplit 拿到的是内联那段
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
    log("页面验收（app.html：模拟面试 + 交叉质询评审团）")
    log("=" * 70)

    with open(PAGE, encoding="utf-8") as f:
        html = f.read()

    # 把页面调用的后端接口抠出来：fetch(...) 和 EventSource(...) 都要算
    called = sorted(set(re.findall(r"""(?:fetch|EventSource)\(\s*['"]([^'"?]+)""", html)))

    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()
        paths = set(spec.get("paths", {}).keys())

        def registered(url):
            """完全一致，或者命中的是带路径参数的那条（/review/session/ -> /review/session/{id}）"""
            if url in paths:
                return True
            return any(p.startswith(url) and p[len(url):].startswith("{") for p in paths)

        # 1) 两个入口都要落到这个页面上
        resp = client.get("/", follow_redirects=False)
        ok_home = resp.status_code in (301, 302, 307, 308) and \
            (resp.headers.get("location") or "").endswith("/static/app.html")
        log(f"\n  GET /          -> {resp.status_code} -> {resp.headers.get('location')}")
        log(f"    落到 app.html                          ：{'通过' if ok_home else '不通过'}")

        resp_r = client.get("/review", follow_redirects=False)
        loc_r = resp_r.headers.get("location") or ""
        ok_review_route = resp_r.status_code in (301, 302, 307, 308) and \
            loc_r.endswith("/static/app.html?tab=review")
        log(f"  GET /review    -> {resp_r.status_code} -> {loc_r}")
        log(f"    带 tab=review 落到同一页               ：{'通过' if ok_review_route else '不通过'}")

        # 2) 页面本体
        resp2 = client.get("/static/app.html")
        log(f"\n  GET /static/app.html -> {resp2.status_code}，{len(resp2.text)} 字节")
        missing = [m for m in MARKERS if m not in resp2.text]
        ok_file = resp2.status_code == 200 and not missing
        log(f"    关键标记齐全                           ：{'是' if not missing else '缺 ' + str(missing)}")

        # 3) 旧页面确实删了
        still = [p for p in OLD_PAGES if client.get(p).status_code == 200]
        log(f"    旧页面已删除（{len(OLD_PAGES)} 个）                  ："
            f"{'通过' if not still else '不通过，还留着 ' + str(still)}")

        # 4) 模板变量对账：模板里引用的名字，Vue 实例里是否真的定义了
        # 手写页面最容易犯的错就是拼错一个名字，那一块直接渲染不出来还不报错
        template = html.split("<script>")[0]

        # v-for 的循环变量从模板里抠：v-for="(h, i) in list" / v-for="m in list" 两种写法都要认
        loop_vars = set()
        for spec in re.findall(r'v-for="([^"]*)"', template):
            hit = re.match(r"\s*\(([^)]*)\)\s+in\s", spec) or \
                re.match(r"\s*([A-Za-z_$][\w$]*)\s+in\s", spec)
            if hit:
                loop_vars.update(n.strip() for n in hit.group(1).split(",") if n.strip())

        known = set(DATA_KEYS) | set(COMPUTED_KEYS) | set(METHOD_KEYS) | set(GLOBALS) | loop_vars
        exprs = []
        exprs += re.findall(r'(?:v-[\w:.-]+|[:@][\w.-]+)="([^"]*)"', template)
        exprs += re.findall(r"\{\{(.*?)\}\}", template, re.S)

        used = set()
        for expr in exprs:
            # 先把字符串字面量剔掉，否则 'cross'、's' 这种会被当成变量名
            expr = re.sub(r"'[^']*'|\"[^\"]*\"", " ", expr)
            # 必须按"每一处出现"判断，不能用 expr.find(name)：
            # 那样 {{ verdictText(m.verdict) }} 里的 verdict 会被定位到 verdictText 里，
            # 于是属性名 verdict 被误判成未定义（踩过）
            for hit in re.finditer(r"[A-Za-z_$][\w$]*", expr):
                name = hit.group(0)
                # 前面带点的说明是属性名（rv.elements 里的 elements）
                if hit.start() > 0 and expr[hit.start() - 1] == ".":
                    continue
                # 后面紧跟冒号的说明是对象字面量的键（{ on: ... } 里的 on）
                if expr[hit.end():].lstrip().startswith(":"):
                    continue
                used.add(name)

        used -= set(KEYWORDS)
        unknown = sorted(n for n in used if n not in known)
        log(f"\n  模板变量对账：")
        log(f"    v-for 循环变量（自动抠出）：{sorted(loop_vars)}")
        log(f"    模板里用了 {len(used)} 个根标识符：{sorted(used)}")
        log(f"    未定义的：{unknown if unknown else '无'}")

        # 5) 学生气泡必须真的靠右。这三条是一条完整的不变式，缺一条就会歪：
        # 只写 row-reverse 只挪头像，.body 还占满整行、.bubble 是 inline-block 会贴左边
        # （踩过这个 bug：气泡留在左边，头像一个在右边，看着很怪）
        css = html.split("</style>")[0]
        ok_align = (
            bool(re.search(r"\.msg\.student\s*\{[^}]*row-reverse", css))
            and bool(re.search(r"\.msg\.student\s+\.body\s*\{[^}]*text-align:\s*right", css))
            # text-align 会继承，气泡里的文字必须显式改回左对齐
            and bool(re.search(r"\.msg\.student\s+\.bubble\s*\{[^}]*text-align:\s*left", css))
        )
        log(f"\n  学生气泡靠右（row-reverse + body 右对齐 + 气泡内文字左对齐）："
            f"{'通过' if ok_align else '不通过'}")

        # 6) JS 语法
        ok_js, js_err = js_syntax(html, "app")
        log(f"\n  JS 语法检查（node --check）：{'通过' if ok_js else '不通过'}")
        if js_err:
            log("    " + js_err)

        # 7) 前后端对账
        log(f"\n  页面调用的接口（共 {len(called)} 个）：")
        bad = []
        for url in called:
            hit = registered(url)
            if not hit:
                bad.append(url)
            log(f"    {'✓' if hit else '✗'} {url}")
        log(f"\n  后端已注册的接口：")
        for p in sorted(paths):
            log(f"    {p}")
        log(f"\n  对账结果：{'全部存在' if not bad else '缺失 ' + str(bad)}")

        # 8) 面试会话的复用分支：页面把本地存的 session_id 带回来，后端要走校验那条路
        ok_reuse = True
        try:
            s1 = client.post("/create_session", json={"user_id": "page_check"}).json()["data"]
            s2 = client.post("/create_session",
                             json={"user_id": "page_check", "session_id": s1}).json()
            ok_reuse = s2.get("data") == s1
            log(f"\n  /create_session 复用（页面带回 session_id）："
                f"{'通过，同一个会话 ' + s1 if ok_reuse else '不通过，拿到 ' + str(s2)}")
        except Exception as e:  # Redis 没起来之类
            ok_reuse = False
            log(f"\n  /create_session 复用：不通过（{e}）")

    log("\n" + "=" * 70)
    log("页面验收结论")
    log("=" * 70)
    checks = [
        ("/ 落到 app.html      ", ok_home),
        ("/review 带 tab 落到  ", ok_review_route),
        ("页面可取到且标记齐全  ", ok_file),
        ("旧页面已删除          ", not still),
        ("模板变量全部有定义    ", not unknown),
        ("学生气泡靠右          ", ok_align),
        ("JS 语法               ", ok_js),
        ("前后端接口对账        ", not bad),
        ("会话复用分支被走到    ", ok_reuse),
    ]
    for title, ok in checks:
        log(f"  {title}：{'通过' if ok else '不通过'}")
    if unknown:
        log(f"    （未定义：{unknown}）")
    if bad:
        log(f"    （缺失接口：{bad}）")
    if still:
        log(f"    （旧页面还在：{still}）")

    # 用退出码兜住结论：只看 exit code 的场合不能永远返回 0
    return all(ok for _, ok in checks)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
