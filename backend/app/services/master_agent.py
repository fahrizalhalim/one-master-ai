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
    timeout=240.0,
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


def _ask_groq(
    prompt: str,
    model_id: str,
    max_output_tokens: int = 4096,
) -> dict:
    """Panggil Groq dengan batas output dari reservasi token."""
    output_limit = max(1, min(int(max_output_tokens), 4096))

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
            max_completion_tokens=output_limit,
        )
    except Exception as exc:
        print(f"[master_agent] responses.create error: {type(exc).__name__}: {exc}", flush=True)
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


def _ask_legacy(
    prompt: str,
    provider_route: str = "manual",
    max_output_tokens: int = 4096,
    image_data_url: str | None = None,
) -> dict:
    """Panggil Ai.sumopod dengan batas output dari reservasi."""
    import os
    output_limit = max(16384, min(int(max_output_tokens), 16384))

    if image_data_url:
        vision_model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o").strip() or "gpt-4o"
        try:
            response = client.chat.completions.create(
                model=vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_data_url}},
                        ],
                    }
                ],
                max_tokens=min(output_limit, 4096),
            )
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            raise ProviderCallError(
                "Provider vision gagal menganalisis gambar.",
                safe_to_refund=_provider_error_is_definitive(exc),
                reason=f"vision_error_{status_code or 'unknown'}",
            ) from exc

        usage = _validated_usage(
            getattr(response, "usage", None),
            "prompt_tokens",
            "completion_tokens",
        )
        answer = _extract_answer(response, "chat_completions")
        if not answer:
            raise ProviderCallError(
                "Model vision tidak memberikan hasil analisis gambar.",
                safe_to_refund=False,
                reason="vision_empty_answer",
            )
        if usage is None:
            raise ProviderCallError(
                "Data usage analisis gambar tidak valid.",
                safe_to_refund=False,
                reason="vision_usage_invalid",
                answer=answer,
            )
        pt, ct = usage
        return {
            "text": answer,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "provider": "aisumopod",
            "model": vision_model,
            "provider_route": "vision",
        }

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
            max_output_tokens=output_limit,
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
                max_tokens=output_limit,
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

    if answer is None and api_type == "responses":
        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=output_limit,
            )
            api_type = "chat_completions"
            usage = _validated_usage(
                getattr(response, "usage", None),
                "prompt_tokens",
                "completion_tokens",
            )
            answer = _extract_answer(response, api_type)
        except Exception:
            pass

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


def ask_ai(
    prompt: str,
    model_id: str = "default",
    max_output_tokens: int = 4096,
    image_data_url: str | None = None,
) -> dict:
    """Gunakan provider dengan batas output sesuai reservasi."""
    output_limit = max(1, min(int(max_output_tokens), 4096))

    # OM.AI Pro menggunakan provider SumoPod.
    if model_id == "default" or image_data_url:
        return _ask_legacy(
            prompt,
            provider_route="manual",
            max_output_tokens=output_limit,
            image_data_url=image_data_url,
        )

    try:
        if output_limit == 4096:
            return _ask_groq(prompt, model_id)
        return _ask_groq(
            prompt, model_id, max_output_tokens=output_limit
        )

    except ProviderCallError as groq_error:
        # Timeout, koneksi putus, jawaban tanpa usage, dan hasil tidak pasti
        # tidak boleh dicoba ulang ke provider lain.
        if not groq_error.safe_to_refund:
            raise

        # Groq dipastikan menolak. Coba provider lama dengan reservasi yang sama.
        try:
            if output_limit == 4096:
                return _ask_legacy(prompt, provider_route="fallback")
            return _ask_legacy(
                prompt,
                provider_route="fallback",
                max_output_tokens=output_limit,
            )

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
    document_mode: bool = False,
) -> dict:
    """Siapkan prompt lengkap sebelum reservasi token."""
    skill = route_skill(user_message)

    # Skill khusus admin hanya dapat digunakan oleh role admin.
    if skill in ADMIN_ONLY_SKILLS and user_role != "admin":
        skill = GENERAL_SKILL_FALLBACK
        skill_instruction = ""
    else:
        skill_instruction = load_skill_instruction(skill, user_message)

    skill_block = ""
    if skill_instruction:
        skill_block = (
            f"Instruksi keahlian ({skill}):\n{skill_instruction}"
        )

    history_block = ""
    if history_text:
        history_block = (
            f"Riwayat percakapan (terbaru di akhir):\n"
            f"{history_text}\n"
        )

    file_block = ""
    if file_text:
        file_block = f"{file_text}\n"

    document_instruction = ""
    if document_mode:
        document_instruction = """- Anda sedang membuat dokumen untuk pengguna.
- Hasilkan balasan Telegram yang singkat dan isi dokumen yang lengkap.
- Jangan mengatakan bahwa Anda tidak dapat membuat atau mengirim file.
- Jangan menambahkan instruksi menyimpan atau mengubah dokumen menjadi PDF.
- Jangan mengarang sumber, kutipan, angka, data, atau daftar pustaka.
- Jika sumber tidak tersedia, nyatakan keterbatasannya secara jujur.
- Susun dokumen secara profesional dengan judul, subjudul, paragraf,
  dan penomoran yang konsisten sesuai kebutuhan.
- Sesuaikan struktur dengan jenis dokumen yang diminta.
- Jika membuat presentasi/slide (PPTX/PPT), susun per slide dengan format `## Slide 1: Judul Slide`, poin ringkas (maksimal 5 poin per slide), dan sertakan tepat 1 baris `Visual: [2-4 kata kunci gambar dalam bahasa Inggris yang relevan dengan isi slide]` pada setiap slide.
- Jangan memasukkan catatan internal, komentar proses, atau instruksi
  kepada pengguna yang tidak termasuk isi dokumen.
- Gunakan format penanda berikut secara persis dan jangan menambahkan
  teks di luar penanda:
<<<TELEGRAM_REPLY>>>
[balasan singkat untuk Telegram]
<<<DOCUMENT_CONTENT>>>
[isi dokumen lengkap dalam Markdown]
<<<END_DOCUMENT>>>"""

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
- Perhatikan variasi ejaan, salah ketik, singkatan, dan istilah yang maknanya sama jika konteks mendukungnya.
- Jika asisten sebelumnya mengajukan beberapa pertanyaan, tentukan pertanyaan mana yang dijawab oleh pesan terbaru. Jangan menganggap pertanyaan lainnya sudah terjawab.
- Gunakan hanya informasi yang benar-benar diberikan pengguna. Jangan mengarang jawaban untuk pertanyaan yang belum terjawab.
- Jika masih ada informasi penting yang belum diberikan, tanyakan bagian yang belum terjawab secara jelas dan seperlunya.
- Jika pesan terbaru jelas membahas topik yang berbeda, jawab topik baru tersebut tanpa membawa konteks lama yang tidak relevan.
- Jika pengguna kembali membahas topik lama, gunakan riwayat lama yang relevan apabila tersedia.
- Jika hubungan pesan terbaru dengan riwayat tidak cukup jelas, minta klarifikasi daripada memaksakan hubungan.

