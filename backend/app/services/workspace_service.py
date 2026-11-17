"""Workspace personal: daftar, pencarian, dan pembacaan file pengguna."""

import os
import re
import stat
import uuid
from pathlib import Path

from app.services.storage_service import user_storage_lock

from app.services.document_service import STORAGE_DIR


UPLOAD_ROOT = STORAGE_DIR / "uploads"
GENERATED_ROOT = STORAGE_DIR / "generated"
ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".md",
    ".xlsx", ".xls", ".pptx", ".ppt",
    ".jpg", ".jpeg", ".png",
}
FILE_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")

MAX_LIST_RESULTS = 200
MAX_READ_CHARS = 30000


class WorkspaceError(Exception):
    """Kesalahan operasi Workspace yang aman ditampilkan kepada pengguna."""


def _validated_user_dir(
    user_id: str,
    upload_root: Path | None = None,
    create: bool = False,
) -> Path:
    """Validasi identitas dan pastikan direktori user berada di dalam upload root."""
    try:
        normalized_id = str(uuid.UUID(str(user_id)))
    except (ValueError, TypeError, AttributeError):
        raise WorkspaceError("Identitas pengguna tidak valid.")

    base = Path(upload_root) if upload_root is not None else UPLOAD_ROOT

    try:
        if create:
            base.mkdir(parents=True, exist_ok=True)

        if not base.exists() or not base.is_dir():
            if not create:
                return base.resolve() / normalized_id
            raise WorkspaceError("Direktori penyimpanan tidak tersedia.")

        root = base.resolve(strict=True)
        candidate = root / normalized_id

        if candidate.is_symlink():
            raise WorkspaceError("Direktori Workspace tidak aman.")

        if create:
            candidate.mkdir(parents=False, exist_ok=True)

        if not candidate.exists():
            return candidate

        resolved = candidate.resolve(strict=True)

        if not resolved.is_relative_to(root) or not resolved.is_dir():
            raise WorkspaceError("Akses direktori Workspace ditolak.")

        return resolved

    except WorkspaceError:
        raise
    except OSError as exc:
        raise WorkspaceError("Direktori Workspace tidak dapat diakses.") from exc


def _original_files(user_dir: Path) -> list[Path]:
    """Temukan file asli secara rekursif tanpa mengikuti symlink."""
    user_dir = Path(user_dir)

    if not user_dir.exists():
        return []

    if user_dir.is_symlink() or not user_dir.is_dir():
        raise WorkspaceError("Direktori Workspace tidak aman.")

    root = user_dir.resolve(strict=True)
    results = []

    for current, directories, filenames in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current_dir = Path(current)

        # Jangan memasuki folder tersembunyi, symlink, atau di luar root.
        safe_directories = []
        for dirname in directories:
            child = current_dir / dirname

            if dirname.startswith(".") or child.is_symlink():
                continue

            try:
                resolved = child.resolve(strict=True)
                if resolved.is_dir() and resolved.is_relative_to(root):
                    safe_directories.append(dirname)
            except (OSError, RuntimeError):
                continue

        directories[:] = safe_directories

        for filename in filenames:
            path = current_dir / filename

            if filename.startswith(".") or path.is_symlink():
                continue

            if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue

            if not FILE_ID_PATTERN.fullmatch(path.stem):
                continue

            try:
                metadata = path.lstat()
                if path.is_file() and path.resolve(strict=True).is_relative_to(root):
                    results.append(path)
            except (OSError, RuntimeError):
                continue

    return sorted(results, key=lambda item: str(item.relative_to(root)).casefold())


