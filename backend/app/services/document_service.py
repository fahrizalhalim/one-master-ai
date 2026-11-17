"""Generator 12 format file dari jawaban ONE MASTER AI."""

import os
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches, Pt as PPTPt
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from PIL import Image, ImageDraw, ImageFont


STORAGE_DIR = Path(
    os.environ.get("DOC_STORAGE", Path(__file__).resolve().parent.parent / "storage")
)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_FORMATS = {
    "pdf", "docx", "doc", "txt", "md",
    "xlsx", "xls", "pptx", "ppt",
    "jpg", "jpeg", "png",
}
LEGACY_FORMATS = {"doc", "xls", "ppt"}
NATIVE_FORMAT = {"doc": "docx", "xls": "xlsx", "ppt": "pptx"}


def parse_blocks(text: str):
    """Ubah markdown sederhana menjadi blok teks."""
    blocks = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
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
    base = re.sub(r"[^\w\- ]", "", str(title or ""), flags=re.UNICODE)
    base = re.sub(r"\s+", "_", base.strip())[:60]
    return base or "dokumen"


def _user_storage_dir(user_id: str) -> Path:
    """Direktori hasil AI yang terisolasi per pengguna."""
    from uuid import UUID

    try:
        safe_user_id = str(UUID(str(user_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("ID pengguna tidak valid.") from exc

    root = STORAGE_DIR / "generated"
    root.mkdir(parents=True, exist_ok=True)

    if root.is_symlink():
        raise ValueError("Direktori penyimpanan tidak valid.")

    base = root.resolve(strict=True)
    user_dir = base / safe_user_id

    if user_dir.is_symlink():
        raise ValueError("Direktori pengguna tidak valid.")

    user_dir.mkdir(parents=True, exist_ok=True)
    resolved = user_dir.resolve(strict=True)

    if resolved.parent != base or resolved != user_dir or not resolved.is_dir():
        raise ValueError("Direktori pengguna tidak valid.")

    return resolved


def _build_docx(title: str, body_text: str, path: Path) -> None:
    doc = Document()
    doc.add_heading(title, level=0)

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

    doc.save(str(path))


def _build_pdf(title: str, body_text: str, path: Path) -> None:
    styles = {
        "doc_title": ParagraphStyle("doc_title", fontSize=18, leading=22, spaceAfter=12),
        "title": ParagraphStyle("title", fontSize=14, leading=18, spaceBefore=10, spaceAfter=6),
        "h2": ParagraphStyle("h2", fontSize=12, leading=16, spaceBefore=8, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontSize=11, leading=14, spaceBefore=6, spaceAfter=3),
        "para": ParagraphStyle("para", fontSize=10, leading=14, spaceAfter=6),
        "bullet": ParagraphStyle("bullet", fontSize=10, leading=14, leftIndent=14, spaceAfter=3),
        "number": ParagraphStyle("number", fontSize=10, leading=14, leftIndent=14, spaceAfter=3),
        "meta": ParagraphStyle("meta", fontSize=8, leading=10, textColor="#666666", spaceAfter=10),
    }

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    story = [
        Paragraph(escape(title), styles["doc_title"]),
        Paragraph(
            escape(f"Dihasilkan oleh ONE MASTER AI — {datetime.now():%d/%m/%Y %H:%M}"),
            styles["meta"],
        ),
    ]

    counter = 0
    for kind, content in parse_blocks(body_text):
        safe = escape(content)
        if kind == "number":
            counter += 1
            story.append(Paragraph(f"{counter}. {safe}", styles["number"]))
        else:
            counter = 0
            if kind in {"title", "h2", "h3"}:
                story.append(Paragraph(safe, styles[kind]))
            elif kind == "bullet":
                story.append(Paragraph(f"&#8226; {safe}", styles["bullet"]))
            else:
                story.append(Paragraph(safe, styles["para"]))
                story.append(Spacer(1, 2))

    doc.build(story)


def _build_txt(title: str, body_text: str, path: Path, markdown: bool = False) -> None:
    content = str(body_text or "")
    if markdown and not content.lstrip().startswith("#"):
        content = f"# {title}\n\n{content}"
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _build_xlsx(title: str, body_text: str, path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Hasil AI"
    sheet.append([title])
    sheet.append(["Dihasilkan oleh ONE MASTER AI"])
    sheet.append([""])
    sheet.append(["Jenis", "Isi"])

    for kind, content in parse_blocks(body_text):
        sheet.append([kind, content])

    sheet.column_dimensions["A"].width = 18
    sheet.column_dimensions["B"].width = 100
    sheet.freeze_panes = "A5"
    workbook.save(path)


def _build_pptx(title: str, body_text: str, path: Path) -> None:
    presentation = Presentation()
    layout = presentation.slide_layouts[1]

    slide = presentation.slides.add_slide(layout)
    slide.shapes.title.text = title
    slide.placeholders[1].text = (
        f"Dihasilkan oleh ONE MASTER AI\n{datetime.now():%d/%m/%Y %H:%M}"
    )

    blocks = parse_blocks(body_text)
    chunks = [blocks[i:i + 7] for i in range(0, len(blocks), 7)] or [[]]

    for index, chunk in enumerate(chunks, start=1):
        slide = presentation.slides.add_slide(layout)
        slide.shapes.title.text = f"{title} — Bagian {index}"
        frame = slide.placeholders[1].text_frame
        frame.clear()

        for block_index, (kind, content) in enumerate(chunk):
            paragraph = frame.paragraphs[0] if block_index == 0 else frame.add_paragraph()
            prefix = "• " if kind == "bullet" else ""
            paragraph.text = prefix + content
            paragraph.level = 0
            paragraph.font.size = PPTPt(18 if kind in {"title", "h2"} else 14)

    presentation.save(path)


def _build_image(title: str, body_text: str, path: Path, image_format: str) -> None:
    """Render jawaban sebagai gambar teks; bukan ilustrasi generatif."""
    width = 1600
    margin = 90
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    try:
        title_font = ImageFont.truetype(bold_path, 42)
        body_font = ImageFont.truetype(font_path, 27)
    except OSError:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    image = Image.new("RGB", (width, 400), "white")
    draw = ImageDraw.Draw(image)
    y = margin
    draw.text((margin, y), title[:100], font=title_font, fill="#172554")
    y += 85
    draw.text(
        (margin, y),
        f"ONE MASTER AI  |  {datetime.now():%d/%m/%Y %H:%M}",
        font=body_font,
        fill="#64748b",
    )
    y += 65

    max_width = width - 2 * margin
    lines = []
    for raw in str(body_text or "").splitlines():
        words = raw.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=body_font)[2] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)

    line_height = 42
    height = max(500, y + len(lines) * line_height + margin)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((margin, margin), title[:100], font=title_font, fill="#172554")
    y = margin + 85
    draw.text(
        (margin, y),
        f"ONE MASTER AI  |  {datetime.now():%d/%m/%Y %H:%M}",
        font=body_font,
        fill="#64748b",
    )
    y += 65

    for line in lines:
        draw.text((margin, y), line, font=body_font, fill="#111827")
        y += line_height

    if image_format in {"jpg", "jpeg"}:
        image.save(path, format="JPEG", quality=92, optimize=True)
    else:
        image.save(path, format="PNG", optimize=True)


def _convert_legacy(source: Path, target_ext: str, output_dir: Path) -> Path:
    """Konversi ke format Office lama dengan LibreOffice headless."""
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        raise RuntimeError("LibreOffice belum tersedia pada lingkungan generator.")

    profile_dir = output_dir / f"lo-profile-{uuid.uuid4().hex}"
    command = [
        executable,
        f"-env:UserInstallation={profile_dir.as_uri()}",
        "--headless",
        "--convert-to", target_ext,
        "--outdir", str(output_dir),
        str(source),
    ]

    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=90,
        check=False,
        env={**os.environ, "HOME": str(output_dir)},
    )

    converted = output_dir / f"{source.stem}.{target_ext}"
    if result.returncode != 0 or not converted.is_file() or converted.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "")[:500]
        raise RuntimeError(f"Konversi LibreOffice gagal: {detail}")

    return converted


