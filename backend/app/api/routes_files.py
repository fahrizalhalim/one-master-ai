"""Upload & ekstraksi teks file (DOCX, PDF, TXT, MD) per user."""

import mimetypes
import os
import uuid
from urllib.parse import quote
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.database import get_db
from app.models.user import User
from app.services.document_service import STORAGE_DIR
from app.services.storage_service import (
    calculate_storage_usage,
    get_storage_policy,
    user_storage_lock,
)
from app.services.workspace_service import (
    WorkspaceError,
    _validated_user_dir,
    _open_workspace_regular_file,
    _unlink_workspace_file,
    _original_files,
    _find_file_with_root,
    find_file_by_id,
    read_file,
    open_file_for_download,
)
from app.services.workspace_folders import _parts, _safe_directory

router = APIRouter(prefix="/api/v1/files", tags=["files"])

UPLOAD_DIR = STORAGE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Batas ukuran 5 MB, ekstensi diizinkan
MAX_SIZE = 5 * 1024 * 1024
ALLOWED_EXT = {
    ".pdf", ".docx", ".doc", ".txt", ".md",
    ".xlsx", ".xls", ".pptx", ".ppt",
    ".jpg", ".jpeg", ".png",
}


class FileInfo(BaseModel):
    id: str
    filename: str
    size: int
    chars: int
    created_at: str
    folder_path: str = ""


def _user_dir(user_id: str) -> Path:
    """Kompatibilitas direktori user dengan validasi keamanan Workspace."""
    try:
        return _validated_user_dir(
            user_id,
            upload_root=UPLOAD_DIR,
            create=True,
        )
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
def extract_text(
    path: Path,
    ext: str,
    user_dir: Path | None = None,
) -> str:
    """Ekstrak teks jika didukung; format lain tetap dapat disimpan."""
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Tipe file tidak didukung.")

    # Format lama Office dan gambar disimpan tanpa ekstraksi teks.
    if ext in {".doc", ".xls", ".ppt", ".jpg", ".jpeg", ".png"}:
        return ""

    if user_dir is None:
        handle = open(path, "rb")
    else:
        fd = _open_workspace_regular_file(user_dir, path)
        handle = os.fdopen(fd, "rb")

    with handle:
        if ext in {".txt", ".md"}:
            return handle.read().decode("utf-8", errors="ignore")

        if ext == ".docx":
            from docx import Document
            doc = Document(handle)
            parts = []
            for para in doc.paragraphs:
                if para.text.strip():
                    prefix = ""
                    if para.style and para.style.name.startswith("Heading"):
                        level = "#" if "1" in para.style.name else "##"
                        prefix = f"{level} "
                    parts.append(prefix + para.text.strip())
            for table in doc.tables:
                for row in table.rows:
                    parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            return "\n".join(parts)

        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(handle)
            return "\n\n".join(
                txt.strip()
                for page in reader.pages
                if (txt := (page.extract_text() or "")).strip()
            )

        if ext == ".xlsx":
            from openpyxl import load_workbook
            workbook = load_workbook(handle, read_only=True, data_only=True)
            try:
                parts = []
                for sheet in workbook.worksheets:
                    parts.append(f"# Sheet: {sheet.title}")
                    for row in sheet.iter_rows(values_only=True):
                        values = [str(value) if value is not None else "" for value in row]
                        if any(value.strip() for value in values):
                            parts.append(" | ".join(values))
                return "\n".join(parts)
            finally:
                workbook.close()

        if ext == ".pptx":
            from pptx import Presentation
            presentation = Presentation(handle)
            parts = []
            for index, slide in enumerate(presentation.slides, start=1):
                parts.append(f"# Slide {index}")
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False):
                        value = shape.text.strip()
                        if value:
                            parts.append(value)
                    if getattr(shape, "has_table", False):
                        for row in shape.table.rows:
                            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            return "\n".join(parts)

    return ""


