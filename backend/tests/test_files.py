"""Tes upload & ekstraksi file."""
import io
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_files.db")

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.database import Base, engine, SessionLocal
from app.models.user import User  # noqa: E402
from app.api.routes_files import _user_dir  # noqa: E402
from app.services.workspace_service import list_files  # noqa: E402

client = TestClient(app)

EMAIL = "file@test.ac.id"
PASSWORD = "password123"


def setup_function():
    Base.metadata.create_all(bind=engine)


def _approve_all():
    """Setujui semua user pending langsung via DB (test helper)."""
    db = SessionLocal()
    try:
        db.query(User).filter(User.status == "pending").update(
            {"status": "active"}, synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def _token() -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD},
    )
    if r.status_code != 201:
        r = client.post(
            "/api/v1/auth/login",
            data={"username": EMAIL, "password": PASSWORD},
        )
    _approve_all()
    return r.json()["access_token"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_upload_txt():
    t = _token()
    r = client.post(
        "/api/v1/files/upload",
        files={"file": ("catatan.txt", io.BytesIO("Ini isi catatan penting.".encode()), "text/plain")},
        headers=_auth(t),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["chars"] > 0
    assert data["filename"] == "catatan.txt"


def test_upload_docx():
    t = _token()
    # buat DOCX di memori
    doc = Document()
    doc.add_heading("Judul Dokumen", level=1)
    doc.add_paragraph("Isi paragraf satu.")
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    r = client.post(
        "/api/v1/files/upload",
        files={"file": ("dok.docx", buf, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers=_auth(t),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["chars"] > 10

    # ambil teks
    r2 = client.get(f"/api/v1/files/{data['id']}", headers=_auth(t))
    assert r2.status_code == 200
    assert "Judul Dokumen" in r2.json()["total_text"]


def test_upload_reject_bad_ext():
    t = _token()
    r = client.post(
        "/api/v1/files/upload",
        files={"file": ("virus.exe", io.BytesIO(b"MZ..."), "application/x-msdownload")},
        headers=_auth(t),
    )
    assert r.status_code == 400


def test_upload_requires_token():
    r = client.post(
        "/api/v1/files/upload",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert r.status_code in (401, 403)


def test_file_isolation():
    t = _token()
    r = client.post(
        "/api/v1/files/upload",
        files={"file": ("privat.txt", io.BytesIO("rahasia".encode()), "text/plain")},
        headers=_auth(t),
    )
    fid = r.json()["id"]

    # user lain tidak bisa akses
    r2 = client.post(
        "/api/v1/auth/register",
        json={"email": "lain@test.ac.id", "password": "password123"},
    )
    t2 = r2.json()["access_token"]
    r3 = client.get(f"/api/v1/files/{fid}", headers=_auth(t2))
    assert r3.status_code == 404


def test_delete_file_removes_original_and_sidecars():
    token = _token()

    uploaded = client.post(
        "/api/v1/files/upload",
        files={"file": ("hapus.txt", io.BytesIO(b"konten untuk dihapus"), "text/plain")},
        headers=_auth(token),
    )
    assert uploaded.status_code == 200, uploaded.text
    file_id = uploaded.json()["id"]

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == EMAIL).first()
        assert owner is not None
        user_dir = _user_dir(owner.id)
    finally:
        db.close()

    original = user_dir / f"{file_id}.txt"
    extract = user_dir / f"{file_id}.extract.txt"
    name = user_dir / f"{file_id}.name.txt"
    name.write_text("hapus.txt", encoding="utf-8")

    response = client.delete(
        f"/api/v1/files/{file_id}",
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert not original.exists()
    assert not extract.exists()
    assert not name.exists()


def test_other_user_cannot_delete_file():
    owner_token = _token()

    uploaded = client.post(
        "/api/v1/files/upload",
        files={"file": ("rahasia.txt", io.BytesIO(b"jangan hapus"), "text/plain")},
        headers=_auth(owner_token),
    )
    assert uploaded.status_code == 200, uploaded.text
    file_id = uploaded.json()["id"]

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == EMAIL).first()
        assert owner is not None
        original = _user_dir(owner.id) / f"{file_id}.txt"
    finally:
        db.close()

    other = client.post(
        "/api/v1/auth/register",
        json={"email": "delete-lain@test.ac.id", "password": PASSWORD},
    )
    assert other.status_code == 201, other.text
    other_token = other.json()["access_token"]

    response = client.delete(
        f"/api/v1/files/{file_id}",
        headers=_auth(other_token),
    )

    assert response.status_code == 404, response.text
    assert original.is_file(), "File pemilik pertama tidak boleh terhapus."
    other_token = other.json()["access_token"]

    response = client.delete(
        f"/api/v1/files/{file_id}",
        headers=_auth(other_token),
    )
    assert response.status_code == 404

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == EMAIL).first()
        assert owner is not None
        original = _user_dir(owner.id) / f"{file_id}.txt"
        assert original.exists()
    finally:
        db.close()


def test_delete_file_rejects_invalid_and_missing_ids():
    token = _token()

    invalid = client.delete(
        "/api/v1/files/not-valid",
        headers=_auth(token),
    )
    assert invalid.status_code == 400

    missing = client.delete(
        "/api/v1/files/abcdef123456",
        headers=_auth(token),
    )
    assert missing.status_code == 404


def test_upload_preserves_original_filename_in_workspace():
    token = _token()

    uploaded = client.post(
        "/api/v1/files/upload",
        files={
            "file": (
                "laporan_penelitian.txt",
                io.BytesIO(b"Isi laporan penelitian."),
                "text/plain",
            )
        },
        headers=_auth(token),
    )

    assert uploaded.status_code == 200, uploaded.text
    file_id = uploaded.json()["id"]

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == EMAIL).first()
        assert owner is not None

        user_dir = _user_dir(owner.id)
        metadata = user_dir / f"{file_id}.name.txt"

        assert metadata.is_file()
        assert metadata.read_text(encoding="utf-8") == "laporan_penelitian.txt"

        files = list_files(owner.id)
        matching = [item for item in files if item["id"] == file_id]

        assert len(matching) == 1
        assert matching[0]["filename"] == "laporan_penelitian.txt"
    finally:
        db.close()


def test_upload_sanitizes_path_from_filename():
    token = _token()

    uploaded = client.post(
        "/api/v1/files/upload",
        files={
            "file": (
                "../../rahasia.txt",
                io.BytesIO(b"Konten aman."),
                "text/plain",
            )
        },
        headers=_auth(token),
    )

    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["filename"] == "rahasia.txt"


def test_upload_read_and_delete_file_inside_folder():
    from app.services.workspace_folders import create_folder

    token = _token()

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == EMAIL).first()
        assert owner is not None
        user_id = owner.id
    finally:
        db.close()

    create_folder(user_id, "Dokumen")
    create_folder(user_id, "Penelitian", "Dokumen")

    uploaded = client.post(
        "/api/v1/files/upload",
        data={"folder_path": "Dokumen/Penelitian"},
        files={"file": ("laporan.txt", io.BytesIO(b"Isi laporan folder."), "text/plain")},
        headers=_auth(token),
    )

    assert uploaded.status_code == 200, uploaded.text
    data = uploaded.json()
    assert data["folder_path"] == "Dokumen/Penelitian"

    listed = list_files(user_id)
    matching = [item for item in listed if item["id"] == data["id"]]
    assert len(matching) == 1
    assert matching[0]["folder_path"] == "Dokumen/Penelitian"

    read = client.get(
        f"/api/v1/files/{data['id']}",
        headers=_auth(token),
    )
    assert read.status_code == 200, read.text
    assert read.json()["folder_path"] == "Dokumen/Penelitian"
    assert read.json()["filename"] == "laporan.txt"
    assert "Isi laporan folder." in read.json()["total_text"]

    deleted = client.delete(
        f"/api/v1/files/{data['id']}",
        headers=_auth(token),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["ok"] is True

    assert not matching[0].get("filename") == ""


def test_upload_rejects_nonexistent_or_traversal_folder():
    token = _token()

    traversal = client.post(
        "/api/v1/files/upload",
        data={"folder_path": "../pengguna_lain"},
        files={"file": ("aman.txt", io.BytesIO(b"konten"), "text/plain")},
        headers=_auth(token),
    )
    assert traversal.status_code == 400, traversal.text

    missing = client.post(
        "/api/v1/files/upload",
        data={"folder_path": "folder-belum-ada"},
        files={"file": ("aman.txt", io.BytesIO(b"konten"), "text/plain")},
        headers=_auth(token),
    )
    assert missing.status_code == 400, missing.text


def test_list_files_finds_files_in_nested_folder():
    from app.services.workspace_folders import create_folder

    token = _token()

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == EMAIL).first()
        assert owner is not None
        user_id = owner.id
    finally:
        db.close()

    create_folder(user_id, "Arsip")

    uploaded = client.post(
        "/api/v1/files/upload",
        data={"folder_path": "Arsip"},
        files={"file": ("arsip.txt", io.BytesIO(b"data arsip"), "text/plain")},
        headers=_auth(token),
    )
    assert uploaded.status_code == 200, uploaded.text

    results = list_files(user_id)
    found = [item for item in results if item["id"] == uploaded.json()["id"]]

    assert len(found) == 1
    assert found[0]["folder_path"] == "Arsip"


def test_symlink_extract_cannot_be_read_or_delete_external_target(tmp_path):
    token = _token()

    uploaded = client.post(
        "/api/v1/files/upload",
        files={
            "file": (
                "symlink-test.txt",
                io.BytesIO(b"konten asli"),
                "text/plain",
            )
        },
        headers=_auth(token),
    )
    assert uploaded.status_code == 200, uploaded.text
    file_id = uploaded.json()["id"]

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == EMAIL).first()
        assert owner is not None
        user_dir = _user_dir(owner.id)
    finally:
        db.close()

    original = user_dir / f"{file_id}.txt"
    extract = user_dir / f"{file_id}.extract.txt"

    outside_file = tmp_path / "data-di-luar-workspace.txt"
    outside_file.write_text(
        "RAHASIA DI LUAR WORKSPACE",
        encoding="utf-8",
    )

    extract.unlink()
    extract.symlink_to(outside_file)

    read = client.get(
        f"/api/v1/files/{file_id}",
        headers=_auth(token),
    )
    assert read.status_code == 404

    deleted = client.delete(
        f"/api/v1/files/{file_id}",
        headers=_auth(token),
    )
    assert deleted.status_code == 200, deleted.text

    assert not original.exists()
    assert outside_file.is_file()
    assert outside_file.read_text(encoding="utf-8") == (
        "RAHASIA DI LUAR WORKSPACE"
    )


def test_duplicate_file_id_in_different_folders_is_rejected():
    import pytest

    from app.services.workspace_service import (
        WorkspaceError,
        find_file_by_id,
    )

    token = _token()

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == EMAIL).first()
        assert owner is not None
        user_dir = _user_dir(owner.id)
    finally:
        db.close()

    file_id = "abcdef123456"
    folder_a = user_dir / "Uji-Duplikat-A"
    folder_b = user_dir / "Uji-Duplikat-B"

    folder_a.mkdir(exist_ok=True)
    folder_b.mkdir(exist_ok=True)

    (folder_a / f"{file_id}.txt").write_text(
        "file pertama",
        encoding="utf-8",
    )
    (folder_b / f"{file_id}.txt").write_text(
        "file kedua",
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceError, match="lebih dari satu"):
        find_file_by_id(owner.id, file_id)

    # Bersihkan hanya dua file uji dan folder uji yang dibuat tes ini.
    (folder_a / f"{file_id}.txt").unlink()
    (folder_b / f"{file_id}.txt").unlink()
    folder_a.rmdir()
    folder_b.rmdir()