def create_document(
    title: str,
    body_text: str,
    user_id: str,
    file_format: str,
    file_id: str | None = None,
) -> Path:
    """Buat file asli dengan pemulihan retry berbasis manifest."""
    from uuid import UUID

    fmt = str(file_format or "").lower().lstrip(".")
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError("Format file tidak didukung.")

    content = str(body_text or "")
    if not content.strip():
        raise ValueError("Isi dokumen tidak boleh kosong.")

    normalized_user_id = str(UUID(str(user_id)))
    user_dir = _user_storage_dir(normalized_user_id)
    safe_title = _safe_filename(title)
    display_name = f"{safe_title}.{fmt}"

    explicit_id = file_id is not None
    if explicit_id:
        selected_id = str(file_id).lower()
        if not re.fullmatch(r"[0-9a-f]{12}", selected_id):
            raise ValueError("ID file tidak valid.")
    else:
        selected_id = uuid.uuid4().hex[:12]

    final_path = user_dir / f"{selected_id}.{fmt}"
    name_path = user_dir / f"{selected_id}.name.txt"
    extract_path = user_dir / f"{selected_id}.extract.txt"
    manifest_path = user_dir / f".{selected_id}.request.json"

    expected_manifest = {
        "user_id": normalized_user_id,
        "file_id": selected_id,
        "format": fmt,
        "title": display_name,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }

    def write_exclusive_or_verify(target: Path, expected: str) -> None:
        if target.is_symlink():
            raise FileExistsError("Metadata file tidak aman.")
        if target.exists():
            if not target.is_file() or target.read_text(encoding="utf-8") != expected:
                raise FileExistsError("Metadata file berbeda dari permintaan.")
            return
        try:
            with target.open("x", encoding="utf-8") as handle:
                handle.write(expected)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if target.is_symlink() or not target.is_file():
                raise FileExistsError("Metadata file tidak aman.")
            if target.read_text(encoding="utf-8") != expected:
                raise FileExistsError("Metadata file berbeda dari permintaan.")

    if explicit_id:
        if manifest_path.is_symlink():
            raise FileExistsError("Manifest file tidak aman.")

        if manifest_path.exists():
            if not manifest_path.is_file():
                raise FileExistsError("Manifest file tidak valid.")
            try:
                stored_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise FileExistsError("Manifest file rusak; perlu pemeriksaan manual.")
            if stored_manifest != expected_manifest:
                raise FileExistsError("ID file sudah digunakan untuk permintaan berbeda.")
        else:
            # Manifest dipublikasikan secara atomik agar tidak terbaca setengah jadi.
            fd, temporary_name = tempfile.mkstemp(
                prefix=".omai-manifest-", dir=user_dir
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(expected_manifest, handle, ensure_ascii=False, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary_path, manifest_path)
                except FileExistsError:
                    pass
            finally:
                temporary_path.unlink(missing_ok=True)

            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise FileExistsError("Manifest file tidak aman.")
            try:
                stored_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise FileExistsError("Manifest file rusak; perlu pemeriksaan manual.")
            if stored_manifest != expected_manifest:
                raise FileExistsError("ID file sudah digunakan untuk permintaan berbeda.")

        # Jika proses sebelumnya berhenti setelah file final dibuat,
        # manifest membuktikan permintaan asal sehingga metadata dapat dipulihkan.
        if final_path.is_symlink():
            raise FileExistsError("File hasil tidak aman.")
        if final_path.exists():
            if not final_path.is_file() or final_path.stat().st_size == 0:
                raise FileExistsError("File hasil tidak valid.")
            write_exclusive_or_verify(name_path, display_name)
            write_exclusive_or_verify(extract_path, content)
            return final_path

        # Metadata parsial hanya boleh dilanjutkan bila cocok dengan manifest.
        if name_path.exists() or extract_path.exists():
            write_exclusive_or_verify(name_path, display_name)
            write_exclusive_or_verify(extract_path, content)

    with tempfile.TemporaryDirectory(prefix=".omai-build-", dir=user_dir) as temp:
        temp_dir = Path(temp)
        if fmt in LEGACY_FORMATS:
            native_ext = NATIVE_FORMAT[fmt]
            source_path = temp_dir / f"{safe_title}.{native_ext}"
            if native_ext == "docx":
                _build_docx(title, content, source_path)
            elif native_ext == "xlsx":
                _build_xlsx(title, content, source_path)
            else:
                _build_pptx(title, content, source_path)
            generated = _convert_legacy(source_path, fmt, temp_dir)
        else:
            generated = temp_dir / f"{selected_id}.{fmt}"
            if fmt == "pdf":
                _build_pdf(title, content, generated)
            elif fmt == "docx":
                _build_docx(title, content, generated)
            elif fmt == "txt":
                _build_txt(title, content, generated)
            elif fmt == "md":
                _build_txt(title, content, generated, markdown=True)
            elif fmt == "xlsx":
                _build_xlsx(title, content, generated)
            elif fmt == "pptx":
                _build_pptx(title, content, generated)
            else:
                _build_image(title, content, generated, fmt)

        if not generated.is_file() or generated.stat().st_size == 0:
            raise RuntimeError("File hasil generator kosong atau tidak ditemukan.")

        with generated.open("rb") as source_file, final_path.open("xb") as target:
            shutil.copyfileobj(source_file, target)
            target.flush()
            os.fsync(target.fileno())

    try:
        write_exclusive_or_verify(name_path, display_name)
        write_exclusive_or_verify(extract_path, content)
    except Exception:
        # File final dipertahankan; manifest memungkinkan retry memulihkan metadata.
        raise

    return final_path


def create_docx(title: str, body_text: str, user_id: str) -> Path:
    return create_document(title, body_text, user_id, "docx")


def create_pdf(title: str, body_text: str, user_id: str) -> Path:
    return create_document(title, body_text, user_id, "pdf")


def resolve_stored_file(filename: str, user_id: str) -> Path | None:
    """Cari file hasil AI hanya di direktori pengguna yang sedang login."""
    if not filename or Path(filename).name != filename:
        return None

    if Path(filename).suffix.lower() not in {f".{ext}" for ext in SUPPORTED_FORMATS}:
        return None

    try:
        user_dir = _user_storage_dir(user_id).resolve(strict=True)
    except (ValueError, OSError):
        return None

    candidate = user_dir / filename
    if candidate.is_symlink():
        return None

    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None

    if resolved.parent != user_dir or not resolved.is_file():
        return None

    return resolved
