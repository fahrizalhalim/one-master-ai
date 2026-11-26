"""Notifikasi pengajuan Top Up baru kepada administrator melalui Telegram."""

import html
import logging
import os
from pathlib import Path

import httpx

from app.services.document_service import STORAGE_DIR
from app.services.token_service import wit_now

logger = logging.getLogger(__name__)

PROOF_DIR = STORAGE_DIR / "topup_proofs"


async def notify_admin_new_topup(user, plan, item) -> bool:
    """Kirim detail transaksi dan bukti pembayaran ke Telegram Admin."""

    bot_token = os.getenv("TOPUP_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TOPUP_TELEGRAM_ADMIN_CHAT_ID", "").strip()

    if not bot_token or not chat_id:
        logger.warning("Notifikasi Top Up Telegram belum dikonfigurasi.")
        return False

    amount = f"Rp {int(item.amount_idr):,}".replace(",", ".")
    tokens = f"{int(item.token_amount):,}".replace(",", ".")
    timestamp = wit_now().strftime("%d-%m-%Y %H:%M:%S WIT")

    message = (
        "PENGAJUAN TOP UP BARU\n\n"
        f"Nama: {user.full_name or '-'}\n"
        f"Email: {user.email}\n"
        f"Paket: {plan.name}\n"
        f"Nominal: {amount}\n"
        f"Token: {tokens}\n"
        f"Waktu: {timestamp}\n"
        f"ID Transaksi: {item.id}\n"
        "Status: MENUNGGU VERIFIKASI"
    )

    proof_path = (PROOF_DIR / str(user.id) / item.proof_file).resolve()
    expected_dir = (PROOF_DIR / str(user.id)).resolve()

    if proof_path.parent != expected_dir or not proof_path.is_file():
        method = "sendMessage"
        data = {"chat_id": chat_id, "text": message}
        files = None
    else:
        suffix = proof_path.suffix.lower()
        method = "sendDocument" if suffix == ".pdf" else "sendPhoto"
        field = "document" if suffix == ".pdf" else "photo"
        mime = "application/pdf" if suffix == ".pdf" else (
            "image/png" if suffix == ".png" else "image/jpeg"
        )
        data = {"chat_id": chat_id, "caption": message}

        try:
            with proof_path.open("rb") as proof:
                files = {
                    field: (proof_path.name, proof, mime)
                }
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"https://api.telegram.org/bot{bot_token}/{method}",
                        data=data,
                        files=files,
                    )
                    response.raise_for_status()
                    result = response.json()
                    if result.get("ok"):
                        return True
                    logger.warning("Telegram menolak notifikasi Top Up.")
                    return False
        except Exception:
            logger.warning("Pengiriman bukti Top Up ke Telegram gagal.")
            return False

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{bot_token}/{method}",
                data=data,
            )
            response.raise_for_status()
            return bool(response.json().get("ok"))
    except Exception:
        logger.warning("Pengiriman notifikasi Top Up ke Telegram gagal.")
        return False