Aturan khusus pembuatan dokumen:
{document_instruction}

Permintaan pengguna:
{user_message}"""

    return {"skill": skill, "prompt": instruction}


def _parse_document_response(raw_text: str) -> tuple[str, str | None]:
    """Pisahkan balasan Telegram dan isi dokumen dari satu respons AI.

    Robust: model kadang (1) membungkus respons dengan code fence markdown,
    (2) melanggar/duplikasi penanda, atau (3) terpotong oleh batas token
    sebelum <<<END_DOCUMENT>>> (kasus nyata: bot menjawab "Dokumen belum
    berhasil dibuat"). Alih-alih gagal total, respons yang cukup panjang
    dipakai langsung sebagai isi dokumen.
    """
    text = str(raw_text or "")
    reply_marker = "<<<TELEGRAM_REPLY>>>"
    document_marker = "<<<DOCUMENT_CONTENT>>>"
    end_marker = "<<<END_DOCUMENT>>>"

    # Buang code fence markdown yang membungkus seluruh respons.
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
    if stripped.endswith("```"):
        stripped = re.sub(r"\s*```$", "", stripped)

    for candidate in (text, stripped):
        if not all(marker in candidate for marker in (
            reply_marker, document_marker, end_marker
        )):
            continue

        if not (
            candidate.count(reply_marker) == 1
            and candidate.count(document_marker) == 1
            and candidate.count(end_marker) == 1
        ):
            continue

        try:
            reply_start = candidate.index(reply_marker) + len(reply_marker)
            document_start = candidate.index(document_marker)
            content_start = document_start + len(document_marker)
            content_end = candidate.index(end_marker)

            if not (reply_start < document_start < content_start < content_end):
                raise ValueError("Urutan penanda respons tidak valid")

            reply = candidate[reply_start:document_start].strip()
            document = candidate[content_start:content_end].strip()

            if not reply or len(reply) > 1500 or not document:
                raise ValueError("Bagian respons kosong atau terlalu panjang")

            return reply, document
        except ValueError:
            continue

    # Jaring pengaman: jangan buang jawaban AI hanya karena format penanda
    # tidak dipatuhi atau respons terpotong. Cukup panjang = jadikan dokumen.
    body = (stripped or text).strip()
    if len(body) >= 100:
        return (
            "📄 Dokumen berhasil dibuat dari jawaban AI. Silakan diunduh.",
            body,
        )

    return (
        "Permintaan dokumen belum dapat diproses dengan benar. "
        "Silakan ulangi permintaan beberapa saat lagi.",
        None,
    )

def master_agent(
    user_message: str,
    profession: str = "umum",
    history_text: str = "",
    file_text: str = "",
    prepared: dict | None = None,
    model_id: str = "default",
    document_mode: bool = False,
    max_output_tokens: int = 4096,
    image_data_url: str | None = None,
) -> dict:
    """Jalankan Master Agent dengan batas output reservasi."""
    prepared = prepared or prepare_master_prompt(
        user_message=user_message,
        profession=profession,
        history_text=history_text,
        file_text=file_text,
        document_mode=document_mode,
    )

    # Pembuatan dokumen membutuhkan output lebih panjang (balasan + isi
    # dokumen sekaligus). Naikkan batas agar respons model tidak terpotong
    # sebelum penanda <<<END_DOCUMENT>>> sempat ditulis.
    if document_mode and max_output_tokens < 12000:
        max_output_tokens = 12000

    skill = prepared["skill"]
    result = ask_ai(
        prepared["prompt"],
        model_id=model_id,
        max_output_tokens=min(max_output_tokens, 4096),
        image_data_url=image_data_url,
    )

    answer = result["text"]
    document_content = None
    if document_mode:
        answer, document_content = _parse_document_response(answer)

    return {
        "skill": skill,
        "answer": answer,
        "document_content": document_content,
        "prompt_tokens": result["prompt_tokens"],
        "completion_tokens": result["completion_tokens"],
        "total_tokens": result["prompt_tokens"] + result["completion_tokens"],
        "provider": result.get("provider", "unknown"),
        "model": result.get("model", model_id),
        "provider_route": result.get("provider_route", "unknown"),
    }
