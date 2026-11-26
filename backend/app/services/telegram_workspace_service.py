"""Layanan File Saya untuk bot Telegram pusat OM.AI.

Semua operasi dibatasi pada user_id pemilik workspace.
Tidak mengelola token, webhook, atau akun bot Telegram.
"""

import os
import re
import uuid
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.api.routes_files import ALLOWED_EXT, MAX_SIZE, extract_text
from app.core.config import OMAI_TELEGRAM_BOT_TOKEN
from app.models.user import User
from app.services.document_service import STORAGE_DIR, create_document
from app.services.storage_service import (
    calculate_storage_usage,
    get_storage_policy,
    user_storage_lock,
)
from app.services.workspace_service import (
    WorkspaceError,
    _validated_user_dir,
    _write_workspace_file_exclusive,
    _unlink_workspace_file,
    _original_files,
    _find_file_with_root,
    find_file_by_id,
    list_files,
    search_files,
    read_file,
    open_file_for_download,
)
from app.services.workspace_folders import (
    create_folder,
    list_folders,
    rename_folder,
    delete_empty_folder,
    _parts,
    _safe_directory,
)

UPLOAD_DIR = STORAGE_DIR / "uploads"
GENERATED_DIR = STORAGE_DIR / "generated"
MAX_TELEGRAM_DOWNLOAD = 5 * 1024 * 1024
FILE_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _format_size(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.2f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.1f} KB"
    return f"{size} byte"


def _combined_storage_usage(user_id: str) -> int:
    """Hitung file asli milik user dari uploads dan hasil AI."""
    if GENERATED_DIR.is_symlink():
        raise WorkspaceError("Direktori hasil AI tidak aman.")

    upload_dir = _validated_user_dir(
        user_id,
        upload_root=UPLOAD_DIR,
        create=False,
    )
    generated_dir = _validated_user_dir(
        user_id,
        upload_root=GENERATED_DIR,
        create=False,
    )

    return (
        calculate_storage_usage(upload_dir)
        + calculate_storage_usage(generated_dir)
    )


def _clean_filename(filename: str) -> str:
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char.isprintable()).strip()
    if not name or name in {".", ".."}:
        raise WorkspaceError("Nama file tidak valid.")
    name = name[:120]
    if Path(name).suffix.lower() not in ALLOWED_EXT:
        raise WorkspaceError(
            "Format belum didukung. Format diizinkan: "
            "PDF, DOCX, DOC, TXT, MD, XLSX, XLS, PPTX, PPT, JPG, JPEG, PNG."
        )
    return name


def _file_rows(user_id: str, rows: list[dict]) -> str:
    if not rows:
        return "Belum ada file yang ditemukan."

    lines = ["📁 File Workspace:"]
    for item in rows[:30]:
        folder = item.get("folder_path") or "/"
        lines.append(
            f"• {item['filename']}\n"
            f"  ID: {item['id']} | {_format_size(item['size_bytes'])}\n"
            f"  Folder: {folder}"
        )
    if len(rows) > 30:
        lines.append(f"\nMenampilkan 30 dari {len(rows)} file.")
    lines.append("\nGunakan /baca ID atau /unduh ID untuk membuka file.")
    return "\n".join(lines)


def workspace_menu_text() -> str:
    return (
        "📁 FILE SAYA — WORKSPACE PERSONAL\n\n"
        "Kirim perintah berikut:\n"
        "/daftar_file — daftar file\n"
        "/cari_file kata — cari nama atau isi file\n"
        "/folder — daftar folder utama\n"
        "/buat_folder nama — buat folder\n"
        "/isi_folder path — lihat isi folder\n"
        "/storage — kapasitas penyimpanan\n"
        "/baca ID — preview teks file\n"
        "/unduh ID — kirim file ke Telegram\n"
        "/rename_file ID|nama.ext — ubah nama file\n"
        "/rename_folder path|nama_baru — ubah nama folder\n"
        "/hapus_file ID — minta konfirmasi hapus\n"
        "/konfirmasi_hapus ID — lanjutkan penghapusan\n"
        "/hapus_folder path — hapus folder kosong\n\n"
        "Untuk mengunggah, kirim dokumen atau foto sebagai lampiran Telegram.\n"
        "Format: PDF, DOCX, DOC, TXT, MD, XLSX, XLS, PPTX, PPT, JPG, JPEG, PNG. "
        "Maksimal 5 MB."
    )


