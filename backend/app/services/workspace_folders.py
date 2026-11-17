"""Operasi folder Workspace personal yang dibatasi pada direktori setiap pengguna."""

from pathlib import Path

from app.services.workspace_service import (
    WorkspaceError,
    _validated_user_dir,
)
from app.services.storage_service import user_storage_lock


MAX_FOLDER_NAME = 80


def _validate_name(name: str) -> str:
    name = str(name or "").strip()

    if (
        not name
        or len(name) > MAX_FOLDER_NAME
        or name in {".", ".."}
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise WorkspaceError("Nama folder tidak valid.")

    return name


def _parts(relative_path: str | None) -> list[str]:
    if relative_path is None or relative_path == "":
        return []

    if not isinstance(relative_path, str):
        raise WorkspaceError("Lokasi folder tidak valid.")

    if relative_path.startswith("/") or "\\" in relative_path:
        raise WorkspaceError("Lokasi folder tidak valid.")

    parts = relative_path.split("/")

    if any(
        not part
        or part in {".", ".."}
        or part.startswith(".")
        or len(part) > MAX_FOLDER_NAME
        or any(ord(char) < 32 or ord(char) == 127 for char in part)
        for part in parts
    ):
        raise WorkspaceError("Lokasi folder tidak valid.")

    return parts


def _safe_directory(root: Path, parts: list[str]) -> Path:
    """Telusuri direktori satu per satu dan tolak symlink."""
    current = root

    for part in parts:
        current = current / part

        if current.is_symlink():
            raise WorkspaceError("Akses folder tidak aman.")

        if not current.exists() or not current.is_dir():
            raise WorkspaceError("Folder tidak ditemukan.")

        resolved = current.resolve(strict=True)

        if not resolved.is_relative_to(root):
            raise WorkspaceError("Akses folder ditolak.")

        current = resolved

    return current


def create_folder(
    user_id: str,
    name: str,
    parent_path: str = "",
) -> dict:
    """Buat folder baru di dalam Workspace pengguna."""
    name = _validate_name(name)
    parts = _parts(parent_path)

    with user_storage_lock(user_id):
        root = _validated_user_dir(user_id, create=True)
        parent = _safe_directory(root, parts)
        target = parent / name

        if target.is_symlink():
            raise WorkspaceError("Folder tujuan tidak aman.")

        if target.exists():
            raise WorkspaceError("Folder dengan nama tersebut sudah ada.")

        try:
            target.mkdir()
        except FileExistsError as exc:
            raise WorkspaceError("Folder dengan nama tersebut sudah ada.") from exc
        except OSError as exc:
            raise WorkspaceError("Folder tidak dapat dibuat.") from exc

        return {
            "name": name,
            "path": "/".join(parts + [name]),
        }


def list_folders(
    user_id: str,
    parent_path: str = "",
) -> list[dict]:
    """Daftar subfolder langsung pada lokasi Workspace yang diminta."""
    parts = _parts(parent_path)

    with user_storage_lock(user_id):
        root = _validated_user_dir(user_id, create=True)
        parent = _safe_directory(root, parts)
        results = []

        try:
            children = sorted(parent.iterdir(), key=lambda item: item.name.casefold())

            for child in children:
                if child.name.startswith(".") or child.is_symlink():
                    continue

                try:
                    if child.is_dir():
                        results.append({
                            "name": child.name,
                            "path": "/".join(parts + [child.name]),
                        })
                except OSError:
                    continue

        except OSError as exc:
            raise WorkspaceError("Daftar folder tidak dapat dibaca.") from exc

        return results


def rename_folder(
    user_id: str,
    folder_path: str,
    new_name: str,
) -> dict:
    """Ganti nama folder tanpa memindahkan isinya."""
    parts = _parts(folder_path)
    if not parts:
        raise WorkspaceError("Folder utama tidak dapat diganti namanya.")

    new_name = _validate_name(new_name)
    parent_parts = parts[:-1]
    old_name = parts[-1]

    with user_storage_lock(user_id):
        root = _validated_user_dir(user_id)
        parent = _safe_directory(root, parent_parts)
        source = parent / old_name

        if source.is_symlink() or not source.is_dir():
            raise WorkspaceError("Folder tidak ditemukan.")

        destination = parent / new_name

        if destination.is_symlink() or destination.exists():
            raise WorkspaceError("Nama folder tujuan sudah digunakan.")

        try:
            source.rename(destination)
        except OSError as exc:
            raise WorkspaceError("Folder tidak dapat diganti namanya.") from exc

        return {
            "old_path": "/".join(parts),
            "name": new_name,
            "path": "/".join(parent_parts + [new_name]),
        }


def delete_empty_folder(
    user_id: str,
    folder_path: str,
) -> dict:
    """Hapus folder kosong saja; tidak pernah menghapus isi secara rekursif."""
    parts = _parts(folder_path)
    if not parts:
        raise WorkspaceError("Folder utama tidak dapat dihapus.")

    parent_parts = parts[:-1]
    folder_name = parts[-1]

    with user_storage_lock(user_id):
        root = _validated_user_dir(user_id)
        parent = _safe_directory(root, parent_parts)
        target = parent / folder_name

        if target.is_symlink() or not target.is_dir():
            raise WorkspaceError("Folder tidak ditemukan.")

        try:
            target.rmdir()
        except OSError as exc:
            # rmdir hanya menghapus direktori kosong.
            raise WorkspaceError(
                "Folder tidak kosong atau tidak dapat dihapus. "
                "Pindahkan atau hapus isinya terlebih dahulu."
            ) from exc

        return {
            "ok": True,
            "deleted_path": "/".join(parts),
        }
