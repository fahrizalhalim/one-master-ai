from pathlib import Path
import re

from openai import OpenAI

from app.core.config import (
    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
    GROQ_API_KEY, GROQ_BASE_URL,
)
from app.skills.skill_router import route_skill

SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Skill dalam folder ini hanya boleh dipakai role admin.
ADMIN_ONLY_SKILLS = {"admin"}
GENERAL_SKILL_FALLBACK = "general"

client = OpenAI(
    api_key=OPENAI_API_KEY or "not-set",
    base_url=OPENAI_BASE_URL,
    timeout=150.0,
    max_retries=0,
)

# Client Groq terpisah. Tidak menggantikan provider utama.
groq_client = OpenAI(
    api_key=GROQ_API_KEY or "not-set",
    base_url=GROQ_BASE_URL,
    timeout=150.0,
    max_retries=0,
)

GROQ_MODELS = {
    "groq-gpt-oss-120b": "openai/gpt-oss-120b",
}


def _ask_groq(prompt: str, model_id: str) -> dict:
    """Panggil model Groq dengan validasi jawaban dan usage token."""

    model = GROQ_MODELS.get(model_id)
    if model is None:
        raise ProviderCallError(
            "Pilihan model tidak dikenal.",
            safe_to_refund=True,
            reason="unknown_model",
        )

    if not GROQ_API_KEY:
        raise ProviderCallError(
            "API key Groq belum dikonfigurasi.",
            safe_to_refund=True,
            reason="groq_api_key_not_configured",
        )

    try:
        response = groq_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=4096,
        )
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        raise ProviderCallError(
            "Provider Groq gagal memproses permintaan.",
            safe_to_refund=_provider_error_is_definitive(exc),
            reason=f"groq_chat_completions_error_{status_code or 'unknown'}",
        ) from exc

    usage = _validated_usage(
        getattr(response, "usage", None),
        "prompt_tokens",
        "completion_tokens",
    )
    answer = _extract_answer(response, "chat_completions")

    if answer is None:
        raise ProviderCallError(
            "Groq memberikan respons tanpa jawaban yang dapat dibaca.",
            safe_to_refund=False,
            reason="groq_response_without_readable_answer",
        )

    if usage is None:
        raise ProviderCallError(
            "Jawaban Groq diterima, tetapi data usage tidak valid.",
            safe_to_refund=False,
            reason="groq_usage_missing_or_invalid",
            answer=answer,
        )

    prompt_tokens, completion_tokens = usage

    return {
        "text": answer,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "provider": "groq",
        "model": model,
        "provider_route": "primary",
    }


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
    # Stopword umum dibuang agar kata sambung ("dan", "yang") tidak menaikkan skor.
    STOPWORDS = {
        "yang", "dan", "untuk", "dengan", "pada", "ini", "itu", "dari", "akan",
        "adalah", "bisa", "agar", "atau", "ke", "di", "the", "and", "for",
    }
    text = user_message.lower()
    def score(f: Path) -> int:
        content = f.read_text(encoding="utf-8", errors="ignore")
        keywords = set(re.split(r"[_\-]", f.stem))
        first_lines = "\n".join(content.splitlines()[:4]).lower()
        keywords.update(re.findall(r"[a-z]{3,}", first_lines))
        keywords -= STOPWORDS
        return sum(1 for kw in keywords if len(kw) >= 3 and kw in text)

    best = max(md_files, key=score)
    return best.read_text(encoding="utf-8")


class ProviderCallError(RuntimeError):
    """Kegagalan provider dengan klasifikasi keamanan refund."""

    def __init__(
        self,
        message: str,
        *,
        safe_to_refund: bool,
        reason: str,
        answer: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ):
        super().__init__(message)
        self.safe_to_refund = safe_to_refund
        self.reason = reason
        self.answer = answer
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


def _validated_usage(
    usage,
    input_name: str,
    output_name: str,
) -> tuple[int, int] | None:
    """Validasi usage. None berarti provider tidak memberi usage valid."""
    if usage is None:
        return None

    try:
        prompt_tokens = getattr(usage, input_name, None)
        completion_tokens = getattr(usage, output_name, None)

        if prompt_tokens is None or completion_tokens is None:
            return None

        if isinstance(prompt_tokens, bool) or isinstance(completion_tokens, bool):
            return None

        prompt_tokens = int(prompt_tokens)
        completion_tokens = int(completion_tokens)

        if prompt_tokens < 0 or completion_tokens < 0:
            return None

        if prompt_tokens + completion_tokens <= 0:
            return None

        return prompt_tokens, completion_tokens
    except (TypeError, ValueError, OverflowError):
        return None


