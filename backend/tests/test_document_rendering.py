from docx import Document
from pypdf import PdfReader

from app.services import document_service as ds


SAMPLE = """\
## 1. Pendahuluan

Ini adalah paragraf pembuka dengan **teks tebal** dan *teks miring*.

## 2. Pembahasan

### 2.1 Penerapan AI

Daftar sektor penerapan:

1. E-commerce
2. Kesehatan
3. Pendidikan

---

## 3. Kesimpulan

AI dapat membantu berbagai sektor di Indonesia.
"""


def test_parser_recognizes_headings_lists_and_rules():
    blocks = ds.parse_blocks(SAMPLE)

    assert ("h2", "1. Pendahuluan") in blocks
    assert ("h2", "2. Pembahasan") in blocks
    assert ("h3", "2.1 Penerapan AI") in blocks
    assert ("number", "E-commerce") in blocks
    assert ("number", "Kesehatan") in blocks
    assert ("number", "Pendidikan") in blocks
    assert ("rule", "") in blocks


def test_pdf_renderer_preserves_content_numbering_and_footer(tmp_path):
    output = tmp_path / "laporan.pdf"

    ds._build_pdf("Laporan Pengujian", SAMPLE, output)

    reader = PdfReader(str(output))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert len(reader.pages) >= 1
    assert "Laporan Pengujian" in text
    assert "1. Pendahuluan" in text
    assert "2. Pembahasan" in text
    assert "2.1 Penerapan AI" in text
    assert "E-commerce" in text
    assert "Kesehatan" in text
    assert "Pendidikan" in text
    assert "Halaman 1" in text
    assert "─" not in text
    assert "---" not in text


def test_docx_renderer_preserves_numbering_formatting_and_footer(tmp_path):
    output = tmp_path / "laporan.docx"

    ds._build_docx("Laporan Pengujian", SAMPLE, output)

    doc = Document(str(output))
    paragraphs = [p.text for p in doc.paragraphs]

    assert "1. Pendahuluan" in paragraphs
    assert "2. Pembahasan" in paragraphs
    assert "2.1 Penerapan AI" in paragraphs
    assert "1. E-commerce" in paragraphs
    assert "2. Kesehatan" in paragraphs
    assert "3. Pendidikan" in paragraphs

    bold_runs = [
        run for paragraph in doc.paragraphs for run in paragraph.runs
        if run.text == "teks tebal"
    ]
    italic_runs = [
        run for paragraph in doc.paragraphs for run in paragraph.runs
        if run.text == "teks miring"
    ]

    assert bold_runs and bold_runs[0].bold is True
    assert italic_runs and italic_runs[0].italic is True

    footer_xml = doc.sections[0].footer._element.xml
    assert 'PAGE' in footer_xml
    assert "ONE MASTER AI" in footer_xml


def test_pdf_renderer_handles_long_document(tmp_path):
    output = tmp_path / "laporan-panjang.pdf"
    body = "\n\n".join(
        f"## Bagian {i}\n\n"
        + ("Isi pengujian dokumen panjang untuk memeriksa pemenggalan halaman. " * 8)
        for i in range(1, 36)
    )

    ds._build_pdf("Laporan Panjang", body, output)

    reader = PdfReader(str(output))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert len(reader.pages) >= 2
    assert "Bagian 1" in text
    assert "Bagian 35" in text
    assert f"Halaman {len(reader.pages)}" in text


def test_pptx_renderer_preserves_numbering_headings_and_content(tmp_path):
    from pptx import Presentation

    output = tmp_path / "presentasi.pptx"
    pptx_sample = SAMPLE + "\n- Efisiensi\n- Produktivitas\n"
    ds._build_pptx("Presentasi Pengujian", pptx_sample, output)
    presentation = Presentation(str(output))
    texts = [
        paragraph.text
        for slide in presentation.slides
        for shape in slide.shapes if shape.has_text_frame
        for paragraph in shape.text_frame.paragraphs
    ]
    joined = "\n".join(texts)

    assert len(presentation.slides) >= 2
    assert "Presentasi Pengujian" in joined
    assert "1.  E-commerce" in joined
    assert "2.  Kesehatan" in joined
    assert "3.  Pendidikan" in joined
    assert "2.1 Penerapan AI" in joined
    assert "•  " in joined
    assert "AI dapat membantu berbagai sektor di Indonesia." in joined


def test_xlsx_renderer_applies_readable_styles_and_numbering(tmp_path):
    from openpyxl import load_workbook

    output = tmp_path / "laporan.xlsx"
    ds._build_xlsx("Laporan Pengujian", SAMPLE, output)
    workbook = load_workbook(output)
    sheet = workbook["Hasil AI"]

    assert sheet["A1"].value == "Laporan Pengujian"
    assert sheet["A1"].font.bold is True
    assert sheet["A4"].value == "Jenis"
    assert sheet["A4"].font.bold is True
    assert sheet["A4"].fill.fgColor.rgb not in (None, "00000000")
    assert sheet["B4"].alignment.horizontal == "center"
    assert sheet.freeze_panes == "A5"
    assert sheet.auto_filter.ref == f"A4:B{sheet.max_row}"

    values = [sheet.cell(row, 2).value for row in range(5, sheet.max_row + 1)]
    assert "1. E-commerce" in values
    assert "2. Kesehatan" in values
    assert "3. Pendidikan" in values
    assert any(
        cell.alignment.wrap_text
        for row in sheet.iter_rows() for cell in row
    )


def test_pptx_numbering_continues_across_slides(tmp_path):
    from pptx import Presentation

    body = "## Daftar Pengujian\n\n"
    body += "\n".join(
        f"{i}. Item pengujian nomor {i} dengan uraian yang cukup panjang untuk menguji kapasitas slide."
        for i in range(1, 61)
    )

    output = tmp_path / "presentasi-panjang.pptx"
    ds._build_pptx("Uji Presentasi Panjang", body, output)
    presentation = Presentation(str(output))

    numbered = []
    for index in range(1, len(presentation.slides)):
        slide = presentation.slides[index]
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for paragraph in shape.text_frame.paragraphs:
                text = paragraph.text.strip()
                if ".  Item pengujian nomor " in text:
                    numbered.append(text)

    assert len(presentation.slides) >= 3
    assert len(numbered) == 60
    assert numbered == [
        f"{i}.  Item pengujian nomor {i} dengan uraian yang cukup panjang untuk menguji kapasitas slide."
        for i in range(1, 61)
    ]
