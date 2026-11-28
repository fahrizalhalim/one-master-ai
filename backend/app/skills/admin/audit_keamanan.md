# Skill: Audit Keamanan — Interpretasi Hasil Scan & Laporan OWASP (KHUSUS ADMIN)
Kata kunci: hasil scan, output zap, zap baseline, nikto, dirsearch, whatweb, interpretasi temuan, tabel temuan owasp, tingkat keparahan, laporan audit, soft-404, bukti temuan, klasifikasi kerentanan.

## ⚠️ Status & Etika
Skill ini HANYA untuk admin om AI (folder admin = role-gated) dan SAUDARA dari `pentest_keamanan.md`.
Peran khusus skill ini: **menginterpretasi output alat yang dijalankan admin** (ZAP, nikto, dirsearch, whatweb, sslscan, curl) menjadi temuan terklasifikasi OWASP + laporan profesional. Aturan mutlak sama seperti pentest_keamanan: hanya sistem milik sendiri atau pihak lain dengan IZIN TERTULIS; om AI tidak menjalankan serangan; semua temuan untuk perbaikan.

## Cara Pakai
Admin menempelkan output mentah alat + konteks (URL target, izin ada). om AI mengeluarkan:

### 1. Tabel Temuan OWASP
Format wajib per temuan:
| No | Temuan | Bukti (kutipan output) | Kategori OWASP | Tingkat |
Tingkat: KRITIS > TINGGI > SEDANG > RENDAH > INFO. Kriteria penilaian: dampak (data? akses? defacement?) x kemudahan eksploitasi.

### 2. Pemisahan Sinyal vs Derau
- **Soft-404**: banyak alat menandai jalur "200 OK" yang sebenarnya respons kosong catch-all — verifikasi dulu dengan jalur acak sebelum jadikan temuan (contoh nyata: bigoselatan.id semua jalur 200 kosong).
- False positive umum: header X-XSS-Protection usang, cookie tanpa SameSite pada endpoint publik, CSP dengan unsafe-inline (catat SEDANG, jangan KRITIS).
- Output alat versi tua sering salah flag — cek manual perintah curl yang mereproduksi bukti.

### 3. Tingkat Keparahan + Justifikasi Singkat
Tiap temuan diberi 1-2 kalimat dampak nyata pada pemilik sistem (bukan teori generik).

### 4. Rekomendasi Perbaikan Terprioritas
Urut: (1) perbaikan cepat <1 hari (header, kuki flags), (2) sedang (konfigurasi, CSP refactor), (3) besar (migrasi OS/EOL, refactor auth). Tiap rekomendasi konkret & bisa dieksekusi pengelola.

### 5. Catatan Etika Laporan
Laporan menyatakan metode pasif/semi-aktif/aktif sesuai izin; temuan TIDAK untuk disebar publik; diserahkan ke pemilik sistem.

## Template Prompt
- "Interpretasi hasil scan ini, beri tabel temuan OWASP + tingkat keparahan + rekomendasi prioritas: [tempel output]"
- "Output ini banyak 200 OK — bantu identifikasi soft-404 vs temuan nyata: [tempel]"
- "Susun laporan audit resmi (ringkasan eksekutif, metodologi, temuan, rekomendasi) dari tabel temuan berikut untuk diserahkan ke pengelola: [daftar]"

## Batasan
- Tidak menganalisis output dari serangan ke sistem tanpa bukti izin tertulis.
- Tidak menghasilkan payload eksploitasi aktif baru; hanya mengklasifikasi temuan dari alat standar yang admin jalankan sendiri.
- Output AI = draf; verifikasi manusia wajib sebelum laporan ditandatangani & diserahkan.
