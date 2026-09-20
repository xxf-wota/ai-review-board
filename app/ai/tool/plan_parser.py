import io
import os

import pdfplumber
from docx import Document

"""
方案文档解析
把学生上传的方案（Word / PDF / 纯文本）统一转成纯文本，交给要素抽取节点
只做解析，不做任何清洗以外的事情，避免把原文改得评审看不懂
"""
# 支持的扩展名
SUPPORTED_EXTS = (".txt", ".md", ".docx", ".pdf")


# 去掉连续空行，模型看到一堆空行会浪费 token
def _tidy(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    result = []
    for line in lines:
        # 连续空行只保留一个
        if not line.strip() and result and not result[-1].strip():
            continue
        result.append(line)
    return "\n".join(result).strip()


# Word：按段落取文本，表格里的文字也要，方案里常有进度表
def _read_docx(stream) -> str:
    doc = Document(stream)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return _tidy("\n".join(parts))


# PDF：逐页取文本，扫描件取不到文字时明确提示，不要静默返回空
def _read_pdf(stream) -> str:
    parts = []
    with pdfplumber.open(stream) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
    content = _tidy("\n".join(parts))
    if not content:
        raise ValueError("这个 PDF 里没有可提取的文字，可能是扫描件或图片版，请改用 Word 或直接粘贴文本")
    return content


# 按扩展名选解析方式，stream 既可以是文件路径，也可以是 BytesIO
def _extract(ext: str, stream) -> str:
    if ext in (".txt", ".md"):
        if isinstance(stream, (bytes, bytearray)):
            return _tidy(stream.decode("utf-8", errors="ignore"))
        raw = stream.read() if hasattr(stream, "read") else stream
        if isinstance(raw, (bytes, bytearray)):
            return _tidy(raw.decode("utf-8", errors="ignore"))
        return _tidy(str(raw))
    if ext == ".docx":
        return _read_docx(stream)
    if ext == ".pdf":
        return _read_pdf(stream)
    if ext == ".doc":
        # python-docx 读不了老的 .doc 格式，直接说清楚，别让用户以为是解析出错
        raise ValueError("不支持旧版 .doc 格式，请另存为 .docx 后再上传")
    raise ValueError(f"不支持的文件类型：{ext}，目前支持 {'、'.join(SUPPORTED_EXTS)}")


# 解析磁盘上的方案文件
def parse_plan_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        with open(path, "rb") as f:
            return _extract(ext, f.read())
    with open(path, "rb") as f:
        return _extract(ext, io.BytesIO(f.read()))


# 解析上传上来的方案文件，filename 用来判断格式
def parse_plan_bytes(filename: str, data: bytes) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if not ext:
        raise ValueError("文件名没有扩展名，无法判断格式，请上传 txt / docx / pdf")
    return _extract(ext, io.BytesIO(data))
