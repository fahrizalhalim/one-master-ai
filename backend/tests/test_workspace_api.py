"""Pengujian API Folder Workspace dengan SQLite sementara."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.database import Base, engine, SessionLocal
from app.models.user import User
from app.services.workspace_service import _validated_user_dir

client = TestClient(app)


def setup_function():
    Base.metadata.create_all(bind=engine)


def _register():
    email = f"workspace-{uuid.uuid4().hex[:12]}@test.ac.id"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"], email


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_workspace_requires_authentication():
    paths = [
        "/api/v1/files/workspace/folders",
        "/api/v1/files/workspace/files",
        "/api/v1/files/workspace/files/search?q=laporan",
        "/api/v1/files/workspace/files/abcdef123456",
    ]

    for path in paths:
        response = client.get(path)
        assert response.status_code in (401, 403), (
            path,
            response.status_code,
        )


def test_workspace_folder_create_list_rename_delete():
    token, _ = _register()
    headers = _auth(token)

    created = client.post(
        "/api/v1/files/workspace/folders",
        json={"name": "Dokumen"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["path"] == "Dokumen"

    nested = client.post(
        "/api/v1/files/workspace/folders",
        json={
            "name": "Penelitian",
            "parent_path": "Dokumen",
        },
        headers=headers,
    )
    assert nested.status_code == 200, nested.text

    listed = client.get(
        "/api/v1/files/workspace/folders",
        params={"parent_path": "Dokumen"},
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["folders"] == [
        {"name": "Penelitian", "path": "Dokumen/Penelitian"}
    ]

    renamed = client.patch(
        "/api/v1/files/workspace/folders/rename",
        json={
            "folder_path": "Dokumen/Penelitian",
            "new_name": "Riset",
        },
        headers=headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["path"] == "Dokumen/Riset"

    deleted = client.delete(
        "/api/v1/files/workspace/folders",
        params={"folder_path": "Dokumen/Riset"},
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["ok"] is True


def test_nonempty_folder_cannot_be_deleted():
    token, email = _register()
    headers = _auth(token)

    created = client.post(
        "/api/v1/files/workspace/folders",
        json={"name": "Penting"},
        headers=headers,
    )
    assert created.status_code == 200, created.text

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == email).first()
        assert owner is not None
        user_dir = _validated_user_dir(owner.id)
        important_file = user_dir / "Penting" / "data.txt"
        important_file.write_text("data harus tetap aman", encoding="utf-8")
    finally:
        db.close()

    deleted = client.delete(
        "/api/v1/files/workspace/folders",
        params={"folder_path": "Penting"},
        headers=headers,
    )

    assert deleted.status_code == 400
    assert important_file.read_text(encoding="utf-8") == "data harus tetap aman"


def test_users_cannot_access_each_others_folders():
    owner_token, _ = _register()
    other_token, _ = _register()

    created = client.post(
        "/api/v1/files/workspace/folders",
        json={"name": "Rahasia"},
        headers=_auth(owner_token),
    )
    assert created.status_code == 200, created.text

    other_access = client.get(
        "/api/v1/files/workspace/folders",
        params={"parent_path": "Rahasia"},
        headers=_auth(other_token),
    )
    assert other_access.status_code == 400

    other_root = client.get(
        "/api/v1/files/workspace/folders",
        headers=_auth(other_token),
    )
    assert other_root.status_code == 200
    assert other_root.json()["folders"] == []


def test_workspace_rejects_path_traversal():
    token, _ = _register()

    response = client.post(
        "/api/v1/files/workspace/folders",
        json={
            "name": "TidakAman",
            "parent_path": "../pengguna_lain",
        },
        headers=_auth(token),
    )
    assert response.status_code == 400


def test_workspace_file_list_search_and_read():
    token, email = _register()
    headers = _auth(token)

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == email).first()
        assert owner is not None
        user_dir = _validated_user_dir(owner.id, create=True)
    finally:
        db.close()

    folder = user_dir / "Dokumen"
    folder.mkdir()

    file_id = "1234abcdef56"
    (folder / f"{file_id}.txt").write_text(
        "Isi laporan penelitian khusus Workspace.",
        encoding="utf-8",
    )
    (folder / f"{file_id}.name.txt").write_text(
        "laporan-penelitian.txt",
        encoding="utf-8",
    )
    (folder / f"{file_id}.extract.txt").write_text(
        "Isi laporan penelitian khusus Workspace.",
        encoding="utf-8",
    )

    listed = client.get(
        "/api/v1/files/workspace/files",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text

    matching = [
        item for item in listed.json()["files"]
        if item["id"] == file_id
    ]
    assert len(matching) == 1
    assert matching[0]["filename"] == "laporan-penelitian.txt"
    assert matching[0]["folder_path"] == "Dokumen"

    searched = client.get(
        "/api/v1/files/workspace/files/search",
        params={"q": "penelitian khusus"},
        headers=headers,
    )
    assert searched.status_code == 200, searched.text
    assert any(
        item["id"] == file_id
        for item in searched.json()["files"]
    )

    read = client.get(
        f"/api/v1/files/workspace/files/{file_id}",
        headers=headers,
    )
    assert read.status_code == 200, read.text
    assert read.json()["filename"] == "laporan-penelitian.txt"
    assert read.json()["folder_path"] == "Dokumen"
    assert "penelitian khusus Workspace" in read.json()["content"]



def test_generated_file_workspace_isolation_download_rename_delete_and_quota():
    import uuid

    from app.services.storage_service import calculate_storage_usage
    from app.services.telegram_workspace_service import (
        rename_user_file,
        delete_user_file,
    )
    from app.services.workspace_service import GENERATED_ROOT, UPLOAD_ROOT

    owner_token, owner_email = _register()
    other_token, _ = _register()
    owner_headers = _auth(owner_token)
    other_headers = _auth(other_token)

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == owner_email).first()
        assert owner is not None
        user_id = str(owner.id)
    finally:
        db.close()

    file_id = uuid.uuid4().hex[:12]
    generated_dir = _validated_user_dir(
        user_id, upload_root=GENERATED_ROOT, create=True
    )
    upload_dir = _validated_user_dir(
        user_id, upload_root=UPLOAD_ROOT, create=True
    )

    generated_file = generated_dir / f"{file_id}.txt"
    name_file = generated_dir / f"{file_id}.name.txt"
    extract_file = generated_dir / f"{file_id}.extract.txt"
    upload_file = upload_dir / f"{uuid.uuid4().hex[:12]}.txt"

    content = "Hasil AI untuk pengujian integrasi Workspace."
    generated_file.write_text(content, encoding="utf-8")
    name_file.write_text("hasil-ai-uji.txt", encoding="utf-8")
    extract_file.write_text(content, encoding="utf-8")
    upload_file.write_bytes(b"U" * 123)

    try:
        # File AI tampil pada daftar File Saya di lokasi Hasil AI.
        listed = client.get(
            "/api/v1/files/workspace/files",
            headers=owner_headers,
        )
        assert listed.status_code == 200, listed.text
        item = next(
            (entry for entry in listed.json()["files"]
             if entry["id"] == file_id),
            None,
        )
        assert item is not None
        assert item["filename"] == "hasil-ai-uji.txt"
        assert item["folder_path"] == "Hasil AI"

        # Pencarian dan pembacaan file AI.
        searched = client.get(
            "/api/v1/files/workspace/files/search",
            params={"q": "pengujian integrasi Workspace"},
            headers=owner_headers,
        )
        assert searched.status_code == 200, searched.text
        assert any(
            entry["id"] == file_id for entry in searched.json()["files"]
        )

        read = client.get(
            f"/api/v1/files/workspace/files/{file_id}",
            headers=owner_headers,
        )
        assert read.status_code == 200, read.text
        assert content in read.json()["content"]
        assert read.json()["folder_path"] == "Hasil AI"

        # Pemilik dapat mengunduh file asli.
        downloaded = client.get(
            f"/api/v1/files/{file_id}/download",
            headers=owner_headers,
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == content.encode("utf-8")

        # Pengguna lain tidak dapat membaca atau mengunduh file tersebut.
        other_read = client.get(
            f"/api/v1/files/workspace/files/{file_id}",
            headers=other_headers,
        )
        assert other_read.status_code == 404, other_read.text

        other_download = client.get(
            f"/api/v1/files/{file_id}/download",
            headers=other_headers,
        )
        assert other_download.status_code == 404, other_download.text

        # File AI tidak dihitung dalam kuota direktori upload.
        assert calculate_storage_usage(upload_dir) == 123

        # Rename mempertahankan ekstensi dan memperbarui nama tampilan.
        rename_user_file(user_id, file_id, "hasil-ai-diubah.txt")
        assert name_file.read_text(encoding="utf-8") == "hasil-ai-diubah.txt"

        renamed = client.get(
            f"/api/v1/files/workspace/files/{file_id}",
            headers=owner_headers,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["filename"] == "hasil-ai-diubah.txt"

        # Hapus file beserta sidecar-nya.
        delete_user_file(user_id, file_id)
        assert not generated_file.exists()
        assert not name_file.exists()
        assert not extract_file.exists()

        missing = client.get(
            f"/api/v1/files/{file_id}/download",
            headers=owner_headers,
        )
        assert missing.status_code == 404, missing.text
    finally:
        # Hanya membersihkan artefak yang dibuat tes ini.
        for path in (generated_file, name_file, extract_file, upload_file):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
