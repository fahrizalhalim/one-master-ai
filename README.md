# ONE MASTER AI

Asisten AI multi-skill untuk pekerjaan akademik, pemerintahan, bisnis, teknologi, dan riset.

## Struktur

```
one-master-ai/
├── backend/          # FastAPI — API & Master Agent
│   └── app/
│       ├── api/      # Route endpoint
│       ├── core/     # Konfigurasi
│       ├── models/   # Model database (SQLAlchemy)
│       ├── services/ # Logika Master Agent & AI
│       └── skills/   # Skill Library (per bidang)
├── db/               # Skema PostgreSQL
├── frontend/         # Next.js (tahap berikutnya)
├── nginx/            # Reverse proxy (produksi)
├── compose.yaml      # Docker Compose
└── .env.example      # Contoh konfigurasi
```

## Menjalankan Lokal (tanpa Docker)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env   # lalu isi OPENAI_API_KEY
uvicorn app.main:app --reload --port 8000
```

Buka http://localhost:8000/docs untuk dokumentasi API.

## Menjalankan dengan Docker Compose

```bash
cp .env.example .env    # isi OPENAI_API_KEY & DB_PASSWORD
docker compose up -d --build
```

Backend berjalan di http://127.0.0.1:8000 (hanya lokal; publik via reverse proxy + HTTPS).

## Uji

```bash
cd backend
pytest tests/ -v
```

## API

| Endpoint | Fungsi |
|---|---|
| `GET /` | Info aplikasi |
| `GET /health` | Cek kesehatan |
| `POST /api/v1/chat` | Chat ke Master Agent — body: `{"message", "profession", "user_id"}` |

## Roadmap MVP (8 minggu)

1. [x] Fondasi proyek + backend API
2. [ ] Autentikasi user (JWT)
3. [ ] Chat tersimpan di PostgreSQL
4. [x] Agent Router + Skill Library (5 skill)
5. [ ] Memory & riwayat percakapan
6. [ ] Generator DOCX/PDF
7. [ ] Dashboard Next.js + task queue
8. [ ] Uji pilot 5–10 pengguna
