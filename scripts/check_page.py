# -*- coding: utf-8 -*-
"""
页面验收（两个页面：login.html 登录页 + app.html 装着两个智能体）
1. 三个入口路由是否都先落到登录页（/ 与 /login，/review 带 tab）
2. 两个页面文件是否能取到，关键标记是否齐全
3. 页面里的 JS 语法（node --check，抓白屏级的低级错误）
4. 模板变量对账：模板里引用的名字，Vue 实例里是否真的定义了
5. 前后端对账：页面调用的后端接口是否真的注册了（防拼写不一致）
6. 学生气泡靠右的那套 CSS 不变式
7. 登录是贯通的：登录页把会话存下来 -> 主页面没登录会跳回来 -> 主页面用登录给的会话
8. 旧的两个页面确实删掉了（不是"新页面放着、旧页面还留着"）
"""
import json
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
LOGIN_PAGE = os.path.join(ROOT, "app", "html", "login.html")
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
# 登录页的标记：登录态存档键 + 它调的两个接口 + 按钮文案
LOGIN_MARKERS = [
    "exam_agent.auth.v1",
    "/auth/send_code",
    "/auth/login",
    "获取验证码",
]

# 模板变量对账用的名单，要和 app.html 里 Vue 实例的定义保持一致
DATA_KEYS = ["userId", "mode", "auth",
             "chatList", "chatActive", "chatInput", "chatStreaming", "chatSource",
             "reviewList", "reviewActive", "rv",
             "reviewRunning", "reviewError", "reviewStatus", "reviewCurrent",
             "reviewSource", "scrollTimer", "reviewers"]
COMPUTED_KEYS = ["historyList", "activeIndex", "chatMessages", "modeSub", "rvStatusText"]
METHOD_KEYS = ["loadAuth", "adoptLoginSession", "logout",
               "save", "restore", "switchMode", "newSession", "openHistory",
               "removeSession", "dropHistory",
               "jumpToBottom", "jumpToBottomSoon", "formatMessage", "ensureSession", "sendChat",
               "listText", "isAbsent", "nameOf", "verdictText", "summaryOf", "loadRecord",
               "onFile", "submitPlan", "pump", "startMeeting", "submitAnswer", "handleReviewEvent"]
# login.html 的那一套，同样要和它自己的 Vue 实例对得上
LOGIN_DATA_KEYS = ["email", "code", "countdown", "sending", "loading", "err", "tip", "timer"]
LOGIN_METHOD_KEYS = ["enter", "post", "sendCode", "startCountdown", "login"]
# 模板里合法出现的全局名字
GLOBALS = ["true", "false", "null", "JSON", "String", "Number", "Math", "Array", "Object"]
# JS 关键字，不是变量
KEYWORDS = ["in", "of", "new", "typeof", "return", "if", "else", "function", "var", "let", "const"]
# v-for 的循环变量不在这里手工维护，直接从模板里抠（手工名单漏一个就误报，踩过两次）


def reconcile(template, known):
    """模板变量对账：把模板里用到的根标识符抠出来，看哪些不在 known 里

    返回 (用到的标识符集合, v-for 循环变量集合, 未定义的名单)
    手写页面最容易犯的错就是拼错一个名字，那一块直接渲染不出来还不报错
    """
    # v-for 的循环变量从模板里抠：v-for="(h, i) in list" / v-for="m in list" 两种写法都要认
    loop_vars = set()
    for spec in re.findall(r'v-for="([^"]*)"', template):
        hit = re.match(r"\s*\(([^)]*)\)\s+in\s", spec) or \
            re.match(r"\s*([A-Za-z_$][\w$]*)\s+in\s", spec)
        if hit:
            loop_vars.update(n.strip() for n in hit.group(1).split(",") if n.strip())

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
    # 循环变量是模板自己声明的，当然算已知
    known = set(known) | loop_vars
    return used, loop_vars, sorted(n for n in used if n not in known)


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


