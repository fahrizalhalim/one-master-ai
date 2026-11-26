import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_WIT = timezone(timedelta(hours=9))
_SECTIONS = (
    "pendahuluan", "latar belakang", "rumusan masalah", "tujuan", "manfaat",
    "tinjauan pustaka", "landasan teori", "metode", "metodologi", "hasil",
    "pembahasan", "kesimpulan", "saran", "daftar pustaka", "referensi",
    "lampiran", "penutup", "abstrak",
)

def _now_label() -> str:
    return datetime.now(_WIT).strftime("%d/%m/%Y %H:%M WIT")

def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"): s = s[1:]
    if s.endswith("|"): s = s[:-1]
    return [c.strip() for c in s.split("|")]

def _is_sep(line: str) -> bool:
    s = line.strip()
    if "|" not in s: return False
    cells = _split_row(s)
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells)

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()

def _is_kv(line: str) -> bool:
    return bool(re.match(r"^(?:\*\*[^*]{1,40}:\*\*|[A-Za-z][A-Za-z0-9 /()-]{1,32}:)\s*", line.strip()))

def _strip_dup_title(title: str, blocks: list[tuple]) -> list[tuple]:
    if blocks and blocks[0][0] in ("title", "h2", "p") and _norm(blocks[0][1]) == _norm(title):
        return blocks[1:]
    return blocks

def parse_rich_blocks(text: str) -> list[tuple]:
    lines = str(text or "").splitlines()
    blocks, i, n = [], 0, len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s: i += 1; continue
        if s.startswith("```"):
            buf = []; i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            if i < n: i += 1
            if buf: blocks.append(("code", "\n".join(buf)))
            continue
        if "|" in s and i + 1 < n and _is_sep(lines[i + 1]):
            hdr = _split_row(s); i += 2; rows = [hdr]
            while i < n and "|" in lines[i] and lines[i].strip():
                r = _split_row(lines[i])
                if len(r) < len(hdr): r += [""] * (len(hdr) - len(r))
                rows.append(r[:len(hdr)]); i += 1
            blocks.append(("table", rows)); continue
        if re.fullmatch(r"[-*_]{3,}", s):
            blocks.append(("rule", "")); i += 1; continue
        if s.startswith(">"):
            q = []
            while i < n and lines[i].strip().startswith(">"):
                q.append(re.sub(r"^>\s?", "", lines[i].strip())); i += 1
            blocks.append(("quote", " ".join(q).strip())); continue
        mh = re.match(r"^(#{1,6})\s+(.+)$", s)
        if mh:
            lv = len(mh.group(1))
            blocks.append(("title" if lv == 1 else ("h2" if lv == 2 else "h3"), mh.group(2).strip()))
            i += 1; continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        mnh = re.match(r"^(\d+(?:\.\d+)*)(?:[.)])?\s+(.+)$", s)
        if mnh and indent == 0:
            num, lbl = mnh.group(1), mnh.group(2).strip()
            lo = lbl.lower().rstrip(":.")
            if "." in num or any(lo == k or lo.startswith(k + " ") for k in _SECTIONS):
                blocks.append(("h2" if num.count(".") == 0 else "h3", f"{num}. {lbl}"))
                i += 1; continue
        if re.match(r"^\d+[.)]\s+", s):
            items = []
            while i < n and re.match(r"^\s*\d+[.)]\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+[.)]\s+", "", lines[i]).strip()); i += 1
            blocks.append(("ol", items)); continue
        if re.match(r"^[-*•]\s+", s):
            items = []
            while i < n and re.match(r"^\s*[-*•]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*•]\s+", "", lines[i]).strip()); i += 1
            blocks.append(("ul", items)); continue
        if _is_kv(s):
            blocks.append(("p", s)); i += 1; continue
        para = [s]; prev_br = raw.endswith("  "); i += 1
        while i < n and not prev_br and lines[i].strip():
            nxt = lines[i].strip()
            if re.match(r"^(#{1,6}\s|[-*•]\s|\d+[.)]\s|>|```|[-*_]{3,}$)", nxt) or _is_kv(nxt): break
            if "|" in nxt and i + 1 < n and _is_sep(lines[i + 1]): break
            para.append(nxt); prev_br = lines[i].endswith("  "); i += 1
        blocks.append(("p", " ".join(para)))
    return blocks

