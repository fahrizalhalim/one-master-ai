"""Router skill berbasis kata kunci — sederhana dan cepat untuk MVP.

Di iterasi berikutnya, ganti dengan klasifikasi berbasis model AI
(skor keyakinan + fallback 'general' + mekanisme klarifikasi).
"""

SKILLS = {
    "academic": [
        "rps", "obe", "skripsi", "makalah", "jurnal", "perkuliahan",
        "penelitian", "kuliah", "mahasiswa", "kurikulum", "cpl", "cpmk",
        "dosen", "kampus", "universitas", "asesmen", "ujian", "tugas akhir",
        "tutor", "belajar", "flashcard", "latihan soal", "soal", "koreksi",
        "sitasi", "daftar pustaka", "apa style", "ieee", "referensi", "footnote",
        "praktikum", "laporan praktikum", "alat dan bahan", "dasar teori",
        "modul", "percobaan", "kesimpulan",
    ],
    "government": [
        "desa", "anggaran", "bupati", "dinas", "program", "kuota",
        "pemerintah", "kecamatan", "kelurahan", "perdes", "skpd", "rpjmd",
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
    ],
}

GENERAL = "general"


def route_skill(message: str) -> str:
    text = message.lower()

    scores = {
        skill: sum(word.strip() in text for word in words)
        for skill, words in SKILLS.items()
    }

    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return GENERAL

    return best