def fence_fallback(html):
    """把页面里真正的 stripFence 抠出来，用 node 跑用例。

    模型有时候会把整篇回答套进 ```markdown 里（实测存进 Redis 的原文就是这样），
    marked 会把整篇当代码块渲染 —— 标题、加粗全不生效。这个兜底要是没了，
    页面不会报错、只会默默变丑，所以必须拿真函数跑一遍，而不是看一眼代码里有没有这几个字。
    """
    start = html.find("stripFence: function")
    if start < 0:
        return False, "页面里找不到 stripFence"
    end = html.find("\n            },", start)
    if end < 0:
        return False, "stripFence 找不到结尾"
    fn = html[start:end + len("\n            },")]
    if "this.stripFence(content)" not in html:
        return False, "formatMessage 没有调用 stripFence（兜底等于没接上）"

    cases = [
        {"name": "带 markdown 标记的整篇（在正文中间）",
         "input": "答案已经记录\n```markdown\n# 面试评价报告\n\n**加粗**\n```",
         "expect": "# 面试评价报告", "no_fence": True},
        {"name": "带 markdown 标记但还没闭合（流式吐到一半）",
         "input": "答案已经记录\n```markdown\n# 面试评价报告\n\n**加粗**",
         "expect": "# 面试评价报告", "no_fence": True},
        {"name": "没写语言的整篇栅栏，里面是 Markdown",
         "input": "```\n# 报告\n- 一\n- 二\n```",
         "expect": "# 报告", "no_fence": True},
        {"name": "真正的代码块要原样留着",
         "input": "看这段：\n```python\nprint('hi')\n```",
         "expect": "```python", "no_fence": False},
        {"name": "普通 Markdown 不动",
         "input": "## 标题\n正文 **加粗**",
         "expect": "## 标题", "no_fence": False},
    ]
    harness = (
        "var obj = {" + fn + "};\n"
        "var cases = " + json.dumps(cases, ensure_ascii=False) + ";\n"
        "var bad = [];\n"
        "cases.forEach(function (c) {\n"
        "    var out = obj.stripFence(c.input);\n"
        "    if (out.indexOf(c.expect) === -1) bad.push(c.name + '：期望出现 ' "
        "+ JSON.stringify(c.expect) + '，实际 ' + JSON.stringify(out.slice(0, 160)));\n"
        "    if (c.no_fence && out.indexOf('```') !== -1) "
        "bad.push(c.name + '：栅栏还留着 ' + JSON.stringify(out.slice(0, 160)));\n"
        "});\n"
        "if (bad.length) { console.log(bad.join('\\n')); process.exit(1); }\n"
    )
    js_tmp = os.path.join(ROOT, ".git", "_page_check_fence.js")
    err_tmp = os.path.join(ROOT, ".git", "_page_check_fence.out")
    with open(js_tmp, "w", encoding="utf-8") as f:
        f.write(harness)
    # 和 js_syntax 一样：stderr 重定向到文件，沙箱不允许子进程走管道
    with open(err_tmp, "w", encoding="utf-8") as errf:
        proc = subprocess.run(["node", js_tmp], stdout=errf, stderr=errf)
    if proc.returncode == 0:
        return True, ""
    with open(err_tmp, encoding="utf-8") as f:
        return False, f.read().strip()[:600]


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
    with open(LOGIN_PAGE, encoding="utf-8") as f:
        lhtml = f.read()

    # 把两个页面调用的后端接口抠出来：fetch(...) 和 EventSource(...) 都要算
    called = sorted(set(re.findall(r"""(?:fetch|EventSource)\(\s*['"]([^'"?]+)""", html + lhtml)))

    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()
        paths = set(spec.get("paths", {}).keys())

        def registered(url):
            """完全一致，或者命中的是带路径参数的那条（/review/session/ -> /review/session/{id}）"""
            if url in paths:
                return True
            return any(p.startswith(url) and p[len(url):].startswith("{") for p in paths)

        # 1) 三个入口都要先落到登录页（登录页自己看本地有没有登录状态决定放不放行）
        def redirects_to(resp, tail):
            return resp.status_code in (301, 302, 307, 308) and \
                (resp.headers.get("location") or "").endswith(tail)

        resp = client.get("/", follow_redirects=False)
        ok_home = redirects_to(resp, "/static/login.html")
        log(f"\n  GET /          -> {resp.status_code} -> {resp.headers.get('location')}")
        log(f"    落到登录页                             ：{'通过' if ok_home else '不通过'}")

        resp_l = client.get("/login", follow_redirects=False)
        ok_login_route = redirects_to(resp_l, "/static/login.html")
        log(f"  GET /login     -> {resp_l.status_code} -> {resp_l.headers.get('location')}")
        log(f"    落到登录页                             ：{'通过' if ok_login_route else '不通过'}")

        resp_r = client.get("/review", follow_redirects=False)
        loc_r = resp_r.headers.get("location") or ""
        ok_review_route = redirects_to(resp_r, "/static/login.html?tab=review")
        log(f"  GET /review    -> {resp_r.status_code} -> {loc_r}")
        log(f"    带 tab=review 落到登录页               ：{'通过' if ok_review_route else '不通过'}")

        # 2) 两个页面本体
        resp2 = client.get("/static/app.html")
        log(f"\n  GET /static/app.html -> {resp2.status_code}，{len(resp2.text)} 字节")
        missing = [m for m in MARKERS if m not in resp2.text]
        ok_file = resp2.status_code == 200 and not missing
        log(f"    关键标记齐全                           ：{'是' if not missing else '缺 ' + str(missing)}")

        resp_l2 = client.get("/static/login.html")
        log(f"  GET /static/login.html -> {resp_l2.status_code}，{len(resp_l2.text)} 字节")
        lmissing = [m for m in LOGIN_MARKERS if m not in resp_l2.text]
        ok_login_file = resp_l2.status_code == 200 and not lmissing
        log(f"    登录页标记齐全                         ：{'是' if not lmissing else '缺 ' + str(lmissing)}")

        # 3) 旧页面确实删了
        still = [p for p in OLD_PAGES if client.get(p).status_code == 200]
        log(f"    旧页面已删除（{len(OLD_PAGES)} 个）                  ："
            f"{'通过' if not still else '不通过，还留着 ' + str(still)}")

        # 4) 模板变量对账：模板里引用的名字，Vue 实例里是否真的定义了
        # 手写页面最容易犯的错就是拼错一个名字，那一块直接渲染不出来还不报错
        used, loop_vars, unknown = reconcile(
            html.split("<script>")[0],
            set(DATA_KEYS) | set(COMPUTED_KEYS) | set(METHOD_KEYS) | set(GLOBALS))
        log(f"\n  模板变量对账（app.html）：")
        log(f"    v-for 循环变量（自动抠出）：{sorted(loop_vars)}")
        log(f"    模板里用了 {len(used)} 个根标识符：{sorted(used)}")
        log(f"    未定义的：{unknown if unknown else '无'}")

        lused, lloop, lunknown = reconcile(
            lhtml.split("<script>")[0],
            set(LOGIN_DATA_KEYS) | set(LOGIN_METHOD_KEYS) | set(GLOBALS))
        log(f"\n  模板变量对账（login.html）：")
        log(f"    v-for 循环变量（自动抠出）：{sorted(lloop)}")
        log(f"    模板里用了 {len(lused)} 个根标识符：{sorted(lused)}")
        log(f"    未定义的：{lunknown if lunknown else '无'}")

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

        # 6) 登录贯通：这三条连起来才叫"登录的时候就把会话建好了"
        ok_wire = (
            # 登录页把后端给的 session_id 存进本地
            ("session_id: res.body.data.session_id" in lhtml)
            # 主页面没登录就回登录页（search 一起带回去，/review 才不会丢 tab）
            and ("window.location.replace('/static/login.html' + window.location.search)" in html)
            # 主页面拿登录给的那个会话去校验，而不是自己另开一个
            and ("session_id: this.auth.session_id" in html)
        )
        log(f"\n  登录贯通（登录页存会话 -> 主页面没登录跳回 -> 主页面接上这个会话）："
            f"{'通过' if ok_wire else '不通过'}")

        # 7) 侧栏删除会话：按钮在模板里、点了走后端 DELETE、会话 id 和用户都带上了。
        # @click.stop 不能少，否则点 ✕ 会穿透到 openHistory（顺手把会话切走）
        ok_delete = (
            'class="hist-del"' in html
            and '@click.stop="removeSession(i)"' in html
            and "method: 'DELETE'" in html
            and "'/session/' + encodeURIComponent(item.id)" in html
            and "'?user_id=' + encodeURIComponent(this.userId)" in html
            and "window.confirm" in html
        )
        log(f"\n  侧栏删除会话（按钮 + DELETE /session/<id> 带 user_id + 二次确认）："
            f"{'通过' if ok_delete else '不通过'}")

        # 8) Markdown 兜底：模型把整篇回答套进 ```markdown 时，前端要拆壳再解析
        # （实测存进 Redis 的面试评价报告原文就是这样，不拆的话整篇被当代码块渲染）
        ok_md, md_err = fence_fallback(html)
        log(f"\n  Markdown 栅栏兜底（带 markdown 标记的栅栏要拆壳，真代码块不动）："
            f"{'通过' if ok_md else '不通过'}")
        if md_err:
            log("    " + md_err)

        # 9) 聊天列宽：消息列和输入框得一样宽，否则输入框跟消息左右错开。
        # 760px 居中在宽屏上两边会空出一大截（用户说的"左右间距太大"就是这个），
        # 所以顺手钉一个下限，以后改窄了会红
        def _max_width(selector):
            m = re.search(re.escape(selector) + r"\s*\{[^}]*max-width:\s*(\d+)px", css)
            return int(m.group(1)) if m else None

        w_thread, w_composer = _max_width(".thread-inner"), _max_width(".composer-inner")
        ok_width = w_thread is not None and w_thread == w_composer and w_thread >= 1000
        log(f"\n  聊天列宽（消息列 {w_thread}px / 输入框 {w_composer}px，要一样宽且不低于 1000）："
            f"{'通过' if ok_width else '不通过'}")

        # 10) JS 语法
        ok_js, js_err = js_syntax(html, "app")
        log(f"\n  JS 语法检查（node --check）app.html  ：{'通过' if ok_js else '不通过'}")
        if js_err:
            log("    " + js_err)
        ok_login_js, login_js_err = js_syntax(lhtml, "login")
        log(f"  JS 语法检查（node --check）login.html：{'通过' if ok_login_js else '不通过'}")
        if login_js_err:
            log("    " + login_js_err)

        # 11) 前后端对账
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

        # 12) 面试会话的复用分支：页面把本地存的 session_id 带回来，后端要走校验那条路
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
        ("/ 落到登录页          ", ok_home),
        ("/login 落到登录页     ", ok_login_route),
        ("/review 带 tab 落登录 ", ok_review_route),
        ("主页面可取到且标记齐全", ok_file),
        ("登录页可取到且标记齐全", ok_login_file),
        ("旧页面已删除          ", not still),
        ("主页面模板变量有定义  ", not unknown),
        ("登录页模板变量有定义  ", not lunknown),
        ("登录贯通              ", ok_wire),
        ("侧栏删除会话          ", ok_delete),
        ("Markdown 栅栏兜底     ", ok_md),
        ("聊天列宽一致          ", ok_width),
        ("学生气泡靠右          ", ok_align),
        ("JS 语法 app.html      ", ok_js),
        ("JS 语法 login.html    ", ok_login_js),
        ("前后端接口对账        ", not bad),
        ("会话复用分支被走到    ", ok_reuse),
    ]
    for title, ok in checks:
        log(f"  {title}：{'通过' if ok else '不通过'}")
    if unknown:
        log(f"    （app.html 未定义：{unknown}）")
    if lunknown:
        log(f"    （login.html 未定义：{lunknown}）")
    if bad:
        log(f"    （缺失接口：{bad}）")
    if still:
        log(f"    （旧页面还在：{still}）")

    # 用退出码兜住结论：只看 exit code 的场合不能永远返回 0
    return all(ok for _, ok in checks)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
