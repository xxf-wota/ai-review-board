# -*- coding: utf-8 -*-
"""
把 docs/答辩材料-AI交叉质询评审团.md 导出成 .txt 和 .pdf（外加一份可编辑的 .docx）。

为什么是这条路：
  这台机器上 pip **装不进系统目录**（沙箱只允许写工作区，装到 site-packages 会
  [WinError 5] 拒绝访问），所以 fpdf2 装在仓库内的 `.deps/` 里，脚本自己把它加进 sys.path。
  没有 soffice / libreoffice / pandoc / wkhtmltopdf，Word COM 也实测不可靠
  （Documents.Open 报 "远程过程调用失败"），所以：
    txt   —— 自己按块渲染（不引第三方，纯文本最好读、最好搜索）
    pdf   —— fpdf2 直接写文字版（中文可选中、可搜索，字体子集化，体积小）
    docx  —— python-docx 顺手给一份可编辑的（没装就跳过，不算失败）
  全都要过验收：txt 不许残留 Markdown 记号、pdf 必须真的能抽出中文关键词、字体不许缺字。

用法：python scripts/make_defense_docs.py
产出：docs/答辩材料-AI交叉质询评审团.{txt,pdf,docx}
     验收报告 data/_defense_docs.txt
退出码：全部通过 0，任何一项不过 1（验收脚本不能只会绿）
"""
import os
import re
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPS = os.path.join(ROOT, ".deps")
if os.path.isdir(DEPS) and DEPS not in sys.path:
    sys.path.insert(0, DEPS)

DOC_STEM = "答辩材料-AI交叉质询评审团"
SRC = os.path.join(ROOT, "docs", DOC_STEM + ".md")
TXT = os.path.join(ROOT, "docs", DOC_STEM + ".txt")
DOCX = os.path.join(ROOT, "docs", DOC_STEM + ".docx")
PDF = os.path.join(ROOT, "docs", DOC_STEM + ".pdf")
REPORT = os.path.join(ROOT, "data", "_defense_docs.txt")

# PDF 里必须能抽出这些词，才说明中文没变成乱码/图片
KEYWORDS = ["交叉质询", "未答好", "qwen2.5:7b", "116"]

CN_FONT = "微软雅黑"
MONO_FONT = "Consolas"
# 候选中文字体：第一个能真正加载的胜出（.ttc 是字体集合，fpdf2 按第 0 号面读）
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
    ("C:/Windows/Fonts/Deng.ttf", "C:/Windows/Fonts/Dengb.ttf"),
    ("C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simhei.ttf"),
    ("C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/simsun.ttc"),
]
MONO_CANDIDATES = ["C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/consolab.ttf"]

# 中文字体里没有的符号，统一换成有的，免得 PDF 里出现空白方块
SAFE_MAP = {
    "\u27f6": "\u2192",   # ⟶ -> →
    "\u21b3": "\u2514",   # ↳ -> └
    "\u2500": "-",        # ─ -> -
    "\u2192": "\u2192",   # → 保留
    "\u2265": "\u2265",   # ≥ 保留
}

LOG = []


def log(msg=""):
    LOG.append(str(msg))


def sanitize(text):
    for k, v in SAFE_MAP.items():
        text = text.replace(k, v)
    return text


# --------------------------------------------------------------------------
# 把 Markdown 解析成块，txt / pdf / docx 三个渲染器共用这一份解析结果
# --------------------------------------------------------------------------
def _is_table_row(s):
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def _is_table_sep(s):
    cells = [c.strip() for c in s.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells)


def inline_plain(s):
    """去掉行内 Markdown 记号：**粗体** / `代码` / [文字](链接)"""
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", s)
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!])", r"\1", s)
    return sanitize(s.strip())