def _provider_error_is_definitive(exc: Exception) -> bool:
    """Hanya penolakan HTTP tertentu yang dianggap aman untuk refund."""
    from openai import APIStatusError

    if not isinstance(exc, APIStatusError):
        return False

    return getattr(exc, "status_code", None) in {
        400, 401, 403, 413, 415, 422,
    }


def _extract_answer(response, api_type: str) -> str | None:
    """Ambil jawaban tanpa menggagalkan penyimpanan jika format tidak lengkap."""
    try:
        if api_type == "responses":
            answer = response.output_text
        else:
            answer = response.choices[0].message.content

        if isinstance(answer, str) and answer.strip():
            return answer
    except (AttributeError, IndexError, KeyError, TypeError):
        pass

    return None


def _ask_legacy(prompt: str, provider_route: str = "manual") -> dict:
    """Panggil provider lama Ai.sumopod dengan Responses lalu Chat Completions."""

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
            max_output_tokens=4096,
        )
        api_type = "responses"

    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        error_text = str(exc).lower()

        unsupported_endpoint = (
            status_code in (404, 405, 501)
            or "not supported" in error_text
            or "unknown endpoint" in error_text
            or "unsupported endpoint" in error_text
        )

        if not unsupported_endpoint:
            raise ProviderCallError(
                "Provider Ai.sumopod Responses API gagal.",
                safe_to_refund=_provider_error_is_definitive(exc),
                reason=f"legacy_responses_error_{status_code or 'unknown'}",
            ) from exc

        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
            api_type = "chat_completions"

        except Exception as fallback_exc:
            fallback_status = getattr(fallback_exc, "status_code", None)
            raise ProviderCallError(
                "Provider Ai.sumopod Chat Completions API gagal.",
                safe_to_refund=_provider_error_is_definitive(fallback_exc),
                reason=f"legacy_chat_error_{fallback_status or 'unknown'}",
            ) from fallback_exc

    if api_type == "responses":
        usage = _validated_usage(
            getattr(response, "usage", None),
            "input_tokens",
            "output_tokens",
        )
    else:
        usage = _validated_usage(
            getattr(response, "usage", None),
            "prompt_tokens",
            "completion_tokens",
        )

    answer = _extract_answer(response, api_type)

    if answer is None:
        raise ProviderCallError(
            "Ai.sumopod memberikan respons tanpa jawaban yang dapat dibaca.",
            safe_to_refund=False,
            reason=f"legacy_{api_type}_response_without_answer",
        )

    if usage is None:
        raise ProviderCallError(
            "Jawaban Ai.sumopod diterima, tetapi data usage tidak valid.",
            safe_to_refund=False,
            reason=f"legacy_{api_type}_usage_missing_or_invalid",
            answer=answer,
        )

    prompt_tokens, completion_tokens = usage

    return {
        "text": answer,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "provider": "aisumopod",
        "model": OPENAI_MODEL,
        "provider_route": provider_route,
    }


def ask_ai(prompt: str, model_id: str = "default") -> dict:
    """Gunakan SumoPod sebagai default; Groq dipilih secara eksplisit."""

    # OM.AI Pro menggunakan provider SumoPod.
    if model_id == "default":
        return _ask_legacy(prompt, provider_route="manual")

    try:
        return _ask_groq(prompt, model_id)

    except ProviderCallError as groq_error:
        # Timeout, koneksi putus, jawaban tanpa usage, dan hasil tidak pasti
        # tidak boleh dicoba ulang ke provider lain.
        if not groq_error.safe_to_refund:
            raise

        # Groq dipastikan menolak. Coba provider lama dengan reservasi yang sama.
        try:
            return _ask_legacy(prompt, provider_route="fallback")

        except ProviderCallError as legacy_error:
            # Refund hanya aman jika kedua provider dipastikan menolak.
            raise ProviderCallError(
                "Groq menolak permintaan dan Ai.sumopod juga gagal. "
                "Tidak ada jawaban yang berhasil dihasilkan.",
                safe_to_refund=legacy_error.safe_to_refund,
                reason=(
                    f"groq_{groq_error.reason};"
                    f"legacy_{legacy_error.reason}"
                ),
                answer=legacy_error.answer,
                prompt_tokens=legacy_error.prompt_tokens,
                completion_tokens=legacy_error.completion_tokens,
            ) from legacy_error