def detect_orientation(title: str, body_text: str, blocks: list[tuple], for_xlsx: bool = False) -> str:
    t = _norm(title)
    if any(k in t for k in ("landscape", "lanskap", "matriks", "rekap", "jadwal")):
        return "landscape"
    for kind, content in blocks:
        if kind == "table" and content:
            cols = len(content[0])
            if cols >= (7 if for_xlsx else 6): return "landscape"
            max_w = sum(max((len(str(r[c])) for r in content), default=0) for c in range(cols))
            if max_w >= (92 if for_xlsx else 78): return "landscape"
    return "portrait"

def _docx_runs(p, text: str, size=None, color=None, bold=False):
    from docx.shared import Pt
    for tok in re.split(r"(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`)", str(text or "")):
        if not tok: continue
        if tok.startswith("**") and tok.endswith("**"):
            r = p.add_run(tok[2:-2]); r.bold = True
        elif tok.startswith("*") and tok.endswith("*"):
            r = p.add_run(tok[1:-1]); r.italic = True; r.bold = bold
        elif tok.startswith("`") and tok.endswith("`"):
            r = p.add_run(tok[1:-1]); r.font.name = "Courier New"
            r.font.size = Pt((size or 10.5) - 1)
        else:
            r = p.add_run(tok); r.bold = bold
        if size and not (tok.startswith("`") and tok.endswith("`")): r.font.size = Pt(size)
        if color: r.font.color.rgb = color

def _shade_cell(cell, hex_color: str) -> None:
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    cell._tc.get_or_add_tcPr().append(parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>'))

def _add_page_footer(footer, muted) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r0 = p.add_run("Halaman "); r0.font.size = Pt(8.5); r0.font.color.rgb = muted
    for part in ("begin", "instr", "separate", "end"):
        r = p.add_run(); r.font.size = Pt(8.5); r.font.color.rgb = muted
        el = OxmlElement("w:instrText" if part == "instr" else "w:fldChar")
        if part == "instr":
            el.set(qn("xml:space"), "preserve"); el.text = " PAGE "
        else:
            el.set(qn("w:fldCharType"), part)
        r._r.append(el)

def build_docx_print_ready(title: str, body_text: str, path: Path) -> None:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Cm, Pt, RGBColor

    blocks = _strip_dup_title(title, parse_rich_blocks(body_text))
    orient = detect_orientation(title, body_text, blocks)
    doc = Document()
    sec = doc.sections[0]
    if orient == "landscape":
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = Cm(29.7), Cm(21.0)
        sec.top_margin, sec.bottom_margin = Cm(1.8), Cm(1.8)
        sec.left_margin, sec.right_margin = Cm(2.0), Cm(2.0)
    else:
        sec.orientation = WD_ORIENT.PORTRAIT
        sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
        sec.top_margin, sec.bottom_margin = Cm(2.2), Cm(2.2)
        sec.left_margin, sec.right_margin = Cm(2.5), Cm(2.2)

    navy, slate, muted = RGBColor(23, 37, 84), RGBColor(30, 64, 175), RGBColor(100, 116, 139)
    white = RGBColor(255, 255, 255)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"; normal.font.size = Pt(10.5)
    normal.paragraph_format.line_spacing = 1.15; normal.paragraph_format.space_after = Pt(5)
    _add_page_footer(sec.footer, muted)

    hp = doc.add_paragraph(); hp.paragraph_format.space_after = Pt(3)
    hr = hp.add_run(title); hr.bold = True; hr.font.size = Pt(17); hr.font.color.rgb = navy
    mp = doc.add_paragraph(); mp.paragraph_format.space_after = Pt(10)
    mr = mp.add_run(f"Dihasilkan oleh ONE MASTER AI  •  {_now_label()}")
    mr.font.size = Pt(8.5); mr.font.color.rgb = muted

    for kind, content in blocks:
        if kind in ("title", "h2", "h3"):
            doc.add_heading(content, level=1 if kind == "title" else (2 if kind == "h2" else 3))
        elif kind == "p":
            p = doc.add_paragraph(); _docx_runs(p, content)
        elif kind == "quote":
            p = doc.add_paragraph(); p.paragraph_format.left_indent = Cm(0.8)
            _docx_runs(p, content, color=slate)
        elif kind == "code":
            p = doc.add_paragraph(); r = p.add_run(content)
            r.font.name = "Courier New"; r.font.size = Pt(9)
        elif kind == "ul":
            for item in content:
                p = doc.add_paragraph(style="List Bullet")
                p.paragraph_format.space_after = Pt(3); _docx_runs(p, item)
        elif kind == "ol":
            for idx, item in enumerate(content, start=1):
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.75)
                p.paragraph_format.first_line_indent = Cm(-0.5)
                p.paragraph_format.space_after = Pt(3)
                _docx_runs(p, f"{idx}. {item}")
        elif kind == "table" and content:
            tbl = doc.add_table(rows=len(content), cols=len(content[0]))
            tbl.alignment = WD_TABLE_ALIGNMENT.CENTER; tbl.style = "Table Grid"
            for r_idx, row in enumerate(content):
                for c_idx, val in enumerate(row):
                    cell = tbl.cell(r_idx, c_idx)
                    if r_idx == 0:
                        _shade_cell(cell, "172554")
                        _docx_runs(cell.paragraphs[0], val, size=9.5, color=white, bold=True)
                    else:
                        if r_idx % 2 == 0: _shade_cell(cell, "F8FAFC")
                        _docx_runs(cell.paragraphs[0], val, size=9.5)
    doc.save(str(path))