def parse_blocks(lines):
    """md 行流 -> 块：('h',级别,文本) / ('p',文本) / ('quote',文本) /
    ('bullet',文本,缩进) / ('num',文本,缩进) / ('code',[行]) / ('table',[行]) / ('hr',)"""
    blocks = []
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()

        if not s:
            i += 1
            continue

        if s.startswith("```"):
            inner = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                inner.append(lines[i].rstrip())
                i += 1
            i += 1
            blocks.append(("code", inner))
            continue

        if _is_table_row(s):
            rows = []
            while i < n and _is_table_row(lines[i].strip()):
                cur = lines[i].strip()
                if not _is_table_sep(cur):
                    rows.append([c.strip() for c in cur.strip("|").split("|")])
                i += 1
            if rows:
                blocks.append(("table", rows))
            continue

        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            blocks.append(("hr",))
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            blocks.append(("h", len(m.group(1)), m.group(2).strip()))
            i += 1
            continue

        if s.startswith(">"):
            blocks.append(("quote", s.lstrip(">").strip()))
            i += 1
            continue

        if s.startswith(("- ", "* ")):
            blocks.append(("bullet", s[2:].strip(), len(raw) - len(raw.lstrip())))
            i += 1
            continue

        m = re.match(r"^(\d+)\.\s+(.*)$", s)
        if m:
            blocks.append(("num", f"{m.group(1)}. {m.group(2).strip()}",
                           len(raw) - len(raw.lstrip())))
            i += 1
            continue

        blocks.append(("p", s))
        i += 1
    return blocks


# --------------------------------------------------------------------------
# 纯文本渲染
# --------------------------------------------------------------------------
def _dw(s):
    """显示宽度：中日韩全角算 2，其余算 1"""
    w = 0
    for ch in s:
        w += 2 if (0x1100 <= ord(ch) <= 0x115F or 0x2E80 <= ord(ch) <= 0xA4CF
                   or 0xAC00 <= ord(ch) <= 0xD7A3 or 0xF900 <= ord(ch) <= 0xFAFF
                   or 0xFE30 <= ord(ch) <= 0xFE6F or 0xFF00 <= ord(ch) <= 0xFF60
                   or 0xFFE0 <= ord(ch) <= 0xFFE6 or 0x20000 <= ord(ch) <= 0x3FFFD) else 1
    return w


def _pad(s, width):
    return s + " " * max(0, width - _dw(s))


def render_txt(blocks):
    out = []
    for b in blocks:
        kind = b[0]
        if kind == "h":
            _, level, text = b
            text = inline_plain(text)
            if level == 1:
                out += [text, "=" * min(_dw(text) + 4, 78), ""]
            elif level == 2:
                out += ["", text, "-" * min(_dw(text) + 4, 78), ""]
            else:
                out += ["", text, ""]
        elif kind == "p":
            out += [inline_plain(b[1]), ""]
        elif kind == "quote":
            text = inline_plain(b[1])
            out.append("    " + text if text else "")
        elif kind == "bullet":
            out.append(" " * b[2] + "· " + inline_plain(b[1]))
        elif kind == "num":
            out.append(" " * b[2] + inline_plain(b[1]))
        elif kind == "code":
            out += ["    " + inline_plain(l) if l.strip() else "" for l in b[1]]
            out.append("")
        elif kind == "hr":
            out += ["", "-" * 64, ""]
        elif kind == "table":
            rows = [[inline_plain(c) for c in r] for r in b[1]]
            cols = max(len(r) for r in rows)
            widths = [0] * cols
            for r in rows:
                for j, c in enumerate(r):
                    widths[j] = max(widths[j], _dw(c))
            widths = [min(w, 46) for w in widths]
            out.append("  ".join(_pad(c, widths[j]) for j, c in enumerate(rows[0])).rstrip())
            out.append("  ".join("-" * widths[j] for j in range(cols)))
            for r in rows[1:]:
                out.append("  ".join(_pad(c, widths[j]) for j, c in enumerate(r)).rstrip())
            out.append("")
    cleaned = []
    for line in out:
        if line == "" and cleaned and cleaned[-1] == "":
            continue
        cleaned.append(line)
    return "\n".join(cleaned).rstrip() + "\n"