def prepare_master_prompt(
    user_message: str,
    profession: str = "umum",
    full_name: str = "",
    history_text: str = "",
    file_text: str = "",
    user_role: str = "user",
) -> dict:
    """Siapkan prompt lengkap sebelum reservasi token."""
    skill = route_skill(user_message)

    # Skill khusus admin (folder "admin") hanya untuk role admin.
    if skill in ADMIN_ONLY_SKILLS and user_role != "admin":
        skill = GENERAL_SKILL_FALLBACK
        skill_instruction = ""
    else:
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

Identitas pengguna yang sedang login: {full_name or "Pengguna"}
Profesi pengguna: {profession}
Keahlian yang dipilih: {skill}

Aturan identitas dan sapaan:
- Jika menyapa pengguna, gunakan nama dari identitas pengguna yang sedang login.
- Jangan pernah menggunakan nama admin, nama pengguna lain, atau nama dari percakapan lain.
- Jangan menganggap nama admin sebagai identitas pengguna.
- Jika nama tidak tersedia, gunakan sapaan netral tanpa mengarang nama.

{skill_block}

{history_block}
{file_block}
Aturan umum:
- Jawab secara profesional, sistematis, akurat dan sesuai konteks pekerjaan.
- Jika data tidak tersedia, jelaskan data apa yang masih dibutuhkan.
- Gunakan bahasa Indonesia.

Aturan memahami percakapan dan konteks:
- Baca riwayat percakapan untuk memahami maksud pesan terbaru, bukan sekadar mengulang atau merangkum riwayat.
- Pesan terbaru pengguna adalah permintaan utama. Riwayat hanya menjadi konteks pendukung jika memang relevan.
- Kenali jawaban singkat seperti nama benda, pilihan, angka, ukuran, atau satuan sebagai kemungkinan jawaban atas pertanyaan asisten sebelumnya.
- Cocokkan jawaban singkat dengan pertanyaan sebelumnya yang paling relevan, bukan otomatis menganggapnya sebagai topik baru.
- Perhatikan variasi ejaan, salah ketik, singkatan, dan istilah yang maknanya sama jika konteks mendukungnya. Contohnya, "polibag" dan "polybag" merujuk pada benda yang sama.
- Jika asisten sebelumnya mengajukan beberapa pertanyaan, tentukan pertanyaan mana yang dijawab oleh pesan terbaru. Jangan menganggap pertanyaan lainnya sudah terjawab.
- Gunakan hanya informasi yang benar-benar diberikan pengguna. Jangan mengarang jawaban untuk pertanyaan yang belum terjawab.
- Jika masih ada informasi penting yang belum diberikan, tanyakan bagian yang belum terjawab secara jelas dan seperlunya.
- Jika pesan terbaru jelas membahas topik yang berbeda, jawab topik baru tersebut tanpa membawa konteks lama yang tidak relevan.
- Jika pengguna kembali membahas topik lama, gunakan riwayat lama yang relevan apabila tersedia.
- Jika hubungan pesan terbaru dengan riwayat tidak cukup jelas, minta klarifikasi daripada memaksakan hubungan.

Permintaan pengguna:
{user_message}"""

    return {"skill": skill, "prompt": instruction}


def master_agent(
    user_message: str,
    profession: str = "umum",
    history_text: str = "",
    file_text: str = "",
    prepared: dict | None = None,
    model_id: str = "default",
) -> dict:
    """Jalankan Master Agent menggunakan prompt dan model terpilih."""
    prepared = prepared or prepare_master_prompt(
        user_message=user_message,
        profession=profession,
        history_text=history_text,
        file_text=file_text,
    )

    skill = prepared["skill"]
    result = ask_ai(prepared["prompt"], model_id=model_id)

    return {
        "skill": skill,
        "answer": result["text"],
        "prompt_tokens": result["prompt_tokens"],
        "completion_tokens": result["completion_tokens"],
        "total_tokens": result["prompt_tokens"] + result["completion_tokens"],
        "provider": result.get("provider", "unknown"),
        "model": result.get("model", model_id),
        "provider_route": result.get("provider_route", "unknown"),
    }
