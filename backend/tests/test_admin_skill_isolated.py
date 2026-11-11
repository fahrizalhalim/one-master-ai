"""Test isolated: skill khusus admin (tulisan natural) + routing skill baru.

Tanpa DB — hanya routing & gate role.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.skills.skill_router import route_skill  # noqa: E402
from app.services.master_agent import (  # noqa: E402
    ADMIN_ONLY_SKILLS,
    prepare_master_prompt,
)


def test_route_admin_folder():
    assert route_skill("humanisasi tulisan ini") == "admin"
    assert route_skill("buat tulisan natural, jangan kesan ai") == "admin"
    assert route_skill("edit agar terdengar natural") == "admin"


def test_admin_skill_blocked_for_user_role():
    prepared = prepare_master_prompt(
        "humanisasi tulisan ini", user_role="user"
    )
    assert prepared["skill"] == "general"
    assert "Tulisan Natural" not in prepared["prompt"]


def test_admin_skill_allowed_for_admin_role():
    prepared = prepare_master_prompt(
        "humanisasi tulisan ini", user_role="admin"
    )
    assert prepared["skill"] == "admin"
    assert "Tulisan Natural" in prepared["prompt"]


def test_admin_folder_in_admin_only_set():
    assert "admin" in ADMIN_ONLY_SKILLS


def test_routing_new_skills():
    assert route_skill("saya peneliti mau bikin systematic review") == "research"
    assert route_skill("hitung ukuran sampel power analysis") == "research"
    assert route_skill("desain penelitian fenomenologi") == "research"
    assert route_skill("jawab reviewer 2 jurnal saya") == "research"
    assert route_skill("saya mau ikut satria 2026") == "research"
    assert route_skill("bantu saya belajar untuk ujian") == "academic"
    assert route_skill("buat cv dan surat lamaran magang") == "academic"
    assert route_skill("susun bahan ajar dan rubrik penilaian") == "academic"
    assert route_skill("bkd dan angka kredit untuk lektor") == "academic"


def test_subskill_selection_within_research():
    prepared = prepare_master_prompt(
        "saya peneliti mau bikin systematic review meta-analisis",
        user_role="user",
    )
    assert "Peneliti Profesional" in prepared["prompt"]


def test_subskill_penelitian_dosen_still_wins_satria():
    prepared = prepare_master_prompt(
        "buatkan proposal satria pembelajaran transformatif",
        user_role="user",
    )
    assert "Penelitian Dosen" in prepared["prompt"]
