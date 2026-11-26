from uuid import uuid4

import pytest

from app.services import document_service as ds


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "STORAGE_DIR", tmp_path)
    return str(uuid4())


def test_same_id_same_content_reuses_file(tmp_path, isolated_storage):
    user_id = isolated_storage
    first = ds.create_document(
        "Laporan Uji", "Isi dokumen yang sama", user_id, "txt",
        file_id="a1b2c3d4e5f6",
    )
    original = first.read_bytes()

    second = ds.create_document(
        "Laporan Uji", "Isi dokumen yang sama", user_id, "txt",
        file_id="a1b2c3d4e5f6",
    )

    assert second == first
    assert second.read_bytes() == original
    assert len(list(first.parent.glob("a1b2c3d4e5f6.txt"))) == 1


def test_same_id_different_content_is_rejected(isolated_storage):
    user_id = isolated_storage
    ds.create_document(
        "Laporan Uji", "Isi pertama", user_id, "txt",
        file_id="a1b2c3d4e5f6",
    )

    with pytest.raises(FileExistsError):
        ds.create_document(
            "Laporan Uji", "Isi berbeda", user_id, "txt",
            file_id="a1b2c3d4e5f6",
        )


def test_missing_metadata_is_recovered(isolated_storage):
    user_id = isolated_storage
    first = ds.create_document(
        "Laporan Uji", "Isi untuk pemulihan", user_id, "txt",
        file_id="a1b2c3d4e5f6",
    )
    original = first.read_bytes()

    (first.parent / "a1b2c3d4e5f6.name.txt").unlink()
    (first.parent / "a1b2c3d4e5f6.extract.txt").unlink()

    recovered = ds.create_document(
        "Laporan Uji", "Isi untuk pemulihan", user_id, "txt",
        file_id="a1b2c3d4e5f6",
    )

    assert recovered.read_bytes() == original
    assert (first.parent / "a1b2c3d4e5f6.name.txt").read_text() == "Laporan_Uji.txt"
    assert (first.parent / "a1b2c3d4e5f6.extract.txt").read_text() == "Isi untuk pemulihan"


def test_manifest_symlink_is_rejected(tmp_path, isolated_storage):
    user_id = isolated_storage
    user_dir = ds._user_storage_dir(user_id)
    outside = tmp_path / "outside.json"
    outside.write_text("{}")

    (user_dir / ".a1b2c3d4e5f6.request.json").symlink_to(outside)

    with pytest.raises(FileExistsError):
        ds.create_document(
            "Laporan Uji", "Isi dokumen", user_id, "txt",
            file_id="a1b2c3d4e5f6",
        )


def test_invalid_file_id_is_rejected(isolated_storage):
    with pytest.raises(ValueError):
        ds.create_document(
            "Laporan Uji", "Isi dokumen", isolated_storage, "txt",
            file_id="../outside",
        )
