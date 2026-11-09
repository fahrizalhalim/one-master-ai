"""OpenAI-compatible proxy dengan pemotongan saldo wallet ONE MASTER AI."""

import json
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.user import User
from app.models.token_usage import TokenUsage
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.services.token_service import (
    reserve_tokens,
    cancel_reservation,
    complete_reservation,
)

router = APIRouter()

HERMES_URL = "http://172.18.0.1:8642/v1/chat/completions"

# Kunci mesin Hermes dibaca dari environment, bukan ditanam di source code.
import os
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "")


def mark_reservation_needs_review(
    db: Session,
    reservation_id: str,
    reason: str,
) -> None:
    """Tahan reservasi ketika hasil pemrosesan Hermes belum pasti."""

    try:
        reservation = (
            db.query(TokenReservation)
            .filter(TokenReservation.id == reservation_id)
            .with_for_update()
            .first()
        )

        if reservation is not None and reservation.status == "pending":
            reservation.status = "needs_review"
            reservation.description = (
                (reservation.description or "")
                + f" | Proxy review: {reason}"
            )[:2000]

        db.commit()

    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail=(
                "Status pemrosesan Hermes belum dapat dipastikan. "
                "Reservasi tidak boleh direfund otomatis."
            ),
        ) from exc


def get_authorized_user(db: Session, authorization: str) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Bearer token")

    api_key = authorization.split(" ", 1)[1].strip()
    user = (
        db.query(User)
        .filter(User.api_key == api_key, User.is_active.is_(True))
        .first()
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    if not user.is_active or user.status != "active":
        raise HTTPException(
            status_code=403,
            detail="Akun tidak aktif atau belum disetujui admin.",
        )

    return user


@router.get("/models")
async def get_models(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    get_authorized_user(db, authorization)

    return {
        "object": "list",
        "data": [
            {
                "id": "hermes",
                "object": "model",
                "owned_by": "sumapod",
            }
        ],
    }


@router.post("/chat/completions")
async def chat_proxy(
    request: Request,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    user = get_authorized_user(db, authorization)

    if not HERMES_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Kunci API Hermes belum dikonfigurasi di backend.",
        )

    body = await request.json()

    # Non-stream agar token aktual dapat dicatat setelah respons provider.
    body["stream"] = False
    body["model"] = "hermes"

    # Estimasi prompt dan output untuk reservasi saldo sebelum request.
    messages_text = json.dumps(
        body.get("messages", []),
        ensure_ascii=False,
        default=str,
    )
    estimated_prompt = max(1, (len(messages_text) + 2) // 3)

    try:
        max_output = int(
            body.get("max_tokens")
            or body.get("max_completion_tokens")
            or 4096
        )
    except (TypeError, ValueError):
        max_output = 4096

    if max_output <= 0:
        raise HTTPException(status_code=400, detail="Batas output token tidak valid.")

    estimated_tokens = estimated_prompt + max_output
    reference = f"proxy:{user.id}:{uuid.uuid4()}"

    reservation = reserve_tokens(
        db=db,
        user=user,
        estimated_tokens=estimated_tokens,
        reference=reference,
        description="Chat OpenAI-compatible / Open WebUI",
    )

    hermes_headers = {
        "Authorization": f"Bearer {HERMES_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                HERMES_URL,
                json=body,
                headers=hermes_headers,
                timeout=120.0,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError(
                    'Hermes returned a non-object JSON response'
                )

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code

        # Status ini merupakan penolakan definitif sebelum pemrosesan AI.
        safe_rejection_statuses = {400, 401, 403, 404, 422}

        if reservation is not None:
            if status_code in safe_rejection_statuses:
                cancel_reservation(db, reservation.id)
            else:
                mark_reservation_needs_review(
                    db,
                    reservation.id,
                    f"http_status_{status_code}",
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Hermes mengembalikan HTTP "
                        f"{status_code}. Hasil pemrosesan belum dapat "
                        "dipastikan. Reservasi ditahan untuk pemeriksaan "
                        "Admin dan tidak direfund otomatis."
                    ),
                ) from exc

        raise HTTPException(
            status_code=status_code,
            detail=f"Hermes Engine Error: {exc.response.text[:1000]}",
        ) from exc

    # Kegagalan sebelum koneksi/request berhasil dikirim.
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
    ) as exc:
        if reservation is not None:
            cancel_reservation(db, reservation.id)

        raise HTTPException(
            status_code=502,
            detail=(
                "Koneksi ke Hermes gagal sebelum request diproses. "
                "Reservasi token telah dikembalikan."
            ),
        ) from exc

    # Request mungkin sudah diterima atau diproses oleh Hermes.
    except Exception as exc:
        if reservation is not None:
            mark_reservation_needs_review(
                db,
                reservation.id,
                f"ambiguous_{type(exc).__name__}",
            )

        raise HTTPException(
            status_code=503 if reservation is not None else 502,
            detail=(
                "Hasil pemrosesan Hermes belum dapat dipastikan. "
                "Reservasi ditahan untuk pemeriksaan Admin."
                if reservation is not None
                else "Layanan Hermes sedang tidak tersedia."
            ),
        ) from exc

    usage = result.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    try:
        actual_tokens = int(usage.get("total_tokens") or 0)
        prompt_tokens = max(0, int(usage.get("prompt_tokens") or 0))
        completion_tokens = max(
            0,
            int(usage.get("completion_tokens") or 0),
        )
    except (TypeError, ValueError):
        actual_tokens = prompt_tokens = completion_tokens = 0

    # Jika provider tidak memberikan total yang valid, gunakan estimasi
    # reservasi. Jika komponen tidak cocok, normalisasi agar jumlahnya tepat.
    if actual_tokens <= 0:
        actual_tokens = estimated_tokens
        prompt_tokens = min(estimated_prompt, actual_tokens)
        completion_tokens = max(0, actual_tokens - prompt_tokens)
    else:
        prompt_tokens = min(prompt_tokens, actual_tokens)
        completion_tokens = actual_tokens - prompt_tokens

    if reservation is not None:
        # Bukti hasil harus tersimpan sebelum settlement.
        stored_result = TokenReservationResult(
            reservation_id=reservation.id,
            user_id=user.id,
            assistant_message_id=None,
            response_content=json.dumps(
                result,
                ensure_ascii=False,
                default=str,
            ),
            skill="open_webui",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=actual_tokens,
            status="received",
        )

        try:
            db.add(stored_result)
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Respons Hermes sudah diterima, tetapi bukti hasil "
                    "belum berhasil disimpan. Reservasi tidak boleh "
                    "direfund otomatis."
                ),
            ) from exc

        # Jika settlement gagal, bukti tetap berstatus received
        # agar dapat dipulihkan oleh Admin.
        try:
            complete_reservation(
                db=db,
                reservation_id=reservation.id,
                actual_tokens=actual_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                skill="open_webui",
            )
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Respons Hermes sudah tersimpan, tetapi settlement "
                    "token belum berhasil. Bukti menunggu pemulihan Admin."
                ),
            ) from exc

        # Tandai bukti settled setelah settlement berhasil.
        try:
            stored_result = db.get(
                TokenReservationResult,
                reservation.id,
            )
            if stored_result is None:
                raise RuntimeError(
                    "Bukti hasil proxy tidak ditemukan setelah settlement."
                )

            stored_result.status = "settled"
            stored_result.settled_at = (
                datetime.now(timezone.utc).replace(tzinfo=None)
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Settlement token telah diproses, tetapi status bukti "
                    "belum berhasil diperbarui. Admin dapat memverifikasi "
                    "reservasi tanpa melakukan pemotongan ulang."
                ),
            ) from exc

    else:
        # Administrator tidak menggunakan reservasi, tetapi statistik
        # pemakaian tetap dicatat.
        db.add(
            TokenUsage(
                user_id=user.id,
                skill="open_webui",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=actual_tokens,
            )
        )
        db.commit()

    return result