def _pdf_markup(text: str) -> str:
    from xml.sax.saxutils import escape
    s = escape(str(text or ""))
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\*([^*\n]+)\*", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r'<font name="Courier">\1</font>', s)
    return s

def build_pdf_print_ready(title: str, body_text: str, path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape, portrait
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

    blocks = _strip_dup_title(title, parse_rich_blocks(body_text))
    orient = detect_orientation(title, body_text, blocks)
    pagesize = landscape(A4) if orient == "landscape" else portrait(A4)
    mx = 1.8 * cm if orient == "landscape" else 2.2 * cm
    doc = SimpleDocTemplate(
        str(path), pagesize=pagesize, leftMargin=mx, rightMargin=mx,
        topMargin=1.8 * cm, bottomMargin=2.0 * cm, title=title,
    )
    base = getSampleStyleSheet()
    st_t = ParagraphStyle("PT", parent=base["Title"], fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=colors.HexColor("#172554"), alignment=0, spaceAfter=4)
    st_m = ParagraphStyle("PM", parent=base["Normal"], fontSize=8.5, textColor=colors.HexColor("#64748B"), spaceAfter=12)
    st_h2 = ParagraphStyle("PH2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=colors.HexColor("#1E40AF"), spaceBefore=10, spaceAfter=4, keepWithNext=True)
    st_h3 = ParagraphStyle("PH3", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=colors.HexColor("#1E293B"), spaceBefore=8, spaceAfter=3, keepWithNext=True)
    st_b = ParagraphStyle("PB", parent=base["Normal"], fontSize=10, leading=14.5, spaceAfter=5)
    st_l = ParagraphStyle("PL", parent=st_b, leftIndent=14, spaceAfter=3)
    st_q = ParagraphStyle("PQ", parent=st_b, leftIndent=14, textColor=colors.HexColor("#1E40AF"))
    st_c = ParagraphStyle("PC", parent=base["Code"], fontName="Courier", fontSize=8.5, leading=11.5, backColor=colors.HexColor("#F1F5F9"), leftIndent=8, rightIndent=8)
    st_cell = ParagraphStyle("PCell", parent=base["Normal"], fontSize=8.5, leading=11.5)
    st_hdr = ParagraphStyle("PHdr", parent=st_cell, fontName="Helvetica-Bold", textColor=colors.white)
    story = [
        Paragraph(_pdf_markup(title), st_t),
        Paragraph(f"Dihasilkan oleh ONE MASTER AI  •  {_now_label()}", st_m),
    ]
    for kind, content in blocks:
        if kind in ("title", "h2"): story.append(Paragraph(_pdf_markup(content), st_h2))
        elif kind == "h3": story.append(Paragraph(_pdf_markup(content), st_h3))
        elif kind == "p": story.append(Paragraph(_pdf_markup(content), st_b))
        elif kind == "quote": story.append(Paragraph(_pdf_markup(content), st_q))
        elif kind == "code": story.extend([Preformatted(content, st_c), Spacer(1, 6)])
        elif kind == "ul": story.extend(Paragraph(f"•  {_pdf_markup(x)}", st_l) for x in content)
        elif kind == "ol": story.extend(Paragraph(f"{idx}.  {_pdf_markup(x)}", st_l) for idx, x in enumerate(content, 1))
        elif kind == "table" and content:
            cols = len(content[0])
            w = [max(6, min(36, max((len(str(r[c])) for r in content), default=6))) for c in range(cols)]
            data = [[Paragraph(_pdf_markup(v), st_hdr if r == 0 else st_cell) for v in row] for r, row in enumerate(content)]
            t = Table(data, colWidths=[doc.width * (x / sum(w)) for x in w], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172554")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.extend([t, Spacer(1, 8)])

    def _footer(canvas, d):
        canvas.saveState(); canvas.setFont("Helvetica", 8); canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawRightString(pagesize[0] - mx, 1.0 * cm, f"Halaman {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)

def build_xlsx_print_ready(title: str, body_text: str, path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    blocks = _strip_dup_title(title, parse_rich_blocks(body_text))
    orient = detect_orientation(title, body_text, blocks, for_xlsx=True)
    wb = Workbook(); ws = wb.active; ws.title = "Dokumen"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE if orient == "landscape" else ws.ORIENTATION_PORTRAIT
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0

    thin = Side(style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill = PatternFill("solid", fgColor="172554")
    alt_fill = PatternFill("solid", fgColor="F8FAFC")

    row_i = 1
    ws.cell(row_i, 1, title).font = Font(name="Calibri", size=14, bold=True, color="172554"); row_i += 1
    ws.cell(row_i, 1, f"Dihasilkan oleh ONE MASTER AI • {_now_label()}").font = Font(name="Calibri", size=9, color="64748B"); row_i += 2

    for kind, content in blocks:
        if kind in ("title", "h2", "h3"):
            ws.cell(row_i, 1, content).font = Font(name="Calibri", size=11, bold=True, color="1E40AF"); row_i += 1
        elif kind in ("p", "quote", "code"):
            c = ws.cell(row_i, 1, content); c.alignment = Alignment(wrap_text=True, vertical="top"); row_i += 1
        elif kind == "ul":
            for item in content:
                c = ws.cell(row_i, 1, f"• {item}"); c.alignment = Alignment(wrap_text=True, vertical="top"); row_i += 1
        elif kind == "ol":
            for idx, item in enumerate(content, start=1):
                c = ws.cell(row_i, 1, f"{idx}. {item}"); c.alignment = Alignment(wrap_text=True, vertical="top"); row_i += 1
        elif kind == "table" and content:
            for r_idx, r_vals in enumerate(content):
                for c_idx, val in enumerate(r_vals, start=1):
                    cell = ws.cell(row_i, c_idx, val)
                    cell.border = border; cell.alignment = Alignment(wrap_text=True, vertical="top")
                    if r_idx == 0:
                        cell.fill = hdr_fill; cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
                    elif r_idx % 2 == 0:
                        cell.fill = alt_fill
                row_i += 1
            row_i += 1

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[get_column_letter(col[0].column)].width = max(12, min(42, max_len + 3))
    wb.save(str(path))
