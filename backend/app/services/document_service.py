"""Generator dokumen DOCX & PDF dari jawaban Master AI.

Menerima teks markdown sederhana dari AI lalu merapikannya menjadi:
- DOCX  : heading, bullet/numbered list, paragraf (python-docx)
- PDF   : paragraf + heading sederhana (ReportLab, font bawaan Helvetica)

Parser markdown ringan: baris yang diawali '# ' -> judul, '## ' -> subjudul,
'- ' atau '* ' -> bullet, angka '1. ' -> item bernomor.
"""

import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Pt
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

STORAGE_DIR = Path(
    os.environ.get("DOC_STORAGE", Path(__file__).resolve().parent.parent / "storage")
)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)


# ---------- Parser markdown ringan ----------

def parse_blocks(text: str):
    """Ubah teks markdown sederhana menjadi list blok (kind, content)."""
    blocks = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # buat penanda markdown **bold** dan `code` jadi teks polos
        clean = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        clean = re.sub(r"`(.+?)`", r"\1", clean)

        if clean.startswith("# "):
            blocks.append(("title", clean[2:].strip()))
        elif clean.startswith("## "):
            blocks.append(("h2", clean[3:].strip()))
        elif clean.startswith("### "):
            blocks.append(("h3", clean[4:].strip()))
        elif re.match(r"^[-*]\s+", clean):
            blocks.append(("bullet", re.sub(r"^[-*]\s+", "", clean)))
        elif re.match(r"^\d+[.)]\s+", clean):
            blocks.append(("number", re.sub(r"^\d+[.)]\s+", "", clean)))
        else:
            blocks.append(("para", clean))
    return blocks


def _safe_filename(title: str) -> str:
    base = re.sub(r"[^\w\- ]", "", title).strip().replace(" ", "_")[:60]
    return base or "dokumen"


# ---------- DOCX ----------

def create_docx(title: str, body_text: str) -> Path:
    doc = Document()

    doc.add_heading(title, level=0)

    # metadata kecil
    meta = doc.add_paragraph()
    run = meta.add_run(
        f"Dihasilkan oleh ONE MASTER AI — {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    run.font.size = Pt(9)
    run.font.italic = True

    for kind, content in parse_blocks(body_text):
        if kind == "title":
            doc.add_heading(content, level=1)
        elif kind == "h2":
            doc.add_heading(content, level=2)
        elif kind == "h3":
            doc.add_heading(content, level=3)
        elif kind == "bullet":
            doc.add_paragraph(content, style="List Bullet")
        elif kind == "number":
            doc.add_paragraph(content, style="List Number")
        else:
            doc.add_paragraph(content)

    path = STORAGE_DIR / f"{_safe_filename(title)}_{uuid.uuid4().hex[:8]}.docx"
    doc.save(str(path))
    return path


# ---------- PDF ----------

def create_pdf(title: str, body_text: str) -> Path:
    styles = {
        "doc_title": ParagraphStyle(
            "doc_title", fontSize=18, leading=22, spaceAfter=12
        ),
        "title": ParagraphStyle("title", fontSize=14, leading=18, spaceBefore=10, spaceAfter=6),
        "h2": ParagraphStyle("h2", fontSize=12, leading=16, spaceBefore=8, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontSize=11, leading=14, spaceBefore=6, spaceAfter=3),
        "para": ParagraphStyle("para", fontSize=10, leading=14, spaceAfter=6),
        "bullet": ParagraphStyle("bullet", fontSize=10, leading=14, leftIndent=14, spaceAfter=3),
        "number": ParagraphStyle("number", fontSize=10, leading=14, leftIndent=14, spaceAfter=3),
        "meta": ParagraphStyle("meta", fontSize=8, leading=10, textColor="#666666", spaceAfter=10),
    }

    path = STORAGE_DIR / f"{_safe_filename(title)}_{uuid.uuid4().hex[:8]}.pdf"
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    story = [Paragraph(title, styles["doc_title"])]
    story.append(
        Paragraph(
            f"Dihasilkan oleh ONE MASTER AI — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            styles["meta"],
        )
    )

    counters = {"number": 0}
    for kind, content in parse_blocks(body_text):
        if kind == "number":
            counters["number"] += 1
            story.append(Paragraph(f"{counters['number']}. {content}", styles["number"]))
            continue
        counters["number"] = 0
        if kind == "title":
            story.append(Paragraph(content, styles["title"]))
        elif kind == "h2":
            story.append(Paragraph(content, styles["h2"]))
        elif kind == "h3":
            story.append(Paragraph(content, styles["h3"]))
        elif kind == "bullet":
            story.append(Paragraph(f"• {content}", styles["bullet"]))
        else:
            story.append(Paragraph(content, styles["para"]))
            story.append(Spacer(1, 2))

    doc.build(story)
    return path


def resolve_stored_file(filename: str) -> Path | None:
    """Ambil file dari storage — cegah path traversal."""
    safe = Path(filename).name  # buang direktori/..
    path = STORAGE_DIR / safe
    if path.exists() and path.parent == STORAGE_DIR:
        return path
    return None