@router.get("/storage")
def get_storage_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Informasi kuota dan pemakaian storage personal."""
    user_dir = _user_dir(current_user.id)
    policy = get_storage_policy(db, current_user)
    used = calculate_storage_usage(user_dir)
    quota = policy.get("quota_bytes")

    return {
        "status": policy.get("status"),
        "eligible": policy.get("eligible", False),
        "exemption_reason": policy.get("exemption_reason"),
        "package_code": policy.get("package_code"),
        "used_bytes": used,
        "used_mb": round(used / 1_000_000, 3),
        "quota_bytes": quota,
        "quota_mb": policy.get("quota_mb"),
        "remaining_bytes": max(0, quota - used) if quota is not None else None,
        "remaining_mb": (
            round(max(0, quota - used) / 1_000_000, 3)
            if quota is not None else None
        ),
        "upload_allowed": policy.get("upload_allowed", True),
        "purge_due": policy.get("purge_due", False),
        "expires_at": policy.get("expires_at"),
        "grace_ends_at": policy.get("grace_ends_at"),
        "purge_at": policy.get("purge_at"),
    }


@router.post("/upload", response_model=FileInfo)
async def upload_file(
    file: UploadFile = File(...),
    folder_path: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw_filename = str(file.filename or "").replace("\\\\", "/")
    safe_filename = raw_filename.rsplit("/", 1)[-1]
    safe_filename = "".join(
        char for char in safe_filename if char.isprintable()
    ).strip()

    if not safe_filename:
        raise HTTPException(status_code=400, detail="Nama file tidak valid.")

    safe_filename = safe_filename[:120]
    ext = Path(safe_filename).suffix.lower()

    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Tipe file tidak didukung. Gunakan: {', '.join(sorted(ALLOWED_EXT))}",
        )

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="Ukuran file maksimal 5 MB.")

    if not content:
        raise HTTPException(status_code=400, detail="File kosong tidak dapat diunggah.")

    user_dir = _user_dir(current_user.id)

    try:
        folder_parts = _parts(folder_path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Lock yang sama akan digunakan oleh upload web dan Telegram.
    with user_storage_lock(current_user.id):
        policy = get_storage_policy(db, current_user)

        if not policy.get("upload_allowed", True):
            status = policy.get("status")
            if status == "upload_locked":
                detail = (
                    "Masa tenggang 7 hari telah berakhir. "
                    "Upload dikunci, tetapi file lama tetap dapat diakses."
                )
            elif status == "purge_due":
                detail = (
                    "Masa penyimpanan telah mencapai hari ke-30. "
                    "Upload dikunci. File belum dihapus."
                )
            else:
                detail = (
                    "Status paket belum dapat diverifikasi. "
                    "Silakan hubungi administrator."
                )

            raise HTTPException(status_code=403, detail=detail)

        try:
            target_dir = _safe_directory(user_dir, folder_parts)
        except WorkspaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        used = calculate_storage_usage(user_dir)
        quota = policy.get("quota_bytes")

        if quota is not None and used + len(content) > quota:
            remaining = max(0, quota - used)
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Kuota storage tidak mencukupi. "
                    f"Terpakai {used} byte, sisa {remaining} byte, "
                    f"ukuran file {len(content)} byte."
                ),
            )

        existing_ids = {item.stem for item in _original_files(user_dir)}
        file_id = uuid.uuid4().hex[:12]
        while file_id in existing_ids:
            file_id = uuid.uuid4().hex[:12]

        path = target_dir / f"{file_id}{ext}"
        extract_path = target_dir / f"{file_id}.extract.txt"
        name_path = target_dir / f"{file_id}.name.txt"

        try:
            path.write_bytes(content)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500,
                detail="File gagal disimpan.",
            ) from exc

        # Preview bersifat opsional. File asli tetap disimpan jika ekstraksi gagal.
        try:
            text = extract_text(path, ext)
        except Exception:
            text = ""

        try:
            extract_path.write_text(text, encoding="utf-8")
            name_path.write_text(safe_filename, encoding="utf-8")
        except Exception as exc:
            path.unlink(missing_ok=True)
            extract_path.unlink(missing_ok=True)
            name_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500,
                detail="Metadata file gagal disimpan.",
            ) from exc

    return FileInfo(
        id=file_id,
        filename=safe_filename,
        size=len(content),
        chars=len(text),
        created_at=datetime.now().isoformat(),
        folder_path="/".join(folder_parts),
    )


@router.get("/{file_id}/download")
def download_user_file(
    file_id: str,
    current_user: User = Depends(get_current_user),
):
    """Unduh file asli milik pengguna yang sedang login."""
    if len(file_id) != 12 or any(
        char not in "0123456789abcdef" for char in file_id.lower()
    ):
        raise HTTPException(status_code=400, detail="ID file tidak valid.")

    file_id = file_id.lower()

    try:
        with user_storage_lock(current_user.id):
            fd, filename, extension, size = open_file_for_download(
                current_user.id,
                file_id,
            )
    except WorkspaceError as exc:
        message = str(exc).lower()
        if "lebih dari satu" in message:
            status = 409
        elif "tidak valid" in message:
            status = 400
        else:
            status = 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    except OSError as exc:
        raise HTTPException(
            status_code=404,
            detail="File tidak dapat dibuka.",
        ) from exc

    def stream_file():
        try:
            while True:
                chunk = os.read(fd, 64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            os.close(fd)

    media_type = (
        mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )
    disposition = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"

    return StreamingResponse(
        stream_file(),
        media_type=media_type,
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(size),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{file_id}")
def get_file_text(
    file_id: str,
    current_user: User = Depends(get_current_user),
):
    """Ambil metadata + pratinjau teks file di seluruh Workspace pengguna."""
    try:
        data = read_file(current_user.id, file_id)
    except WorkspaceError as exc:
        status = 400 if "tidak valid" in str(exc).lower() else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    text = data["content"]

    return {
        "id": data["id"],
        "filename": data["filename"],
        "folder_path": data["folder_path"],
        "chars": data["chars_returned"],
        "preview": text[:500],
        "total_text": text,
    }


@router.delete("/{file_id}")
def delete_user_file(
    file_id: str,
    current_user: User = Depends(get_current_user),
):
    """Hapus file personal beserta sidecar dari folder tempat file berada."""
    if len(file_id) != 12 or any(
        char not in "0123456789abcdef" for char in file_id.lower()
    ):
        raise HTTPException(status_code=400, detail="ID file tidak valid.")

    file_id = file_id.lower()

    with user_storage_lock(current_user.id):
        try:
            _, user_dir, original = _find_file_with_root(
                current_user.id,
                file_id,
            )
        except WorkspaceError as exc:
            status = 400 if "tidak valid" in str(exc).lower() else 404
            raise HTTPException(status_code=status, detail=str(exc)) from exc

        parent = original.parent
        candidates = [
            original,
            parent / f"{file_id}.extract.txt",
            parent / f"{file_id}.name.txt",
        ]

        deleted = 0
        for candidate in candidates:
            try:
                if _unlink_workspace_file(user_dir, candidate):
                    deleted += 1
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail="File gagal dihapus dengan aman.",
                ) from exc

    return {
        "ok": True,
        "id": file_id,
        "deleted_files": deleted,
        "message": "File dan data ekstraksinya berhasil dihapus.",
    }
