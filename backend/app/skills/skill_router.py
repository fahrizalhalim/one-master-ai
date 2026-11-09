"""Router skill berbasis kata kunci — sederhana dan cepat untuk MVP.

Di iterasi berikutnya, ganti dengan klasifikasi berbasis model AI
(skor keyakinan + fallback 'general' + mekanisme klarifikasi).
"""

import re

SKILLS = {
    "academic": [
        "rps", "obe", "skripsi", "makalah", "jurnal", "perkuliahan",
        "penelitian", "kuliah", "mahasiswa", "kurikulum", "cpl", "cpmk",
        "dosen", "kampus", "universitas", "asesmen", "ujian", "tugas akhir",
        "tutor", "belajar", "flashcard", "latihan soal", "soal", "koreksi",
        "skripsi", "tugas akhir", "proposal skripsi", "sidang", "penguji",
        "makalah", "essay", "esai", "resume materi", "ringkasan jurnal",
        "outline presentasi", "tugas kuliah",
        "sitasi", "daftar pustaka", "apa style", "ieee", "referensi", "footnote",
        "praktikum", "laporan praktikum", "alat dan bahan", "dasar teori",
        "modul", "percobaan", "kesimpulan",
        "review", "reviewer", "novelty", "gap", "catatan perbaikan",
        "validitas", "pemeriksaan naskah", "periksa proposal", "periksa skripsi",
        "turnitin", "plagiarisme", "similarity", "deteksi ai", "cek ai",
        "pra-integritas", "duplikasi", "indeks kesamaan",
    ],
    "government": [
        "desa", "anggaran", "bupati", "dinas", "program", "kuota",
        "pemerintah", "kecamatan", "kelurahan", "perdes", "skpd", "rpjmd",
        "notulen", "rapat", "dekan", "kaprodi", "fakultas", "renstra",
        "jadwal", "surat keputusan", "sk pembimbing", "surat dinas",
        "surat tugas", "undangan", "pengumuman ujian", "draft sk",
        "surat", "pengumuman", "seminar", "ujian proposal", "ujian skripsi",
        "penempatan ruang", "pengawas",
        "swot", "kpi", "strategi", "perencanaan strategis", "eksekutif",
    ],
    "business": [
        "usaha", "bisnis", "pemasaran", "keuangan", "umkm", "modal",
        "pelanggan", "omzet", "laba", "marketing", "produk",
    ],
    "technology": [
        "aplikasi", "website", "database", "coding", "server", "ai",
        "program", "python", "javascript", "api", "docker", "linux",
    ],
    "research": [
        "data", "statistik", "analisis", "spss", " regresi", "kuesioner",
        "sampel", "laporan", "grafik", "hipotesis",
        "penelitian", "hibah", "drtpm", "bima", "proposal penelitian",
        "jurnal", "publikasi", "sinta", "scopus", "imrad", "artikel ilmiah",
    ],
}

GENERAL = "general"


def route_skill(message: str) -> str:
    text = message.lower()

    def _count(words) -> int:
        total = 0
        for word in words:
            w = word.strip()
            if not w:
                continue
            # Kata kunci multi-kata ("proposal penelitian") → substring;
            # kata tunggal → cocokkan utuh per kata (anti false-positive 'ai' di 'slovin')
            if " " in w:
                if w in text:
                    total += 1
            else:
                if re.search(rf"\b{re.escape(w)}\b", text):
                    total += 1
        return total

    scores = {skill: _count(words) for skill, words in SKILLS.items()}

    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return GENERAL

    return best