def _open_workspace_regular_file(user_dir: Path, target_path: Path) -> int:
    """Buka file biasa di dalam Workspace tanpa mengikuti symlink."""
    try:
        relative = target_path.relative_to(user_dir)
    except ValueError as exc:
        raise OSError("Path berada di luar Workspace.") from exc

    if not relative.parts:
        raise OSError("Path file tidak valid.")

    directory_fd = None
    file_fd = None

    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)

        directory_fd = os.open(user_dir, directory_flags | nofollow)

        metadata = os.fstat(directory_fd)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("Workspace bukan direktori.")

        for part in relative.parts[:-1]:
            next_fd = os.open(
                part,
                directory_flags | nofollow,
                dir_fd=directory_fd,
            )
            try:
                metadata = os.fstat(next_fd)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise OSError("Komponen path bukan direktori.")
            except Exception:
                os.close(next_fd)
                raise

            os.close(directory_fd)
            directory_fd = next_fd

        file_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | nofollow,
            dir_fd=directory_fd,
        )

        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("Objek bukan file biasa.")

        result = file_fd
        file_fd = None
        return result

    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _write_workspace_file_exclusive(
    user_dir: Path,
    target_path: Path,
    content: bytes,
) -> None:
    """Buat file baru secara eksklusif tanpa mengikuti symlink."""
    try:
        relative = target_path.relative_to(user_dir)
    except ValueError as exc:
        raise OSError("Path berada di luar Workspace.") from exc

    if not relative.parts:
        raise OSError("Path file tidak valid.")

    directory_fd = None
    file_fd = None
    created = False
    filename = relative.parts[-1]

    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(user_dir, directory_flags | nofollow)

        metadata = os.fstat(directory_fd)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("Workspace bukan direktori.")


        for part in relative.parts[:-1]:
            next_fd = os.open(
                part,
                directory_flags | nofollow,
                dir_fd=directory_fd,
            )
            metadata = os.fstat(next_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(next_fd)
                raise OSError("Komponen path bukan direktori.")
            os.close(directory_fd)
            directory_fd = next_fd

        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | nofollow
        )
        file_fd = os.open(
            filename,
            flags,
            0o600,
            dir_fd=directory_fd,
        )
        created = True

        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("Objek yang dibuat bukan file biasa.")

        remaining = memoryview(content)
        while remaining:
            written = os.write(file_fd, remaining)
            if written <= 0:
                raise OSError("Penulisan file tidak selesai.")
            remaining = remaining[written:]

        os.fsync(file_fd)

    except Exception:
        if created and directory_fd is not None:
            try:
                os.unlink(filename, dir_fd=directory_fd)
            except OSError:
                pass
        raise

    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)

def _unlink_workspace_file(
    user_dir: Path,
    target_path: Path,
    missing_ok: bool = True,
) -> bool:
    """Hapus file Workspace melalui directory descriptor tanpa mengikuti symlink."""
    try:
        relative = target_path.relative_to(user_dir)
    except ValueError as exc:
        raise OSError("Path berada di luar Workspace.") from exc

    if not relative.parts:
        raise OSError("Path file tidak valid.")

    directory_fd = None

    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)

        directory_fd = os.open(
            user_dir,
            directory_flags | nofollow,
        )

        metadata = os.fstat(directory_fd)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("Workspace bukan direktori.")

        for part in relative.parts[:-1]:
            next_fd = os.open(
                part,
                directory_flags | nofollow,
                dir_fd=directory_fd,
            )
            try:
                metadata = os.fstat(next_fd)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise OSError("Komponen path bukan direktori.")
            except Exception:
                os.close(next_fd)
                raise

            os.close(directory_fd)
            directory_fd = next_fd

        try:
            os.unlink(relative.parts[-1], dir_fd=directory_fd)
            return True
        except FileNotFoundError:
            if missing_ok:
                return False
            raise

    finally:
        if directory_fd is not None:
            os.close(directory_fd)

def _read_workspace_text(
    user_dir: Path,
    target_path: Path,
    max_chars: int,
    errors: str = "replace",
) -> str:
    """Baca sidecar melalui descriptor yang tidak mengikuti symlink."""
    fd = _open_workspace_regular_file(user_dir, target_path)
    with os.fdopen(fd, "r", encoding="utf-8", errors=errors) as handle:
        return handle.read(max_chars)


