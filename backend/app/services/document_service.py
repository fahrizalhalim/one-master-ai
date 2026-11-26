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
from docx.shared import Pt, Inches as DocxInches
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches as PPTInches, Pt as PPTPt
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, HRFlowable,
    ListFlowable, ListItem, KeepTogether,
)
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
    """Parse Markdown sederhana dengan pengenalan heading bernomor."""
    blocks = []
    section_names = (
        "pendahuluan", "latar belakang", "rumusan masalah",
        "tujuan", "manfaat", "tinjauan pustaka", "landasan teori",
        "metode", "metodologi", "hasil", "pembahasan",
        "kesimpulan", "saran", "daftar pustaka", "referensi",
        "lampiran", "penutup", "abstrak",
    )

    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        if re.fullmatch(r"[-*_]{3,}", line):
            blocks.append(("rule", ""))
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            kind = "title" if level == 1 else (
                "h2" if level == 2 else "h3"
            )
            blocks.append((kind, heading.group(2).strip()))
            continue

        # Heading bernomor: 1. Pendahuluan, 2.1 Perkembangan, dst.
        numbered_heading = re.match(
            r"^(\d+(?:\.\d+)*)(?:[.)])?\s+(.+)$", line
        )
        if numbered_heading:
            number = numbered_heading.group(1)
            label = numbered_heading.group(2).strip()
            normalized = label.lower().rstrip(":.")

            if (
                "." in number
                or any(
                    normalized == name or normalized.startswith(name + " ")
                    for name in section_names
                )
            ):
                depth = number.count(".")
                kind = "h2" if depth == 0 else "h3"
                blocks.append((kind, f"{number}. {label}"))
                continue

        if re.match(r"^[-*+]\s+", line):
            blocks.append(("bullet", re.sub(r"^[-*+]\s+", "", line)))
        elif re.match(r"^\d+[.)]\s+", line):
            blocks.append(("number", re.sub(r"^\d+[.)]\s+", "", line)))
        else:
            blocks.append(("para", line))

    return blocks


def _reportlab_markup(text: str) -> str:
    """Konversi inline Markdown menjadi markup ReportLab yang aman."""
    value = escape(str(text or ""))
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", value)
    value = re.sub(
        r"`([^`]+)`",
        r'<font name="OMAI-Mono">\1</font>',
        value,
    )
    return value


def _add_docx_runs(paragraph, text: str) -> None:
    """Tambahkan teks dan inline Markdown ke paragraf DOCX."""
    pattern = re.compile(r"(\*\*.+?\*\*|\*[^*]+\*|`[^`]+`)")
    position = 0

    for match in pattern.finditer(str(text or "")):
        if match.start() > position:
            paragraph.add_run(text[position:match.start()])

        token = match.group(0)
        if token.startswith("**"):
            run = paragraph.add_run(token[2:-2])
            run.bold = True
        elif token.startswith("`"):
            run = paragraph.add_run(token[1:-1])
            run.font.name = "Consolas"
        else:
            run = paragraph.add_run(token[1:-1])
            run.italic = True

        position = match.end()

    if position < len(text):
        paragraph.add_run(text[position:])


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
    from .print_layout_service import build_docx_print_ready
    build_docx_print_ready(title, body_text, path)



def _build_pdf(title: str, body_text: str, path: Path) -> None:
    from .print_layout_service import build_pdf_print_ready
    build_pdf_print_ready(title, body_text, path)


def _build_txt(title: str, body_text: str, path: Path, markdown: bool = False) -> None:
    content = str(body_text or "")
    if markdown and not content.lstrip().startswith("#"):
        content = f"# {title}\n\n{content}"
    path.write_text(content.rstrip() + "\n", encoding="utf-8")



def _build_xlsx(title: str, body_text: str, path: Path) -> None:
    from .print_layout_service import build_xlsx_print_ready
    build_xlsx_print_ready(title, body_text, path)


def _build_pptx(title: str, body_text: str, path: Path) -> None:
    from .pptx_layout_service import build_pptx_print_ready
    build_pptx_print_ready(title, body_text, path)

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
