from pathlib import Path

from openai import OpenAI

from app.core.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from app.skills.skill_router import route_skill

SKILLS_DIR = Path(__file__).parent.parent / "skills"

client = OpenAI(
    api_key=OPENAI_API_KEY or "not-set",
    base_url=OPENAI_BASE_URL,
)


def load_skill_instruction(skill: str) -> str:
    """Muat instruksi skill dari file markdown pertama di folder skill-nya."""
    skill_dir = SKILLS_DIR / skill
    if not skill_dir.is_dir():
        return ""
    for md_file in sorted(skill_dir.glob("*.md")):
        return md_file.read_text(encoding="utf-8")
    return ""


def ask_ai(prompt: str) -> str:
    """Panggil model AI via endpoint OpenAI-compatible.

    Coba endpoint /responses dulu; jika provider tidak mendukung,
    fallback ke /chat/completions yang lebih universal.
    """
    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )
        return response.output_text
    except Exception:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content


def master_agent(
    user_message: str,
    profession: str = "umum",
    history_text: str = "",
    file_text: str = "",
) -> dict:
    skill = route_skill(user_message)
    skill_instruction = load_skill_instruction(skill)

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

    answer = ask_ai(instruction)

    return {"skill": skill, "answer": answer}
