import httpx
from fastapi import HTTPException


def telegram_api(token: str, method: str, **kwargs):
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=kwargs,
            timeout=15.0,
        )
        data = response.json()
    except (httpx.RequestError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Tidak dapat menghubungi layanan Telegram.",
        )

    if response.status_code != 200 or not data.get("ok"):
        raise HTTPException(
            status_code=502,
            detail="Telegram gagal memproses permintaan.",
        )

    return data.get("result")