# --------------------------------------------------------------------------
# PDF 渲染（fpdf2）
# --------------------------------------------------------------------------
def _fonts_loadable(reg, bold):
    """真把字体用一遍，确认 fpdf2 + fontTools 能读（.ttc 有些面读不了）"""
    from fpdf import FPDF
    p = FPDF()
    p.add_font("T", "", reg)
    if bold and bold != reg:
        p.add_font("T", "B", bold)
    p.add_page()
    p.set_font("T", "", 12)
    p.cell(0, 8, "字体测试 中文 ABC 123")
    p.set_font("T", "B" if (bold and bold != reg) else "", 12)
    p.cell(0, 8, "加粗测试")
    return True


def pick_fonts():
    for reg, bold in FONT_CANDIDATES:
        if not os.path.exists(reg):
            continue
        if bold and not os.path.exists(bold):
            bold = None
        try:
            _fonts_loadable(reg, bold)
            mono = next((m for m in MONO_CANDIDATES if os.path.exists(m)), None)
            return reg, bold, mono
        except Exception:
            continue
    raise RuntimeError("找不到可用的中文字体（试过：%s）" % ", ".join(r for r, _ in FONT_CANDIDATES))


def missing_glyphs(text, font_path):
    """字体里没有的字符（PDF 里会变空白方块），用 fontTools 直接查 cmap"""
    from fontTools.ttLib import TTFont
    f = TTFont(font_path, fontNumber=0, lazy=True)
    cmap = f.getBestCmap()
    return sorted({ch for ch in text if ord(ch) > 127 and ord(ch) not in cmap})


def _ascii_only(s):
    return all(ord(c) < 128 for c in s)