def test_download_original_file():
    token = _token()
    content = b"Isi asli dokumen yang harus diunduh."

    uploaded = client.post(
        "/api/v1/files/upload",
        files={"file": ("arsip.txt", io.BytesIO(content), "text/plain")},
        headers=_auth(token),
    )
    assert uploaded.status_code == 200, uploaded.text
    file_id = uploaded.json()["id"]

    response = client.get(
        f"/api/v1/files/{file_id}/download",
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    assert response.content == content
    assert "attachment" in response.headers["content-disposition"]
    assert "arsip.txt" in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-length"] == str(len(content))


def test_download_file_inside_nested_folder():
    from app.services.workspace_folders import create_folder

    token = _token()
    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == EMAIL).first()
        assert owner is not None
        user_id = owner.id
    finally:
        db.close()

    create_folder(user_id, "Unduhan-Uji")
    create_folder(user_id, "Subfolder", "Unduhan-Uji")

    content = b"Konten file di subfolder."
    uploaded = client.post(
        "/api/v1/files/upload",
        data={"folder_path": "Unduhan-Uji/Subfolder"},
        files={"file": ("subfolder.txt", io.BytesIO(content), "text/plain")},
        headers=_auth(token),
    )
    assert uploaded.status_code == 200, uploaded.text

    response = client.get(
        f"/api/v1/files/{uploaded.json()['id']}/download",
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    assert response.content == content


def test_other_user_cannot_download_file():
    owner_token = _token()
    content = b"Konten rahasia pemilik."

    uploaded = client.post(
        "/api/v1/files/upload",
        files={"file": ("rahasia-download.txt", io.BytesIO(content), "text/plain")},
        headers=_auth(owner_token),
    )
    assert uploaded.status_code == 200, uploaded.text
    file_id = uploaded.json()["id"]

    other = client.post(
        "/api/v1/auth/register",
        json={
            "email": "download-lain@test.ac.id",
            "password": PASSWORD,
        },
    )
    assert other.status_code == 201, other.text
    _approve_all()

    response = client.get(
        f"/api/v1/files/{file_id}/download",
        headers=_auth(other.json()["access_token"]),
    )

    assert response.status_code == 404


def test_download_rejects_original_symlink(tmp_path):
    token = _token()

    uploaded = client.post(
        "/api/v1/files/upload",
        files={"file": ("symlink-download.txt", io.BytesIO(b"konten asli"), "text/plain")},
        headers=_auth(token),
    )
    assert uploaded.status_code == 200, uploaded.text
    file_id = uploaded.json()["id"]

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == EMAIL).first()
        assert owner is not None
        user_dir = _user_dir(owner.id)
    finally:
        db.close()

    original = user_dir / f"{file_id}.txt"
    outside_file = tmp_path / "private-outside.txt"
    outside_content = b"JANGAN DIBACA DARI LUAR WORKSPACE"
    outside_file.write_bytes(outside_content)

    original.unlink()
    original.symlink_to(outside_file)

    response = client.get(
        f"/api/v1/files/{file_id}/download",
        headers=_auth(token),
    )

    assert response.status_code == 404
    assert outside_file.read_bytes() == outside_content


