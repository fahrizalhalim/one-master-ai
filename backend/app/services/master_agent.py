from pathlib import Path
import re

from openai import OpenAI

from app.core.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from app.skills.skill_router import route_skill

SKILLS_DIR = Path(__file__).parent.parent / "skills"

client = OpenAI(
    api_key=OPENAI_API_KEY or "not-set",
    base_url=OPENAI_BASE_URL,
)


def load_skill_instruction(skill: str, user_message: str = "") -> str:
    """Muat instruksi skill dari folder skill-nya.

    Jika folder punya beberapa file .md (sub-skill), pilih file yang
    paling cocok dengan kata kunci pada pesan user. Tanpa kecocokan,
    gunakan file pertama sebagai default.
    """
    skill_dir = SKILLS_DIR / skill
    if not skill_dir.is_dir():
        return ""

    md_files = sorted(skill_dir.glob("*.md"))
    if not md_files:
        return ""

    if len(md_files) == 1 or not user_message:
        return md_files[0].read_text(encoding="utf-8")

    # Sub-skill: skor kecocokan kata kunci (nama file + JUDUL baris pertama file)
    # Catatan: hanya baris judul, bukan seluruh isi — kata umum di isi (tugas,
    # mahasiswa, hari) tidak boleh mengganggu pemilihan.
    text = user_message.lower()
    def score(f: Path) -> int:
        content = f.read_text(encoding="utf-8", errors="ignore")
        keywords = set(re.split(r"[_\-]", f.stem))
        first_lines = "\n".join(content.splitlines()[:4]).lower()
        keywords.update(re.findall(r"[a-z]{3,}", first_lines))
        return sum(1 for kw in keywords if len(kw) >= 3 and kw in text)

    best = max(md_files, key=score)
    return best.read_text(encoding="utf-8")


def ask_ai(prompt: str) -> dict:
    """Panggil model AI via endpoint OpenAI-compatible.

    Kembalikan dict {text, prompt_tokens, completion_tokens}.
    Coba endpoint /responses dulu; jika provider tidak mendukung,
    fallback ke /chat/completions yang lebih universal.
    """
    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )
        usage = getattr(response, "usage", None)
        return {
            "text": response.output_text,
            "prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "output_tokens", 0) or 0,
        }
    except Exception:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = getattr(response, "usage", None)
        return {
            "text": response.choices[0].message.content,
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        }


def master_agent(
    user_message: str,
    profession: str = "umum",
    history_text: str = "",
    file_text: str = "",
) -> dict:
    skill = route_skill(user_message)
    skill_instruction = load_skill_instruction(skill, user_message)

    skill_block = ""
    if skill_instruction:
        skill_block = f"Instruksi keahlian ({skill}):\n{skill_instruction}"

    history_block = ""
    if history_text:
        history_block = f"Riwayat percakapan (terbaru di akhir):\n{history_text}\n"

    file_block = ""
    if file_text:
        file_block = f"{file_text}\n"

    instruction = f"""Anda adalah ONE MASTER AI, asisten pekerjaan profesional.

Profesi pengguna: {profession}
Keahlian yang dipilih: {skill}

{skill_block}

{history_block}
{file_block}
Aturan umum:
- Jawab secara profesional, sistematis, akurat dan sesuai konteks pekerjaan.
- Jika data tidak tersedia, jelaskan data apa yang masih dibutuhkan.
- Gunakan bahasa Indonesia.

Permintaan pengguna:
{user_message}"""

    result = ask_ai(instruction)

    return {
        "skill": skill,
        "answer": result["text"],
        "prompt_tokens": result["prompt_tokens"],
        "completion_tokens": result["completion_tokens"],
        "total_tokens": result["prompt_tokens"] + result["completion_tokens"],
    }