def _display_name(
    user_dir: Path,
    file_dir: Path,
    file_id: str,
    fallback: str,
) -> str:
    """Ambil nama asli melalui pembacaan sidecar yang aman."""
    name_path = file_dir / f"{file_id}.name.txt"

    try:
        name = _read_workspace_text(
            user_dir,
            name_path,
            max_chars=4096,
            errors="replace",
        ).strip()
    except OSError:
        return fallback

    return Path(name).name[:120] if name else fallback


def find_file_by_id(user_id: str, file_id: str) -> Path:
    """Temukan file asli berdasarkan ID di seluruh Workspace satu pengguna."""
    _, _, original = _find_file_with_root(user_id, file_id)
    return original


def _workspace_user_dirs(user_id: str) -> list[tuple[str, Path]]:
    """Direktori Workspace milik pengguna, dipisahkan menurut jenis penyimpanan."""
    locations = []
    for label, root in (("", UPLOAD_ROOT), ("Hasil AI", GENERATED_ROOT)):
        if root.is_symlink():
            raise WorkspaceError("Direktori penyimpanan tidak aman.")
        user_dir = _validated_user_dir(user_id, upload_root=root)
        if user_dir.exists():
            locations.append((label, user_dir))
    return locations


def _find_file_with_root(user_id: str, file_id: str) -> tuple[str, Path, Path]:
    """Temukan file dan direktori asalnya hanya dalam Workspace pengguna."""
    file_id = str(file_id or "").lower()
    if not FILE_ID_PATTERN.fullmatch(file_id):
        raise WorkspaceError("ID file tidak valid.")

    matches = []
    for label, user_dir in _workspace_user_dirs(user_id):
        matches.extend(
            (label, user_dir, path)
            for path in _original_files(user_dir)
            if path.stem == file_id
        )

    if not matches:
        raise WorkspaceError("File tidak ditemukan.")
    if len(matches) > 1:
        raise WorkspaceError(
            "ID file ditemukan lebih dari satu kali. Hubungi administrator."
        )
    return matches[0]


def _list_files_unlocked(user_id: str, limit: int = 100) -> list[dict]:
    """Daftar unggahan dan hasil AI milik pengguna."""
    limit = max(1, min(int(limit), MAX_LIST_RESULTS))
    results = []

    for label, user_dir in _workspace_user_dirs(user_id):
        for path in _original_files(user_dir):
            if len(results) >= limit:
                return results
            try:
                metadata = path.stat()
                file_id = path.stem
                relative_parent = path.parent.relative_to(user_dir).as_posix()
                folder_path = "" if relative_parent == "." else relative_parent
                if label:
                    folder_path = "/".join(
                        part for part in (label, folder_path) if part
                    )

                results.append({
                    "id": file_id,
                    "filename": _display_name(
                        user_dir, path.parent, file_id, path.name
                    ),
                    "extension": path.suffix.lower(),
                    "size_bytes": metadata.st_size,
                    "modified_at": metadata.st_mtime,
                    "folder_path": folder_path,
                })
            except OSError:
                continue

    return results


def _search_files_unlocked(
    user_id: str,
    query: str,
    limit: int = 50,
) -> list[dict]:
    """Cari unggahan dan hasil AI berdasarkan nama atau teks ekstraksi."""
    query = str(query or "").strip().casefold()
    if not query:
        raise WorkspaceError("Kata pencarian tidak boleh kosong.")

    limit = max(1, min(int(limit), 100))
    results = []

    for label, user_dir in _workspace_user_dirs(user_id):
        for path in _original_files(user_dir):
            file_id = path.stem
            filename = _display_name(user_dir, path.parent, file_id, path.name)
            extract_path = path.parent / f"{file_id}.extract.txt"

            matched = query in filename.casefold()
            if not matched:
                try:
                    content = _read_workspace_text(
                        user_dir,
                        extract_path,
                        max_chars=200000,
                        errors="ignore",
                    )
                    matched = query in content.casefold()
                except OSError:
                    matched = False

            if matched:
                try:
                    relative_parent = path.parent.relative_to(user_dir).as_posix()
                    folder_path = "" if relative_parent == "." else relative_parent
                    if label:
                        folder_path = "/".join(
                            part for part in (label, folder_path) if part
                        )

                    results.append({
                        "id": file_id,
                        "filename": filename,
                        "extension": path.suffix.lower(),
                        "size_bytes": path.stat().st_size,
                        "folder_path": folder_path,
                    })
                except OSError:
                    continue

            if len(results) >= limit:
                return results

    return results


