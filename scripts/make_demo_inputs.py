# -*- coding: utf-8 -*-
"""
演示输入素材包：准备 + 自检

这个脚本管的是「上台要用的输入」这一件事：
    1. 准备 —— 把 data/ 下的方案样本收进 docs/演示输入素材包/方案样本/，
       并补齐 demo_plan 缺的那种格式（txt/docx/pdf 三种齐全，现场随便点哪个都能演示）
    2. 自检 —— 包里每一样东西都真的能用：样本能被 parse_plan_bytes 解析、
       三种格式字数对得上、回答稿覆盖四位评审的关注点、卡片里提到的文件都存在、
       预跑记录在。任何一项不过就非零退出

为什么要有自检：演示翻车最常见的原因不是程序崩了，是"拷错了文件 / 少了一种格式 /
回答稿缺了那个维度"。这些都靠脚本查，不靠上台前临时翻。

用法：
    python scripts/make_demo_inputs.py            # 准备 + 自检
    python scripts/make_demo_inputs.py --check    # 只自检，不动文件

报告写到 data/_demo_inputs_report.txt，返回码 0 表示全过。
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# fpdf2 装在仓库里（沙箱不让写系统 site-packages），要在 import 之前挂上
DEPS = os.path.join(ROOT, ".deps")
if os.path.isdir(DEPS) and DEPS not in sys.path:
    sys.path.insert(0, DEPS)

PKG = os.path.join(ROOT, "docs", "演示输入素材包")
SAMPLE_DIR = os.path.join(PKG, "方案样本")
REPORT = os.path.join(ROOT, "data", "_demo_inputs_report.txt")

# 现场要用的两份方案，每份都必须有 txt / docx / pdf
SAMPLE_STEMS = ["demo_plan", "sample_plan_eldercare"]
FORMATS = [".txt", ".docx", ".pdf"]
# 包里必须存在的文件（00 号卡片里逐个提到，缺一个现场就少一个动作）
REQUIRED = [
    "00_演示输入包.md",
    "10_回答稿.txt",
    "20_模拟面试输入.txt",
    "90_预跑记录.md",
    "方案样本/demo_plan.txt",
    "方案样本/demo_plan.docx",
    "方案样本/demo_plan.pdf",
    "方案样本/sample_plan_eldercare.txt",
    "方案样本/sample_plan_eldercare.docx",
    "方案样本/sample_plan_eldercare.pdf",
]
# 回答稿必须覆盖的四位评审 + 三种答法 + 跳过兜底
ANSWER_MUST = ["【技术评审】", "【成本与进度评审】", "【合规与伦理评审】", "【用户与价值评审】",
               "答虚版", "半答版", "认真版", "跳过"]

FONT = r"C:\Windows\Fonts\msyh.ttc"


# 把方案正文画成最简单的中文 PDF：一份文本一页一页排下去，没有样式
def text_to_pdf(text: str, pdf_path: str) -> None:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_font("cn", "", FONT)
    pdf.add_font("cn", "B", FONT)
    pdf.add_page()
    pdf.set_font("cn", size=12)
    for line in text.splitlines():
        # new_x/new_y 必须显式给：不给的话 multi_cell 的默认行为会把光标留在右边界，
        # 下一行算出可用宽度是 0，fpdf2 直接抛 "Not enough horizontal space to render a single character"
        pdf.multi_cell(0, 7.5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(pdf_path)


# 准备：把样本收进包里，缺的格式补出来
def prepare(lines: list) -> None:
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    for stem in SAMPLE_STEMS:
        for ext in FORMATS:
            src = os.path.join(ROOT, "data", stem + ext)
            # data/ 里缺的格式（目前只有 demo_plan.pdf 缺）：由同名的 txt 现生成到 data/。
            # 生成到 data/ 而不是只生成到包里，是为了让样本目录本身就是三格式齐全的 —— 
            # README 里"两个样本各自提供 txt / docx / pdf"那句话才对得上实物
            if not os.path.exists(src) and ext == ".pdf":
                txt = os.path.join(ROOT, "data", stem + ".txt")
                if os.path.exists(txt):
                    with open(txt, encoding="utf-8") as f:
                        text = f.read()
                    text_to_pdf(text, src)
                    lines.append(f"  新生成：data/{stem}.pdf（由 data/{stem}.txt 转出）")
            dst = os.path.join(SAMPLE_DIR, stem + ext)
            if not os.path.exists(src):
                lines.append(f"  缺：{stem}{ext}（data/ 里也没有，包和样本目录都少了这一种）")
                continue
            if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                shutil.copyfile(src, dst)
                lines.append(f"  收进包里：方案样本/{stem}{ext}")
    # 英文版方案当备用：中文方案抽要素不理想时换它上
    en = os.path.join(ROOT, "data", "sample_plan_en.pdf")
    if os.path.exists(en):
        shutil.copyfile(en, os.path.join(SAMPLE_DIR, "sample_plan_en.pdf"))
        lines.append("  收进包里：方案样本/sample_plan_en.pdf（备用）")


# 自检：包里的东西每一样都要真的能用
def check(lines: list) -> bool:
    from app.ai.tool.plan_parser import parse_plan_bytes
    from app.web.review_router.review_router import MAX_PLAN_CHARS

    ok = True

    # 1. 该在的文件都在
    for rel in REQUIRED:
        path = os.path.join(PKG, rel.replace("/", os.sep))
        if not os.path.exists(path):
            lines.append(f"  [不过] 缺文件：{rel}")
            ok = False
    lines.append(f"  文件齐全：{len(REQUIRED)} 项逐个查过")

    # 2. 每份方案的三种格式都能解析，字数还要对得上（docx/pdf 抽字偶有出入，5% 以内算正常）
    for stem in SAMPLE_STEMS:
        counts = {}
        for ext in FORMATS:
            path = os.path.join(SAMPLE_DIR, stem + ext)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "rb") as f:
                    text = parse_plan_bytes(os.path.basename(path), f.read())
            except Exception as e:
                lines.append(f"  [不过] {stem}{ext} 解析失败：{type(e).__name__}: {e}")
                ok = False
                continue
            if not text.strip():
                lines.append(f"  [不过] {stem}{ext} 解析出来是空的")
                ok = False
                continue
            if len(text) > MAX_PLAN_CHARS:
                lines.append(f"  [不过] {stem}{ext} 太长：{len(text)} 字 > {MAX_PLAN_CHARS}")
                ok = False
            counts[ext] = len(text)
        if len(counts) == 3:
            lo, hi = min(counts.values()), max(counts.values())
            if hi - lo > hi * 0.05:
                lines.append(f"  [不过] {stem} 三种格式字数差太多：{counts}")
                ok = False
            else:
                lines.append(f"  {stem}：三种格式都能解析，字数 {counts}（差 {hi - lo} 字，在 5% 以内）")

    # 3. 回答稿要覆盖四位评审、三种答法和跳过兜底，否则现场会缺一段
    answer_path = os.path.join(PKG, "10_回答稿.txt")
    if os.path.exists(answer_path):
        with open(answer_path, encoding="utf-8") as f:
            answer = f.read()
        missing = [k for k in ANSWER_MUST if k not in answer]
        if missing:
            lines.append(f"  [不过] 回答稿缺：{missing}")
            ok = False
        else:
            lines.append(f"  回答稿：{len(answer)} 字符，四位评审 / 三种答法 / 跳过兜底都覆盖了")

    # 4. 00 号卡片要提包里每个文件，不然现场翻不到
    card_path = os.path.join(PKG, "00_演示输入包.md")
    if os.path.exists(card_path):
        with open(card_path, encoding="utf-8") as f:
            card = f.read()
        not_mentioned = [r for r in REQUIRED if r.split("/")[-1] not in card]
        if not_mentioned:
            lines.append(f"  [不过] 00 号卡片里没提到：{not_mentioned}")
            ok = False
        else:
            lines.append("  00 号卡片：包里每个文件都在卡片里指过路")

    # 5. 预跑记录要真跑过，且跑完了
    dry = os.path.join(ROOT, "data", "_demo_dry_run.txt")
    if not os.path.exists(dry):
        lines.append("  [不过] 没有预跑记录 data/_demo_dry_run.txt —— 先跑 python scripts/dry_run_demo.py")
        ok = False
    else:
        with open(dry, encoding="utf-8") as f:
            text = f.read()
        if "结论：这场开完了" not in text:
            lines.append("  [不过] 预跑记录里这场没开完")
            ok = False
        else:
            cross = "接话出现：是" in text
            follow = "追问出现：是" in text
            lines.append(f"  预跑记录：开完了；接话 {'有' if cross else '没有'}；"
                         f"追问 {'有' if follow else '没有'}")
            if not (cross and follow):
                lines.append("  [注意] 这份记录里接话 / 追问不全 —— 上台前重跑预跑直到两个都出现")
    return ok


def main() -> int:
    check_only = "--check" in sys.argv
    lines = []
    if check_only:
        lines.append("模式：只自检（不动文件）")
    else:
        lines.append("一、准备")
        prepare(lines)

    lines.append("")
    lines.append("二、自检")
    ok = check(lines)
    lines.append("")
    lines.append("结论：" + ("全过" if ok else "**有不过的项**"))

    text = "\n".join(lines) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"（已写入 {os.path.relpath(REPORT, ROOT)}）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
