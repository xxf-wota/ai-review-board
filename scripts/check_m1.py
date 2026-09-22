# -*- coding: utf-8 -*-
"""
M1 端到端验收：方案上传 -> 解析 -> 抽要素 -> 落库 -> 读回
走真实的 app.main（含 main.py 的路由接线），用 FastAPI TestClient 打接口

验收标准：
    1. txt / docx / pdf 三种格式都能解析出正文
    2. POST /review/upload 能把上传的 Word 解析成文本
    3. POST /review/submit 能抽出要素表，并真的写进 MySQL
    4. GET /review/session/{id} 能读回刚写的数据
    5. 错误输入能返回 400 而不是 500
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# 必须从项目根目录运行：app/main.py 里静态目录是 app/html，相对项目根解析

from fastapi.testclient import TestClient  # noqa: E402

from app.ai.tool.plan_parser import parse_plan_file  # noqa: E402
from app.ai.utils.mysql_util import get_mysql_conn  # noqa: E402
from app.main import app  # noqa: E402

REPORT = os.path.join(ROOT, "data", "_m1_check.txt")
TXT = os.path.join(ROOT, "data", "demo_plan.txt")
DOCX = os.path.join(ROOT, "data", "demo_plan.docx")
PDF = os.path.join(ROOT, "data", "sample_plan_en.pdf")
# 第二份样本（社区居家养老）：四个维度都留了破绽，用来测评审效果
ELDER_TXT = os.path.join(ROOT, "data", "sample_plan_eldercare.txt")
ELDER_DOCX = os.path.join(ROOT, "data", "sample_plan_eldercare.docx")
ELDER_PDF = os.path.join(ROOT, "data", "sample_plan_eldercare.pdf")

_lines = []


def log(text=""):
    _lines.append(str(text))
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    print(str(text)[:200].encode("ascii", "replace").decode("ascii"))


def main():
    log("=" * 70)
    log("M1 验收：方案上传 + 要素抽取 + 落库")
    log("=" * 70)

    # ---------- 步骤一：三种格式的解析 ----------
    log("\n" + "-" * 70)
    log("步骤一：文档解析（txt / docx / pdf）")
    log("-" * 70)
    parsed = {}
    for path, must_have in [
        (TXT, "教材"),
        (DOCX, "教材"),
        (PDF, "FastAPI"),
        # 中文 PDF：验证 Chrome 渲染出来的 PDF 里汉字能正常抽出来
        (ELDER_TXT, "独居"),
        (ELDER_DOCX, "独居"),
        (ELDER_PDF, "独居"),
    ]:
        name = os.path.basename(path)
        try:
            text = parse_plan_file(path)
            parsed[path] = text
            ok = "命中关键内容" if must_have in text else f"！缺少关键内容 {must_have}"
            log(f"  {name:<24} {len(text):>5} 字   {ok}")
        except Exception as e:
            log(f"  {name:<24} 解析失败：{type(e).__name__}: {e}")

    # ---------- 步骤二：走接口上传 Word ----------
    log("\n" + "-" * 70)
    log("步骤二：POST /review/upload 上传 Word")
    log("-" * 70)
    with TestClient(app) as client:
        with open(DOCX, "rb") as f:
            resp = client.post(
                "/review/upload",
                files={"file": ("demo_plan.docx", f,
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        log(f"  状态码：{resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            plan_text = data["plan_text"]
            log(f"  文件名：{data['filename']}，解析出 {data['chars']} 字")
            log(f"  正文开头：{plan_text[:60]}...")
        else:
            log(f"  失败：{resp.text[:300]}")
            plan_text = ""

        # ---------- 步骤三：提交方案，抽要素并落库 ----------
        log("\n" + "-" * 70)
        log("步骤三：POST /review/submit 抽要素 + 落库")
        log("-" * 70)
        resp = client.post("/review/submit", json={
            "plan_text": plan_text,
            "plan_title": "校园二手教材智能交易平台",
            "student_id": "check_m1",
        })
        log(f"  状态码：{resp.status_code}")
        session_id = ""
        if resp.status_code == 200:
            data = resp.json()
            session_id = data["session_id"]
            log(f"  会话ID：{session_id}")
            log(f"  方案标题：{data['plan_title']}")
            log("\n  要素表：")
            for line in data["elements_text"].splitlines():
                log(f"    {line}")
        else:
            log(f"  失败：{resp.text[:300]}")

        # ---------- 步骤四：读回，确认真的落库了 ----------
        log("\n" + "-" * 70)
        log("步骤四：GET /review/session/{id} 读回")
        log("-" * 70)
        if session_id:
            resp = client.get(f"/review/session/{session_id}")
            log(f"  状态码：{resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                log(f"  标题：{data['plan_title']}")
                log(f"  学生：{data['student_id']}   状态：{data['status']}   轮次：{data['round']}")
                log(f"  方案正文长度：{len(data['plan_text'] or '')} 字")
                log(f"  要素表读回：{len(data['plan_elements'])} 个字段")
                log(f"  创建时间：{data['created_at']}")
            else:
                log(f"  失败：{resp.text[:300]}")
        else:
            log("  跳过：上一步没拿到会话ID")

        # 清理本次及历史验收写进数据库的测试数据，不给库里留垃圾
        # 只按验收专用的 student_id 删，碰不到真实数据
        conn = get_mysql_conn()
        try:
            cur = conn.cursor()
            cur.execute("delete from review_session where student_id=%s", ("check_m1",))
            conn.commit()
            log(f"\n  已清理验收测试数据 {cur.rowcount} 行")
        finally:
            conn.close()

        # ---------- 步骤五：错误输入 ----------
        log("\n" + "-" * 70)
        log("步骤五：错误输入应返回 400 而不是 500")
        log("-" * 70)
        # 空方案
        resp = client.post("/review/submit", json={"plan_text": "   "})
        log(f"  空方案            -> {resp.status_code}  {str(resp.json().get('detail'))[:50]}")
        # 不支持的格式
        resp = client.post("/review/upload", files={"file": ("old.doc", b"dummy", "application/msword")})
        log(f"  上传 .doc         -> {resp.status_code}  {str(resp.json().get('detail'))[:50]}")
        # 空文件
        resp = client.post("/review/upload", files={"file": ("empty.txt", b"", "text/plain")})
        log(f"  上传空文件        -> {resp.status_code}  {str(resp.json().get('detail'))[:50]}")
        # 不存在的会话
        resp = client.get("/review/session/not_exist_xxx")
        log(f"  读不存在的会话    -> {resp.status_code}  {str(resp.json().get('detail'))[:50]}")

        # ---------- 步骤六：确认原有接口没被弄坏 ----------
        log("\n" + "-" * 70)
        log("步骤六：路由注册情况（回归检查）")
        log("-" * 70)
        # 直接问 OpenAPI，这是已注册接口的权威清单
        spec = client.get("/openapi.json").json()
        paths = sorted(spec.get("paths", {}).keys())
        for path in paths:
            methods = ",".join(sorted(m.upper() for m in spec["paths"][path]))
            log(f"    {methods:<12} {path}")
        # 原有的和新增的关键接口都必须还在
        need = ["/", "/chat", "/review/upload", "/review/submit", "/review/session/{session_id}"]
        missing = [p for p in need if p not in paths]
        log(f"\n  关键接口缺失：{missing if missing else '无'}")

    log("\n" + "=" * 70)
    log("M1 验收结束")
    log("=" * 70)


if __name__ == "__main__":
    main()