def list_user_files(user_id: str) -> str:
    return _file_rows(user_id, list_files(user_id, limit=100))


def search_user_files(user_id: str, query: str) -> str:
    rows = search_files(user_id, query, limit=50)
    return _file_rows(user_id, rows)


def list_user_folders(user_id: str, parent_path: str = "") -> str:
    rows = list_folders(user_id, parent_path)
    heading = f"📂 Folder: {parent_path or '/'}"
    if not rows:
        return heading + "\nBelum ada subfolder."
    return heading + "\n" + "\n".join(
        f"• {item['path']}" for item in rows
    )


def create_user_folder(user_id: str, name: str, parent_path: str = "") -> str:
    item = create_folder(user_id, name, parent_path)
    return f"Folder berhasil dibuat: {item['path']}"


def rename_user_folder(user_id: str, folder_path: str, new_name: str) -> str:
    item = rename_folder(user_id, folder_path, new_name)
    return f"Folder berhasil diubah: {item['old_path']} → {item['path']}"


def delete_user_folder(user_id: str, folder_path: str) -> str:
    item = delete_empty_folder(user_id, folder_path)
    return f"Folder kosong berhasil dihapus: {item['deleted_path']}"


def storage_user_info(db: Session, user: User) -> str:
    _validated_user_dir(user.id, upload_root=UPLOAD_DIR, create=True)
    policy = get_storage_policy(db, user)
    used = _combined_storage_usage(user.id)
    quota = policy.get("quota_bytes")
    used_text = _format_size(used)
    quota_text = _format_size(quota) if quota is not None else "Tidak terbatas"
    remaining = max(0, quota - used) if quota is not None else None
    remaining_text = _format_size(remaining) if remaining is not None else "—"
    return (
        "💾 STORAGE PERSONAL\n\n"
        f"Paket: {policy.get('package_code') or '—'}\n"
        f"Status: {policy.get('status') or '—'}\n"
        f"Terpakai: {used_text}\n"
        f"Kapasitas: {quota_text}\n"
        f"Sisa: {remaining_text}\n"
        f"Upload: {'Diizinkan' if policy.get('upload_allowed', True) else 'Dikunci'}"
    )


def preview_user_file(user_id: str, file_id: str) -> str:
    item = read_file(user_id, file_id, max_chars=3000)
    content = item.get("content") or ""
    if not content.strip():
        return (
            f"File: {item['filename']}\n"
            "Belum tersedia teks yang dapat dipreview. "
            "Gunakan /unduh untuk mengambil file aslinya."
        )
    result = (
        f"📄 {item['filename']}\n"
        f"ID: {item['id']} | Folder: {item.get('folder_path') or '/'}\n\n"
        f"{content}"
    )
    if item.get("truncated"):
        result += "\n\n[Preview dipotong. Unduh file untuk membaca seluruh isi.]"
    return result


def get_user_file_extension(user_id: str, file_id: str) -> str:
    """Ambil ekstensi file melalui pencarian yang dibatasi pada workspace user."""
    if not FILE_ID_RE.fullmatch(str(file_id or "").lower()):
        raise WorkspaceError("ID file tidak valid.")
    return find_file_by_id(user_id, file_id).suffix.lower()


def rename_user_file(user_id: str, file_id: str, new_filename: str) -> str:
    if not FILE_ID_RE.fullmatch(str(file_id or "").lower()):
        raise WorkspaceError("ID file tidak valid.")
    new_filename = _clean_filename(new_filename)
    with user_storage_lock(user_id):
        _, user_dir, original = _find_file_with_root(user_id, file_id)
        if Path(new_filename).suffix.lower() != original.suffix.lower():
            raise WorkspaceError(
                "Ekstensi file tidak boleh diubah saat mengganti nama."
            )
        name_path = original.parent / f"{original.stem}.name.txt"
        try:
            _unlink_workspace_file(user_dir, name_path, missing_ok=True)
            _write_workspace_file_exclusive(
                user_dir, name_path, new_filename.encode("utf-8")
            )
        except OSError as exc:
            raise WorkspaceError("Nama file gagal diperbarui.") from exc
    return f"Nama file berhasil diubah menjadi: {new_filename}"


