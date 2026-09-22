# -*- coding: utf-8 -*-
"""
评审会页面验收
1. /review 是否能跳到页面
2. 页面文件是否能取到，关键标记是否齐全
3. 页面里 fetch 调用的每个后端接口是否真的注册了（前后端对账，防拼写不一致）
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
PAGE = os.path.join(ROOT, "app", "html", "review.html")

# 页面里必须出现的标记
MARKERS = [
    "AI 交叉质询评审团",
    "⟶ 接",
    "质询",
    "方案要素表",
    "方案缺失项",
]

# 模板变量对账用的名单，要和 review.html 里 Vue 实例的定义保持一致
DATA_KEYS = ["reviewers", "planText", "elements", "sessionId", "utterances",
             "current", "running", "error", "status", "summary"]
COMPUTED_KEYS = ["spokenRoles", "statusText"]
METHOD_KEYS = ["listText", "isAbsent", "onFile", "submitPlan", "startMeeting", "handleEvent"]
# v-for 里的循环变量
LOOP_VARS = ["r", "i", "u", "m"]
# 模板里合法出现的全局名字
GLOBALS = ["true", "false", "null", "JSON", "String", "Number", "Math", "Array", "Object"]
# JS 关键字，不是变量
KEYWORDS = ["in", "of", "new", "typeof", "return", "if", "else", "function", "var", "let", "const"]


def main():
    lines = []

    def log(text=""):
        lines.append(str(text))
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(str(text)[:200].encode("ascii", "replace").decode("ascii"))

    log("=" * 70)
    log("评审会页面验收")
    log("=" * 70)

    # 先把页面文件里调用的后端接口抠出来，供后面和第 3 步对账
    with open(PAGE, encoding="utf-8") as f:
        html = f.read()
    called = sorted(set(re.findall(r"fetch\(\s*['\"](/review/[^'\"]*)['\"]", html)))

    with TestClient(app) as client:
        # 1) 页面路由
        resp = client.get("/review", follow_redirects=False)
        log(f"\n  GET /review                     -> {resp.status_code}")
        log(f"    跳转到                        -> {resp.headers.get('location')}")
        ok_route = resp.status_code in (301, 302, 307, 308) and \
            (resp.headers.get("location") or "").endswith("/static/review.html")

        # 2) 页面文件本体
        resp2 = client.get("/static/review.html")
        log(f"\n  GET /static/review.html         -> {resp2.status_code}")
        log(f"    页面大小                      -> {len(resp2.text)} 字节")
        ok_file = resp2.status_code == 200
        if ok_file:
            missing = [m for m in MARKERS if m not in resp2.text]
            log(f"    关键标记齐全                  -> {'是' if not missing else '缺 ' + str(missing)}")
            ok_file = not missing

        # 3) 模板变量对账：模板里引用的名字，Vue 实例里是否真的定义了
        # 手写页面最容易犯的错就是拼错一个名字，那一块直接渲染不出来还不报错
        known = set(DATA_KEYS) | set(COMPUTED_KEYS) | set(METHOD_KEYS) | set(LOOP_VARS) | set(GLOBALS)
        template = html.split("<script>")[0]
        exprs = []
        # 指令绑定：v-if / :class / @click 等的值
        exprs += re.findall(r'(?:v-[\w:.-]+|[:@][\w.-]+)="([^"]*)"', template)
        # 插值：{{ ... }}
        exprs += re.findall(r"\{\{(.*?)\}\}", template, re.S)

        used = set()
        for expr in exprs:
            # 先把字符串字面量剔掉，否则 'cross'、's' 这种会被当成变量名
            expr = re.sub(r"'[^']*'|\"[^\"]*\"", " ", expr)
            for name in re.findall(r"[A-Za-z_$][\w$]*", expr):
                # 只看根标识符：前面带点的说明是属性名（elements.goal 里的 goal）
                idx = expr.find(name)
                if idx > 0 and expr[idx - 1] == ".":
                    continue
                # 后面紧跟冒号的说明是对象字面量的键（{ on: ... } 里的 on）
                after = expr[idx + len(name):].lstrip()
                if after.startswith(":"):
                    continue
                used.add(name)

        used -= set(KEYWORDS)
        unknown = sorted(n for n in used if n not in known)
        log(f"\n  模板变量对账：")
        log(f"    模板里用了 {len(used)} 个根标识符：{sorted(used)}")
        log(f"    未定义的：{unknown if unknown else '无'}")

        # 3.5) JS 语法检查：手写页面最容易犯的就是语法错误，一错整个页面直接白屏，
        # 而且浏览器不一定把错误说清楚。用 node --check 只做语法校验，不执行代码
        script = html.rsplit("<script>", 1)[1].split("</script>")[0]
        # 临时文件放 .git 下：那个目录永远不会被提交，不用污染 .gitignore
        js_tmp = os.path.join(ROOT, ".git", "_page_check.js")
        err_tmp = os.path.join(ROOT, ".git", "_page_check.err")
        with open(js_tmp, "w", encoding="utf-8") as f:
            f.write(script)
        # stderr 重定向到文件而不是管道：沙箱不允许子进程走管道
        with open(err_tmp, "w", encoding="utf-8") as errf:
            proc = subprocess.run(["node", "--check", js_tmp], stdout=errf, stderr=errf)
        ok_js = proc.returncode == 0
        log(f"\n  JS 语法检查（node --check）：{'通过' if ok_js else '不通过'}")
        if not ok_js:
            with open(err_tmp, encoding="utf-8") as f:
                log("    " + f.read().strip()[:500])

        # 4) 前后端对账：页面调用的接口，后端是否都在
        spec = client.get("/openapi.json").json()
        paths = set(spec.get("paths", {}).keys())
        log(f"\n  页面调用的接口（共 {len(called)} 个）：")
        bad = []
        for url in called:
            hit = url in paths
            if not hit:
                bad.append(url)
            log(f"    {'✓' if hit else '✗'} {url}")
        log(f"\n  后端已注册的接口：")
        for p in sorted(paths):
            log(f"    {p}")
        log(f"\n  对账结果：{'全部存在' if not bad else '缺失 ' + str(bad)}")

    log("\n" + "=" * 70)
    log("页面验收结论")
    log("=" * 70)
    log(f"  /review 跳转          ：{'通过' if ok_route else '不通过'}")
    log(f"  页面可取到且标记齐全  ：{'通过' if ok_file else '不通过'}")
    log(f"  JS 语法               ：{'通过' if ok_js else '不通过'}")
    log(f"  模板变量全部有定义    ：{'通过' if not unknown else '不通过，未定义 ' + str(unknown)}")
    log(f"  前后端接口对账        ：{'通过' if not bad else '不通过，缺失 ' + str(bad)}")


if __name__ == "__main__":
    main()