def _read_file_unlocked(
    user_id: str,
    file_id: str,
    max_chars: int = MAX_READ_CHARS,
) -> dict:
    """Baca teks ekstraksi file dari lokasi aslinya di Workspace pengguna."""
    label, user_dir, original = _find_file_with_root(user_id, file_id)
    file_id = original.stem
    extract_path = original.parent / f"{file_id}.extract.txt"

    try:
        max_chars = max(1, min(int(max_chars), MAX_READ_CHARS))

        content = _read_workspace_text(
            user_dir,
            extract_path,
            max_chars=max_chars + 1,
            errors="replace",
        )

        truncated = len(content) > max_chars
        content = content[:max_chars]

        relative_parent = original.parent.relative_to(user_dir).as_posix()
        folder_path = "" if relative_parent == "." else relative_parent
        if label:
            folder_path = "/".join(
                part for part in (label, folder_path) if part
            )

        return {
            "id": file_id,
            "filename": _display_name(
                user_dir,
                original.parent,
                file_id,
                original.name,
            ),
            "extension": original.suffix.lower(),
            "content": content,
            "chars_returned": len(content),
            "truncated": truncated,
            "folder_path": folder_path,
        }

    except OSError as exc:
        raise WorkspaceError("File tidak dapat dibaca.") from exc


def list_files(user_id: str, limit: int = 100) -> list[dict]:
    """Daftar file dengan penguncian storage per pengguna."""
    with user_storage_lock(user_id):
        return _list_files_unlocked(user_id, limit=limit)


def search_files(
    user_id: str,
    query: str,
    limit: int = 50,
) -> list[dict]:
    """Cari file dengan penguncian storage per pengguna."""
    with user_storage_lock(user_id):
        return _search_files_unlocked(user_id, query=query, limit=limit)


def read_file(
    user_id: str,
    file_id: str,
    max_chars: int = MAX_READ_CHARS,
) -> dict:
    """Baca teks file dengan penguncian storage per pengguna."""
    with user_storage_lock(user_id):
        return _read_file_unlocked(
            user_id,
            file_id,
            max_chars=max_chars,
        )


def open_file_for_download(user_id: str, file_id: str) -> tuple[int, str, str, int]:
    """Buka file asli secara aman tanpa mengikuti symlink pada komponen path."""
    _, user_dir, original = _find_file_with_root(user_id, file_id)

    try:
        relative = original.relative_to(user_dir)
    except ValueError as exc:
        raise WorkspaceError("Akses file ditolak.") from exc

    directory_fd = None
    file_fd = None

    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)

        directory_fd = os.open(user_dir, directory_flags | nofollow)

        for part in relative.parts[:-1]:
            next_fd = os.open(
                part,
                directory_flags | nofollow,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd

        file_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | nofollow,
            dir_fd=directory_fd,
        )

        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise WorkspaceError("Objek yang diminta bukan file biasa.")

        filename = _display_name(
            user_dir,
            original.parent,
            original.stem,
            original.name,
        )

        result = (file_fd, filename, original.suffix.lower(), metadata.st_size)
        file_fd = None
        return result

    except WorkspaceError:
        raise
    except OSError as exc:
        raise WorkspaceError("File tidak dapat dibuka dengan aman.") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)