def delete_user_file(user_id: str, file_id: str) -> str:
    if not FILE_ID_RE.fullmatch(str(file_id or "").lower()):
        raise WorkspaceError("ID file tidak valid.")
    with user_storage_lock(user_id):
        _, user_dir, original = _find_file_with_root(user_id, file_id)
        stem = original.stem
        deleted = 0
        for path in (
            original,
            original.parent / f"{stem}.extract.txt",
            original.parent / f"{stem}.name.txt",
        ):
            try:
                if _unlink_workspace_file(user_dir, path, missing_ok=True):
                    deleted += 1
            except OSError as exc:
                raise WorkspaceError("File gagal dihapus dengan aman.") from exc
    return f"File {file_id} berhasil dihapus. Komponen dihapus: {deleted}."


async def upload_telegram_document(
    db: Session,
    user: User,
    telegram_document: dict,
) -> str:
    if not OMAI_TELEGRAM_BOT_TOKEN:
        raise WorkspaceError("Konfigurasi bot pusat belum tersedia.")

    filename = _clean_filename(telegram_document.get("file_name") or "")
    ext = Path(filename).suffix.lower()

    declared_size = telegram_document.get("file_size")
    if declared_size is not None:
        try:
            declared_size = int(declared_size)
        except (TypeError, ValueError, OverflowError) as exc:
            raise WorkspaceError("Informasi ukuran file Telegram tidak valid.") from exc
        if declared_size < 0 or declared_size > MAX_TELEGRAM_DOWNLOAD:
            raise WorkspaceError("Ukuran file maksimal 5 MB.")

    file_id_telegram = str(telegram_document.get("file_id") or "")
    if (
        not file_id_telegram
        or len(file_id_telegram) > 1024
        or not file_id_telegram.isprintable()
    ):
        raise WorkspaceError("Identitas dokumen Telegram tidak valid.")

    api_base = f"https://api.telegram.org/bot{OMAI_TELEGRAM_BOT_TOKEN}"
    timeout = httpx.Timeout(30.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(
            f"{api_base}/getFile",
            params={"file_id": file_id_telegram},
        )
        response.raise_for_status()
        payload = response.json()

        if not payload.get("ok"):
            raise WorkspaceError("Telegram gagal menyiapkan file.")

        remote_path = payload.get("result", {}).get("file_path")
        if (
            not isinstance(remote_path, str)
            or not remote_path
            or len(remote_path) > 1024
            or remote_path.startswith("/")
            or "\\" in remote_path
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", remote_path)
            or any(part in {"", ".", ".."} for part in remote_path.split("/"))
        ):
            raise WorkspaceError("Lokasi file Telegram tidak valid.")

        download_url = (
            f"https://api.telegram.org/file/bot{OMAI_TELEGRAM_BOT_TOKEN}/{remote_path}"
        )
        chunks = []
        total_size = 0

        async with client.stream("GET", download_url) as download:
            download.raise_for_status()
            async for chunk in download.aiter_bytes(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total_size += len(chunk)
                if total_size > MAX_TELEGRAM_DOWNLOAD:
                    raise WorkspaceError("Ukuran file maksimal 5 MB.")
                chunks.append(chunk)

    if total_size == 0:
        raise WorkspaceError("File kosong atau tidak dapat diunduh.")

    content = b"".join(chunks)

    user_dir = _validated_user_dir(user.id, upload_root=UPLOAD_DIR, create=True)
    with user_storage_lock(user.id):
        policy = get_storage_policy(db, user)
        if not policy.get("upload_allowed", True):
            raise WorkspaceError(
                "Upload sedang dikunci oleh kebijakan masa aktif paket penyimpanan."
            )

        used = _combined_storage_usage(user.id)
        quota = policy.get("quota_bytes")
        if quota is not None and used + len(content) > quota:
            raise WorkspaceError(
                f"Kuota storage tidak cukup. Terpakai {_format_size(used)}, "
                f"sisa {_format_size(max(0, quota - used))}."
            )

        existing_ids = {item.stem for item in _original_files(user_dir)}
        new_id = uuid.uuid4().hex[:12]
        while new_id in existing_ids:
            new_id = uuid.uuid4().hex[:12]

        path = user_dir / f"{new_id}{ext}"
        extract_path = user_dir / f"{new_id}.extract.txt"
        name_path = user_dir / f"{new_id}.name.txt"
        created_paths = []

        try:
            _write_workspace_file_exclusive(user_dir, path, content)
            created_paths.append(path)

            try:
                extracted = extract_text(path, ext, user_dir=user_dir)
            except Exception:
                # Preview opsional; jangan membatalkan penyimpanan file asli.
                extracted = ""

            _write_workspace_file_exclusive(
                user_dir, extract_path, extracted.encode("utf-8")
            )
            created_paths.append(extract_path)

            _write_workspace_file_exclusive(
                user_dir, name_path, filename.encode("utf-8")
            )
            created_paths.append(name_path)

        except Exception as exc:
            for created_path in reversed(created_paths):
                try:
                    _unlink_workspace_file(user_dir, created_path, missing_ok=True)
                except OSError:
                    pass
            raise WorkspaceError(
                "File gagal disimpan atau tidak dapat diproses."
            ) from exc

    return (
        f"✅ File berhasil diunggah.\n"
        f"Nama: {filename}\n"
        f"ID: {new_id}\n"
        f"Ukuran: {_format_size(len(content))}\n"
        f"Preview: gunakan /baca {new_id}\n"
        f"Unduh: gunakan /unduh {new_id}"
    )



def create_user_document(
    db: Session,
    user: User,
    title: str,
    body_text: str,
    file_format: str,
    file_id: str,
) -> Path:
    """Buat dokumen dalam workspace pemilik dengan pemeriksaan kuota."""
    if not FILE_ID_RE.fullmatch(str(file_id or "").lower()):
        raise WorkspaceError("ID dokumen tidak valid.")

    user_dir = _validated_user_dir(
        user.id,
        upload_root=GENERATED_DIR,
        create=True,
    )

    final_path = user_dir / f"{file_id}.{file_format}"
    name_path = user_dir / f"{file_id}.name.txt"
    extract_path = user_dir / f"{file_id}.extract.txt"
    manifest_path = user_dir / f".{file_id}.request.json"

    with user_storage_lock(user.id):
        policy = get_storage_policy(db, user)
        already_exists = final_path.is_file() and not final_path.is_symlink()

        if not already_exists and policy.get("eligible"):
            if not policy.get("upload_allowed", False):
                raise WorkspaceError(
                    "Pembuatan dokumen baru dikunci oleh status paket storage Anda."
                )

            quota = policy.get("quota_bytes")
            if not isinstance(quota, int) or quota <= 0:
                raise WorkspaceError(
                    "Kapasitas storage paket tidak tersedia. Hubungi administrator."
                )

            used = _combined_storage_usage(user.id)
            if used >= quota:
                raise WorkspaceError(
                    "Kuota storage sudah penuh. Hapus file yang tidak diperlukan "
                    "atau tingkatkan paket Anda sebelum membuat dokumen baru."
                )

        try:
            result = create_document(
                title=title,
                body_text=body_text,
                user_id=user.id,
                file_format=file_format,
                file_id=file_id,
            )

            if result.parent.resolve() != user_dir.resolve():
                raise WorkspaceError("Lokasi hasil dokumen tidak sesuai.")

            # Hasil retry yang sudah ada tidak dihitung sebagai file baru.
            if not already_exists and policy.get("eligible"):
                quota = policy.get("quota_bytes")
                used_after = _combined_storage_usage(user.id)

                if used_after > quota:
                    # Batalkan hanya artefak milik permintaan baru ini.
                    for target in (
                        result,
                        name_path,
                        extract_path,
                        manifest_path,
                    ):
                        if target.exists() or target.is_symlink():
                            _unlink_workspace_file(
                                user_dir,
                                target,
                                missing_ok=True,
                            )

                    raise WorkspaceError(
                        "Dokumen tidak disimpan karena ukuran hasilnya "
                        "melebihi sisa kuota storage Anda."
                    )

            return result

        except WorkspaceError:
            raise
        except Exception as exc:
            raise WorkspaceError(
                "Dokumen gagal dibuat. Silakan coba kembali."
            ) from exc


async def send_generated_document(
    chat_id: int,
    user_id: str,
    file_id: str,
    file_format: str,
    title: str,
) -> bool | None:
    """Kirim hanya file generated milik user; hasil tri-state."""
    if not OMAI_TELEGRAM_BOT_TOKEN:
        raise WorkspaceError("Konfigurasi bot pusat belum tersedia.")

    if not FILE_ID_RE.fullmatch(str(file_id or "").lower()):
        raise WorkspaceError("ID dokumen tidak valid.")

    if file_format not in {
        "pdf", "docx", "doc", "txt", "md",
        "xlsx", "xls", "pptx", "ppt",
        "jpg", "jpeg", "png",
    }:
        raise WorkspaceError("Format dokumen tidak valid.")

    user_dir = _validated_user_dir(
        user_id,
        upload_root=GENERATED_DIR,
        create=False,
    )
    filename_on_disk = f"{file_id}.{file_format}"
    display_filename = f"{title}.{file_format}"
    target = user_dir / filename_on_disk

    directory_fd = None
    file_fd = None

    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(user_dir, flags | nofollow)
        file_fd = os.open(filename_on_disk, os.O_RDONLY | nofollow, dir_fd=directory_fd)

        metadata = os.fstat(file_fd)
        import stat
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise WorkspaceError("Hasil dokumen bukan file biasa yang valid.")

        mime = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "doc": "application/msword",
            "txt": "text/plain",
            "md": "text/markdown",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xls": "application/vnd.ms-excel",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "ppt": "application/vnd.ms-powerpoint",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
        }[file_format]

        api_url = (
            f"https://api.telegram.org/bot{OMAI_TELEGRAM_BOT_TOKEN}/sendDocument"
        )

        with os.fdopen(file_fd, "rb") as handle:
            file_fd = None
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    api_url,
                    data={"chat_id": str(chat_id)},
                    files={"document": (display_filename, handle, mime)},
                )

        if 400 <= response.status_code < 500:
            return False
        if response.status_code >= 500:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict):
            return None
        return True if payload.get("ok") is True else False

    except WorkspaceError:
        raise
    except httpx.RequestError:
        return None
    except OSError as exc:
        raise WorkspaceError("File hasil AI tidak dapat dibuka dengan aman.") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


