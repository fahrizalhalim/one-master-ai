"""Upload & ekstraksi teks file (DOCX, PDF, TXT, MD) per user."""

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.database import get_db
from app.models.user import User
from app.services.document_service import STORAGE_DIR

router = APIRouter(prefix="/api/v1/files", tags=["files"])

UPLOAD_DIR = STORAGE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Batas ukuran 5 MB, ekstensi diizinkan
MAX_SIZE = 5 * 1024 * 1024
ALLOWED_EXT = {".docx", ".pdf", ".txt", ".md"}


class FileInfo(BaseModel):
    id: str
    filename: str
    size: int
    chars: int
    created_at: str


def _user_dir(user_id: str) -> Path:
    d = UPLOAD_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def extract_text(path: Path, ext: str) -> str:
    """Ekstrak teks dari file sesuai tipe."""
    if ext == ".txt" or ext == ".md":
        return path.read_text(encoding="utf-8", errors="ignore")

    if ext == ".docx":
        from docx import Document

        doc = Document(str(path))
        parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                prefix = ""
                if para.style.name.startswith("Heading"):
                    level = "".join("#" for _ in range(1)) if "1" in para.style.name else "##"
                    prefix = f"{level} " if "Heading" in para.style.name else ""
                parts.append(prefix + para.text.strip())
        # tabel DOCX
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                parts.append(" | ".join(cells))
        return "\n".join(parts)

    if ext == ".pdf":
        import io

        from reportlab.lib.utils import simpleSplit  # noqa: F401
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            txt = page.extract_text() or ""
            if txt.strip():
                parts.append(txt.strip())
        return "\n\n".join(parts)

    raise HTTPException(status_code=400, detail="Tipe file tidak didukung.")


@router.post("/upload", response_model=FileInfo)
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Tipe file tidak didukung. Gunakan: {', '.join(sorted(ALLOWED_EXT))}",
        )

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="Ukuran file maksimal 5 MB.")

    # Simpan dengan nama acak (hindari path traversal dari filename)
    file_id = uuid.uuid4().hex[:12]
    user_dir = _user_dir(current_user.id)
    path = user_dir / f"{file_id}{ext}"
    path.write_bytes(content)

    # Ekstrak teks
    try:
        text = extract_text(path, ext)
    except HTTPException:
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="File rusak atau tidak bisa dibaca. Coba file lain.",
        )

    # Simpan teks hasil ekstraksi sebagai .txt di samping file asli
    (user_dir / f"{file_id}.extract.txt").write_text(
        text, encoding="utf-8"
    )

    return FileInfo(
        id=file_id,
        filename=file.filename or f"file{ext}",
        size=len(content),
        chars=len(text),
        created_at=datetime.now().isoformat(),
    )


@router.get("/{file_id}")
def get_file_text(
    file_id: str,
    current_user: User = Depends(get_current_user),
):
    """Ambil metadata + pratinjau teks (500 char pertama)."""
    if not file_id.isalnum():
        raise HTTPException(status_code=400, detail="ID tidak valid.")
    user_dir = _user_dir(current_user.id)
    extract = user_dir / f"{file_id}.extract.txt"
    if not extract.exists():
        raise HTTPException(status_code=404, detail="File tidak ditemukan.")

    text = extract.read_text(encoding="utf-8", errors="ignore")
    return {
        "id": file_id,
        "chars": len(text),
        "preview": text[:500],
        "total_text": text,
    }
