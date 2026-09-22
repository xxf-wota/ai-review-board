# -*- coding: utf-8 -*-
"""
生成方案上传功能的测试素材

两份样本，各自生成 Word 与 PDF：
- data/demo_plan.txt           校园二手教材平台（信息较全，基础样本）
- data/sample_plan_eldercare.txt 社区居家养老系统（四个维度都留了破绽，用于测评审效果）

PDF 用 Chrome 无头模式从 HTML 渲染，这样中文能正常嵌入。
（环境里没有 reportlab/fpdf；手拼字节流只能放 base14 字体，不含中文字形。
 data/sample_plan_en.pdf 是早期手写的纯英文 PDF，不依赖 Chrome，
 已提交在仓库里作为兜底样本，本脚本不再重新生成它。）
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docx import Document

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 文本样本 -> 要生成的 docx / pdf
SAMPLES = [
    ("demo_plan.txt", "demo_plan.docx", None),
    ("sample_plan_eldercare.txt", "sample_plan_eldercare.docx", "sample_plan_eldercare.pdf"),
]

# Chrome 所在位置，用来把 HTML 打成 PDF
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


# 按空行分段，逐段写进 Word
def build_docx(txt_name, docx_name):
    with open(os.path.join(ROOT, "data", txt_name), encoding="utf-8") as f:
        blocks = [b.strip() for b in f.read().split("\n\n") if b.strip()]
    doc = Document()
    for block in blocks:
        doc.add_paragraph(block)
    out = os.path.join(ROOT, "data", docx_name)
    doc.save(out)
    return out, len(blocks)


# 拼一个干净的公文式 HTML，交给 Chrome 渲染成 PDF
def build_html(txt_name):
    with open(os.path.join(ROOT, "data", txt_name), encoding="utf-8") as f:
        blocks = [b.strip() for b in f.read().split("\n\n") if b.strip()]
    body = "\n".join("<p>%s</p>" % b.replace("\n", "<br>") for b in blocks)
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        "body{font-family:'Microsoft YaHei','PingFang SC',sans-serif;"
        "font-size:14px;line-height:1.95;margin:34px 44px;color:#111}"
        "p{margin:0 0 11px}"
        "</style></head><body>%s</body></html>" % body
    )
    path = os.path.join(ROOT, ".git", "_pdf_source.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def build_pdf_chrome(chrome, txt_name, pdf_name):
    html_path = build_html(txt_name)
    pdf_path = os.path.join(ROOT, "data", pdf_name)
    # 用户数据目录放进工作区，避免沙箱拦掉工作区外的写入
    profile = os.path.join(ROOT, ".git", "_chrome_pdf_profile")
    log_path = os.path.join(ROOT, ".git", "_chrome_pdf.log")
    url = "file:///" + html_path.replace("\\", "/")
    cmd = [
        chrome,
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--user-data-dir=" + profile,
        "--no-pdf-header-footer",
        "--print-to-pdf=" + pdf_path,
        url,
    ]
    # stderr 重定向到文件而不是管道：沙箱不允许子进程走管道
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=log, timeout=120)
    ok = os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000
    return pdf_path, ok, proc.returncode


def main():
    lines = []
    chrome = find_chrome()

    for txt_name, docx_name, pdf_name in SAMPLES:
        docx_path, blocks = build_docx(txt_name, docx_name)
        lines.append(f"{docx_name} 生成：{os.path.getsize(docx_path)} 字节，{blocks} 段")

        if not pdf_name:
            continue
        if not chrome:
            lines.append(f"{pdf_name} 跳过：没找到 Chrome，PDF 需要它来嵌入中文字形")
            continue
        pdf_path, ok, code = build_pdf_chrome(chrome, txt_name, pdf_name)
        if ok:
            lines.append(f"{pdf_name} 生成：{os.path.getsize(pdf_path)} 字节（Chrome 渲染）")
        else:
            lines.append(f"{pdf_name} 失败：Chrome 退出码 {code}，详情见 .git/_chrome_pdf.log")

    with open(os.path.join(ROOT, "data", "_m1_fixtures.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("fixtures done")


if __name__ == "__main__":
    main()
