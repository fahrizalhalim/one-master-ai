"""Pengujian keamanan dan operasi Folder Workspace."""

import uuid

import pytest

from app.services import workspace_service
from app.services.workspace_folders import (
    create_folder,
    list_folders,
    rename_folder,
    delete_empty_folder,
)
from app.services.workspace_service import WorkspaceError


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(workspace_service, "UPLOAD_ROOT", root)
    return root


@pytest.fixture
def user_id():
    return str(uuid.uuid4())


def test_create_and_list_nested_folders(workspace_root, user_id):
    created = create_folder(user_id, "Dokumen")
    assert created["path"] == "Dokumen"

    nested = create_folder(user_id, "Penelitian", "Dokumen")
    assert nested["path"] == "Dokumen/Penelitian"

    folders = list_folders(user_id)
    assert folders == [{"name": "Dokumen", "path": "Dokumen"}]

    nested_folders = list_folders(user_id, "Dokumen")
    assert nested_folders == [
        {"name": "Penelitian", "path": "Dokumen/Penelitian"}
    ]


def test_duplicate_folder_is_rejected(workspace_root, user_id):
    create_folder(user_id, "Dokumen")

    with pytest.raises(WorkspaceError):
        create_folder(user_id, "Dokumen")


def test_rename_folder_preserves_contents(workspace_root, user_id):
    create_folder(user_id, "Lama")
    user_dir = workspace_root / str(uuid.UUID(user_id))
    original = user_dir / "Lama" / "catatan.txt"
    original.write_text("data tetap aman", encoding="utf-8")

    result = rename_folder(user_id, "Lama", "Baru")

    assert result["path"] == "Baru"
    assert not (user_dir / "Lama").exists()
    assert (user_dir / "Baru" / "catatan.txt").read_text(
        encoding="utf-8"
    ) == "data tetap aman"


def test_nonempty_folder_cannot_be_deleted(workspace_root, user_id):
    create_folder(user_id, "Penting")
    user_dir = workspace_root / str(uuid.UUID(user_id))
    important_file = user_dir / "Penting" / "data.txt"
    important_file.write_text("jangan hapus", encoding="utf-8")

    with pytest.raises(WorkspaceError):
        delete_empty_folder(user_id, "Penting")

    assert important_file.is_file()


def test_empty_folder_can_be_deleted(workspace_root, user_id):
    create_folder(user_id, "Kosong")

    result = delete_empty_folder(user_id, "Kosong")

    assert result["ok"] is True
    assert not (workspace_root / str(uuid.UUID(user_id)) / "Kosong").exists()


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "../rahasia", "a/b", r"a\\b", ".tersembunyi"],
)
def test_invalid_folder_names_are_rejected(workspace_root, user_id, name):
    with pytest.raises(WorkspaceError):
        create_folder(user_id, name)


def test_path_traversal_is_rejected(workspace_root, user_id):
    with pytest.raises(WorkspaceError):
        create_folder(user_id, "Baru", "../pengguna_lain")

    with pytest.raises(WorkspaceError):
        list_folders(user_id, "/etc")


def test_users_cannot_access_each_others_folders(workspace_root):
    owner = str(uuid.uuid4())
    other = str(uuid.uuid4())

    create_folder(owner, "Rahasia")

    with pytest.raises(WorkspaceError):
        list_folders(other, "Rahasia")

    assert list_folders(other) == []


def test_symlink_folder_cannot_be_traversed(workspace_root, user_id):
    create_folder(user_id, "Aman")

    user_dir = workspace_root / str(uuid.UUID(user_id))
    outside = workspace_root.parent / "outside"
    outside.mkdir()

    (user_dir / "jalan_keluar").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceError):
        list_folders(user_id, "jalan_keluar")

    with pytest.raises(WorkspaceError):
        create_folder(user_id, "TidakAman", "jalan_keluar")


def test_root_folder_cannot_be_renamed_or_deleted(workspace_root, user_id):
    with pytest.raises(WorkspaceError):
        rename_folder(user_id, "", "Baru")

    with pytest.raises(WorkspaceError):
        delete_empty_folder(user_id, "")