def render_pdf(blocks, path):
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    reg, bold, mono = pick_fonts()
    fam, mono_fam = "CN", "MONO"
    stat = {"font": reg, "mono": mono, "mono_cn_fallback": 0}

    class Doc(FPDF):
        def footer(self):
            self.set_y(-13)
            self.set_font(fam, "", 8)
            self.set_text_color(150, 150, 150)
            self.cell(0, 6, str(self.page_no()), align="C")

    pdf = Doc(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_font(fam, "", reg)
    if bold and bold != reg:
        pdf.add_font(fam, "B", bold)
    if mono:
        pdf.add_font(mono_fam, "", mono)
    pdf.set_title("AI 交叉质询评审团 · 答辩材料")
    pdf.set_author("exam_agent")
    pdf.add_page()

    body, lh = 10.5, 5.6
    base_bold = "B" if (bold and bold != reg) else ""

    def inline(text, size=body):
        for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
            if not part:
                continue
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                pdf.set_font(fam, base_bold, size)
                pdf.write(lh, part[2:-2])
            elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                inner = part[1:-1]
                # Consolas 没有中文字形：行内代码里有中文时要用中文字体，否则出空白方块
                if mono and _ascii_only(inner):
                    pdf.set_font(mono_fam, "", size - 0.5)
                else:
                    if mono and not _ascii_only(inner):
                        stat["mono_cn_fallback"] += 1
                    pdf.set_font(fam, "", size - 0.5)
                pdf.write(lh, inner)
            else:
                pdf.set_font(fam, "", size)
                pdf.write(lh, part)

    def table_widths(rows, cols):
        weights = []
        for j in range(cols):
            longest = max((_dw(inline_plain(r[j])) for r in rows if j < len(r)), default=6)
            weights.append(min(max(longest, 6), 40))
        return weights

    for b in blocks:
        kind = b[0]
        if kind == "h":
            _, level, text = b
            size = {1: 17, 2: 13.5, 3: 11.5}.get(level, 10.5)
            pdf.ln(size * 0.28)
            pdf.set_text_color(20, 20, 20)
            pdf.set_font(fam, base_bold, size)
            pdf.multi_cell(0, size * 0.55, inline_plain(text))
            pdf.ln(1.0)
        elif kind == "p":
            pdf.set_text_color(0, 0, 0)
            inline(b[1])
            pdf.ln(lh)
            pdf.set_x(pdf.l_margin)
        elif kind == "quote":
            if b[1]:
                pdf.set_text_color(90, 90, 90)
                pdf.set_x(pdf.l_margin + 5)
                inline(b[1])
                pdf.ln(lh)
                pdf.set_x(pdf.l_margin)
                pdf.set_text_color(0, 0, 0)
        elif kind == "bullet":
            pdf.set_text_color(0, 0, 0)
            pdf.set_x(pdf.l_margin + 4)
            pdf.set_font(fam, "", body)
            pdf.write(lh, "· ")
            inline(b[1])
            pdf.ln(lh)
            pdf.set_x(pdf.l_margin)
        elif kind == "num":
            pdf.set_text_color(0, 0, 0)
            pdf.set_x(pdf.l_margin + 4)
            inline(b[1])
            pdf.ln(lh)
            pdf.set_x(pdf.l_margin)
        elif kind == "code":
            pdf.set_text_color(30, 30, 30)
            pdf.set_fill_color(246, 247, 249)
            pdf.set_font(mono_fam if mono else fam, "", 8.5)
            for line in b[1]:
                pdf.multi_cell(0, 4.6, inline_plain(line), fill=True)
            pdf.ln(1.5)
            pdf.set_text_color(0, 0, 0)
        elif kind == "hr":
            pdf.set_draw_color(200, 200, 200)
            y = pdf.get_y() + 1
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(3.5)
        elif kind == "table":
            rows = b[1]
            cols = max(len(r) for r in rows)
            head_style = (FontFace(emphasis="BOLD", size_pt=8.5, fill_color=(236, 240, 245))
                          if base_bold else FontFace(size_pt=8.5, fill_color=(236, 240, 245)))
            pdf.set_text_color(0, 0, 0)
            pdf.set_font(fam, "", 8.5)
            with pdf.table(col_widths=table_widths(rows, cols), text_align="LEFT",
                           line_height=4.4, padding=1.5, borders_layout="ALL",
                           first_row_as_headings=True, headings_style=head_style) as table:
                for r in rows:
                    row = table.row()
                    for j in range(cols):
                        row.cell(inline_plain(r[j]) if j < len(r) else "")
            pdf.ln(2.5)

    pdf.output(path)
    return stat


def raster_pdf(text, pdf_path):
    """最后兜底：把纯文本画成图片版 PDF（能看，但文字不可选中、搜不到）"""
    from PIL import Image, ImageDraw, ImageFont

    font_path = "C:\\Windows\\Fonts\\msyh.ttc"
    if not os.path.exists(font_path):
        font_path = "C:\\Windows\\Fonts\\simhei.ttf"
    font = ImageFont.truetype(font_path, 26)
    W, H = 1654, 2339  # A4 @150dpi
    margin, line_h = 120, 42
    page = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(page)
    pages, y = [], margin
    max_w = W - 2 * margin

    def wrap(s):
        lines, cur = [], ""
        for ch in s:
            if font.getlength(cur + ch) > max_w:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
        return lines

    for raw in text.split("\n"):
        for seg in (wrap(raw) if raw.strip() else [""]):
            if y + line_h > H - margin:
                pages.append(page)
                page = Image.new("RGB", (W, H), "white")
                draw = ImageDraw.Draw(page)
                y = margin
            draw.text((margin, y), seg, fill="black", font=font)
            y += line_h
    pages.append(page)
    pages[0].save(pdf_path, "PDF", resolution=150.0, save_all=True,
                  append_images=pages[1:])
    return len(pages)


# --------------------------------------------------------------------------
# docx（赠品：可编辑版本，没装 python-docx 就跳过，不算失败）
# --------------------------------------------------------------------------
def _east_asian(run, font_name):
    from docx.oxml.ns import qn
    run.font.name = font_name
    rFonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), font_name)
    rFonts.set(qn("w:hAnsi"), font_name)
    rFonts.set(qn("w:eastAsia"), font_name)


def _add_inline(paragraph, text, size=None):
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            run = paragraph.add_run(part[2:-2])
            run.bold = True
            _east_asian(run, CN_FONT)
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            run = paragraph.add_run(part[1:-1])
            # Word 里也照同样规矩：代码里有中文就别用等宽西文字体
            _east_asian(run, MONO_FONT if _ascii_only(part[1:-1]) else CN_FONT)
        else:
            run = paragraph.add_run(part)
            _east_asian(run, CN_FONT)
        if size:
            run.font.size = size