def test_download_requires_token():
    response = client.get("/api/v1/files/012345abcdef/download")
    assert response.status_code in (401, 403)


def test_workspace_exclusive_write_rejects_existing_file_and_symlink(tmp_path):
    from app.services.workspace_service import (
        _write_workspace_file_exclusive,
    )

    user_dir = tmp_path / "workspace"
    user_dir.mkdir()

    # File baru harus berhasil dibuat.
    original = user_dir / "new-file.txt"
    _write_workspace_file_exclusive(user_dir, original, b"konten asli")
    assert original.read_bytes() == b"konten asli"

    # File yang sudah ada tidak boleh ditimpa.
    try:
        _write_workspace_file_exclusive(user_dir, original, b"konten pengganti")
        assert False, "File yang sudah ada seharusnya ditolak."
    except FileExistsError:
        pass

    assert original.read_bytes() == b"konten asli"

    # Symlink tidak boleh diikuti atau menimpa target di luar Workspace.
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"data rahasia")
    link = user_dir / "linked-file.txt"
    link.symlink_to(outside)

    try:
        _write_workspace_file_exclusive(user_dir, link, b"konten berbahaya")
        assert False, "Symlink seharusnya ditolak."
    except FileExistsError:
        pass

    assert outside.read_bytes() == b"data rahasia"
    assert link.is_symlink()