async def send_user_file(
    chat_id: int,
    user_id: str,
    file_id: str,
) -> bool | None:
    """Kirim file milik user; hasil tri-state untuk mencegah duplikasi."""
    if not OMAI_TELEGRAM_BOT_TOKEN:
        raise WorkspaceError("Konfigurasi bot pusat belum tersedia.")

    fd, filename, extension, _size = open_file_for_download(user_id, file_id)

    mime = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".ppt": "application/vnd.ms-powerpoint",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(extension, "application/octet-stream")

    api_url = f"https://api.telegram.org/bot{OMAI_TELEGRAM_BOT_TOKEN}/sendDocument"

    try:
        with os.fdopen(fd, "rb") as handle:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    api_url,
                    data={"chat_id": str(chat_id)},
                    files={"document": (filename, handle, mime)},
                )

        if 400 <= response.status_code < 500:
            return False
        if response.status_code >= 500:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict):
            return None

        return True if payload.get("ok") is True else False

    except httpx.RequestError:
        return None

async def send_user_image_preview(
    chat_id: int,
    user_id: str,
    file_id: str,
) -> bool | None:
    """Kirim preview gambar milik user dengan hasil tri-state."""
    if not OMAI_TELEGRAM_BOT_TOKEN:
        raise WorkspaceError("Konfigurasi bot pusat belum tersedia.")

    fd, filename, extension, _size = open_file_for_download(user_id, file_id)

    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(extension)

    if not mime:
        os.close(fd)
        raise WorkspaceError("File tersebut bukan gambar yang didukung.")

    api_url = f"https://api.telegram.org/bot{OMAI_TELEGRAM_BOT_TOKEN}/sendPhoto"

    try:
        with os.fdopen(fd, "rb") as handle:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    api_url,
                    data={"chat_id": str(chat_id)},
                    files={"photo": (filename, handle, mime)},
                )

        if 400 <= response.status_code < 500:
            return False
        if response.status_code >= 500:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict):
            return None

        return True if payload.get("ok") is True else False

    except httpx.RequestError:
        return None