def _repeat_header(row):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(el)


def _bottom_border(paragraph):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "BBBBBB")
    pBdr.append(bottom)
    paragraph._p.get_or_add_pPr().append(pBdr)


def _page_number_footer(section):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.font.size = Pt(8.5)
    run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for el in (begin, instr, end):
        run._r.append(el)


def build_docx(blocks, path):
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = sec.bottom_margin = Cm(2.0)
    sec.left_margin = sec.right_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name = CN_FONT
    normal.font.size = Pt(10.5)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), CN_FONT)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.25
    for name, size, shade in (("Heading 1", 18, 0x11), ("Heading 2", 14, 0x22),
                              ("Heading 3", 12, 0x33), ("Heading 4", 11, 0x44)):
        st = doc.styles[name]
        st.font.name = CN_FONT
        st.font.size = Pt(size)
        st.font.color.rgb = RGBColor(shade, shade, shade)
        st.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), CN_FONT)
    _page_number_footer(sec)

    for b in blocks:
        kind = b[0]
        if kind == "h":
            _, level, text = b
            doc.add_heading(inline_plain(text), level=min(level, 4))
        elif kind == "p":
            _add_inline(doc.add_paragraph(), b[1])
        elif kind == "quote":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.5)
            if b[1]:
                _add_inline(p, b[1])
                for r in p.runs:
                    r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
        elif kind == "bullet":
            p = doc.add_paragraph(style="List Bullet")
            p.paragraph_format.space_after = Pt(2)
            _add_inline(p, b[1])
        elif kind == "num":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.75)
            p.paragraph_format.space_after = Pt(2)
            _add_inline(p, b[1])
        elif kind == "code":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.5)
            p.paragraph_format.space_after = Pt(0)
            for k, line in enumerate(b[1]):
                if k:
                    p.add_run().add_break()
                run = p.add_run(line)
                _east_asian(run, MONO_FONT)
                run.font.size = Pt(9)
        elif kind == "hr":
            _bottom_border(doc.add_paragraph())
        elif kind == "table":
            rows = b[1]
            cols = max(len(r) for r in rows)
            table = doc.add_table(rows=0, cols=cols)
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            table.autofit = False
            weights = []
            for j in range(cols):
                longest = max((_dw(inline_plain(r[j])) for r in rows if j < len(r)), default=6)
                weights.append(min(max(longest, 6), 40))
            total = sum(weights) or 1
            widths = [17.0 * w / total for w in weights]
            for ri, r in enumerate(rows):
                cells = table.add_row().cells
                for j in range(cols):
                    cells[j].width = Cm(widths[j])
                    para = cells[j].paragraphs[0]
                    para.paragraph_format.space_after = Pt(1)
                    _add_inline(para, inline_plain(r[j]) if j < len(r) else "", size=Pt(9))
                    if ri == 0:
                        for run in para.runs:
                            run.bold = True
            if table.rows:
                _repeat_header(table.rows[0])
            doc.add_paragraph()

    doc.save(path)
    return doc


# --------------------------------------------------------------------------
def pdf_pages_text(pdf_path):
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    return len(reader.pages), text


