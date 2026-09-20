# -*- coding: utf-8 -*-
"""
生成方案上传功能的测试素材
- demo_plan.docx：由 data/demo_plan.txt 转成 Word（中文，和文本样本内容一致）
- sample_plan_en.pdf：手写 PDF（环境里没有 reportlab/fpdf，所以自己拼字节流）

为什么 PDF 是英文的：base14 字体（Helvetica）不含中文字形，要放中文必须内嵌 CJK 字体
（FontDescriptor + CIDFont + ToUnicode CMap），为了一个解析测试不值得
PDF 这条路径要验证的是"能不能把文字抽出来"，英文同样能验证
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docx import Document

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TXT_PATH = os.path.join(ROOT, "data", "demo_plan.txt")
DOCX_PATH = os.path.join(ROOT, "data", "demo_plan.docx")
PDF_PATH = os.path.join(ROOT, "data", "sample_plan_en.pdf")

# PDF 正文，每行不要太长，免得超出页面宽度
PDF_LINES = [
    "Campus Second-hand Textbook Platform",
    "",
    "Background: textbooks are replaced every term, and most copies sit idle.",
    "Goal: a trading platform serving 5000 students of this university.",
    "",
    "Tech stack: FastAPI backend, Vue3 frontend, MySQL and Redis for storage.",
    "A multimodal LLM reads the cover photo and fills in the book information.",
    "Another LLM call does semantic matching between buyers and sellers.",
    "",
    "Schedule: 10 weeks, finished by 3 undergraduate students.",
    "Outcome: a running platform plus a graduation thesis.",
]


# 转义 PDF 字符串里的反斜杠和括号
def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


# 手写一个最小可用的 PDF：一个页面、一段文字流、偏移正确的 xref
def build_pdf(lines) -> bytes:
    content = "BT /F1 12 Tf 72 770 Td 16 TL\n"
    for line in lines:
        content += f"({_escape(line)}) Tj T*\n"
    content += "ET"
    stream = content.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    # xref 表，每条 20 字节，偏移必须精确
    xref_pos = len(out)
    total = len(objects) + 1
    out += f"xref\n0 {total}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def main():
    # 1) Word：把文本样本按空行分段搬过去
    with open(TXT_PATH, encoding="utf-8") as f:
        blocks = [b.strip() for b in f.read().split("\n\n") if b.strip()]

    doc = Document()
    for block in blocks:
        doc.add_paragraph(block)
    doc.save(DOCX_PATH)

    # 2) PDF
    with open(PDF_PATH, "wb") as f:
        f.write(build_pdf(PDF_LINES))

    report = [
        f"demo_plan.docx 生成：{os.path.getsize(DOCX_PATH)} 字节，{len(blocks)} 段",
        f"sample_plan_en.pdf 生成：{os.path.getsize(PDF_PATH)} 字节，{len(PDF_LINES)} 行",
    ]
    with open(os.path.join(ROOT, "data", "_m1_fixtures.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("fixtures done")


if __name__ == "__main__":
    main()