def test_workspace_open_rejects_symlink_parent_and_non_directory(tmp_path):
    import pytest
    from app.services.workspace_service import _open_workspace_regular_file

    user_dir = tmp_path / "workspace"
    user_dir.mkdir()

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("data rahasia", encoding="utf-8")

    link_dir = user_dir / "linked"
    link_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        _open_workspace_regular_file(
            user_dir,
            link_dir / "secret.txt",
        )

    regular_file = user_dir / "regular.txt"
    regular_file.write_text("konten biasa", encoding="utf-8")

    with pytest.raises(OSError):
        _open_workspace_regular_file(
            user_dir,
            regular_file / "child.txt",
        )

    assert outside_file.read_text(encoding="utf-8") == "data rahasia"


def test_workspace_unlink_is_safe_with_symlink_and_missing_file(tmp_path):
    import pytest
    from app.services.workspace_service import _unlink_workspace_file

    user_dir = tmp_path / "workspace"
    user_dir.mkdir()

    # File biasa dapat dihapus.
    original = user_dir / "original.txt"
    original.write_text("konten asli", encoding="utf-8")

    assert _unlink_workspace_file(user_dir, original) is True
    assert not original.exists()

    # File yang tidak ditemukan ditangani tanpa error.
    assert _unlink_workspace_file(user_dir, original) is False

    # Symlink yang dihapus tidak boleh menghapus target eksternal.
    outside = tmp_path / "outside.txt"
    outside.write_text("data rahasia", encoding="utf-8")

    link = user_dir / "linked.txt"
    link.symlink_to(outside)

    assert _unlink_workspace_file(user_dir, link) is True
    assert not link.is_symlink()
    assert outside.read_text(encoding="utf-8") == "data rahasia"

    # Path di luar Workspace harus ditolak.
    with pytest.raises(OSError):
        _unlink_workspace_file(user_dir, outside)

    assert outside.read_text(encoding="utf-8") == "data rahasia"