def main():
    ok = True
    log("=" * 70)
    log("答辩材料导出（md -> txt / pdf，另赠 docx）")
    log("=" * 70)

    if not os.path.exists(SRC):
        log(f"✗ 源文件不存在：{SRC}")
        write_report(False)
        return 1

    with open(SRC, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")
    blocks = parse_blocks(lines)
    headings = [inline_plain(b[2]) for b in blocks if b[0] == "h"]
    log(f"\n解析：{len(lines)} 行 -> {len(blocks)} 个块"
        f"（标题 {len(headings)}、表格 {sum(1 for b in blocks if b[0] == 'table')}、"
        f"列表 {sum(1 for b in blocks if b[0] in ('bullet', 'num'))}）")

    # --- txt ---
    txt = render_txt(blocks)
    with open(TXT, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    bad_md = [m for m in ("**", "```", "\n| ") if m in txt]
    # 标题有没有漏转：拿源文件里的标题原文去查，避免把表格里 "#" 这一格误判成漏洞
    leftover = [h for h in headings
                if re.search(r"(?m)^#{1,6}\s+" + re.escape(h) + r"\s*$", txt)]
    missing = [k for k in KEYWORDS if k not in txt]
    ok_txt = (len(txt) >= 8000) and not bad_md and not leftover and not missing
    ok = ok and ok_txt
    log(f"\n[txt] {os.path.basename(TXT)}  {len(txt)} 字符 / {txt.count(chr(10)) + 1} 行")
    log(f"      残留 Markdown 记号：{bad_md or '无'}；漏转标题：{leftover or '无'}")
    log(f"      关键内容缺失：{missing or '无'}")
    log(f"      结论：{'通过' if ok_txt else '不通过'}")

    # --- pdf ---
    ok_pdf, ok_glyph = False, False
    how, chosen_font = "未生成", "-"
    if os.path.exists(PDF):
        os.remove(PDF)
    try:
        stats = render_pdf(blocks, PDF)
        chosen_font = stats["font"]
        how = (f"fpdf2 文字版（字体 {os.path.basename(chosen_font)}；行内代码里的中文"
               f"改用中文字体 {stats['mono_cn_fallback']} 处，避免空白方块）")
    except Exception:
        log("\n[pdf] fpdf2 失败：\n" + traceback.format_exc())
        try:
            n = raster_pdf(txt, PDF)
            how = f"PIL 图片版兜底（{n} 页，文字不可选中）"
        except Exception:
            log("\n[pdf] 图片版兜底也失败：\n" + traceback.format_exc())
    if os.path.exists(PDF):
        try:
            pages, text = pdf_pages_text(PDF)
            found = [k for k in KEYWORDS if k in text]
            selectable = len(found) == len(KEYWORDS)
            missing_g = missing_glyphs(text or txt, chosen_font) if chosen_font != "-" else []
            ok_glyph = not missing_g
            ok_pdf = pages >= 4 and selectable
            log(f"\n[pdf] {os.path.basename(PDF)}  {pages} 页 / {os.path.getsize(PDF)} 字节")
            log(f"      生成方式：{how}")
            log(f"      抽出文字 {len(text)} 字符，关键内容命中 {len(found)}/{len(KEYWORDS)}"
                f"（{'可选中可搜索' if selectable else '抽不出文字，是图片版'}）")
            log(f"      字体缺字：{missing_g or '无'}（正文/标题字体必须覆盖全部中文字形）")
            log(f"      结论：{'通过' if ok_pdf and ok_glyph else '不通过'}")
        except Exception:
            log("\n[pdf] 读不回来：\n" + traceback.format_exc())
    ok = ok and ok_pdf and ok_glyph

    # --- docx（赠品，缺依赖就跳过）---
    ok_docx, note_docx = False, ""
    try:
        build_docx(blocks, DOCX)
        from docx import Document
        d = Document(DOCX)
        ok_docx = len(d.paragraphs) > 150 and len(d.tables) >= 10
        note_docx = f"段落 {len(d.paragraphs)} / 表格 {len(d.tables)}"
    except ImportError:
        note_docx = "跳过（没装 python-docx）"
    except Exception:
        note_docx = "生成失败：\n" + traceback.format_exc()
    log(f"\n[docx] {os.path.basename(DOCX)}  {note_docx}")
    log(f"      结论：{'通过' if ok_docx else ('跳过' if '跳过' in note_docx else '不通过')}")

    log("\n" + "=" * 70)
    log("结论")
    log("=" * 70)
    log(f"  txt 导出      {'通过' if ok_txt else '不通过'}")
    log(f"  pdf 导出      {'通过' if (ok_pdf and ok_glyph) else '不通过'}")
    log(f"  docx 赠品     {'通过' if ok_docx else ('跳过' if '跳过' in note_docx else '不通过')}")
    log(f"\n  {'全部通过' if ok else '有项目不通过'}")
    write_report(ok)
    return 0 if ok else 1


def write_report(ok):
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(LOG) + "\n")


if __name__ == "__main__":
    sys.exit(main())
