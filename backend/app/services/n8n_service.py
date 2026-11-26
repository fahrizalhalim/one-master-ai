"""Komunikasi internal ONE MASTER AI dengan n8n."""

from typing import Any
import re

import httpx

from app.core.config import (
    N8N_API_KEY,
    N8N_BASE_URL,
    N8N_WEBHOOK_AUTH_VALUE,
)


class N8NServiceError(Exception):
    """Kesalahan komunikasi n8n tanpa membocorkan kredensial."""


def _configuration() -> tuple[str, dict[str, str]]:
    base_url = N8N_BASE_URL.rstrip("/")
    if not base_url or not N8N_API_KEY:
        raise N8NServiceError("Konfigurasi koneksi automation belum lengkap.")

    return base_url, {"X-N8N-API-KEY": N8N_API_KEY}


async def n8n_api_request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    """Memanggil REST API n8n. Detail respons sensitif tidak diteruskan."""
    base_url, headers = _configuration()
    clean_path = path.lstrip("/")

    if ".." in clean_path or "://" in clean_path:
        raise N8NServiceError("Path API automation tidak valid.")

    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=False,
        ) as client:
            response = await client.request(
                method,
                f"{base_url}/api/v1/{clean_path}",
                headers=headers,
                json=json,
                params=params,
            )
            response.raise_for_status()

            if not response.content:
                return {}

            return response.json()

    except httpx.HTTPStatusError as exc:
        raise N8NServiceError(
            f"n8n API menolak permintaan (HTTP {exc.response.status_code})."
        ) from None
    except httpx.TimeoutException:
        raise N8NServiceError("Koneksi ke n8n mengalami timeout.") from None
    except httpx.RequestError:
        raise N8NServiceError("Backend tidak dapat menghubungi n8n.") from None
    except ValueError:
        raise N8NServiceError("Respons n8n bukan JSON yang valid.") from None


async def execute_webhook(
    webhook_path: str,
    payload: dict[str, Any],
    timeout_seconds: int = 120,
) -> Any:
    """Menjalankan Production Webhook dengan autentikasi khusus OM.AI."""

    base_url = N8N_BASE_URL.rstrip("/")
    webhook_secret = N8N_WEBHOOK_AUTH_VALUE

    if not base_url or not webhook_secret:
        raise N8NServiceError(
            "Konfigurasi autentikasi webhook automation belum lengkap."
        )

    path = webhook_path.strip().strip("/")

    # Path wajib berupa 48 karakter heksadesimal hasil generator acak.
    if not re.fullmatch(r"[0-9a-f]{48}", path):
        raise N8NServiceError("Path webhook automation tidak valid.")

    timeout = max(5, min(int(timeout_seconds), 300))

    try:
        async with httpx.AsyncClient(
            timeout=float(timeout),
            follow_redirects=False,
        ) as client:
            response = await client.post(
                f"{base_url}/webhook/{path}",
                json=payload,
                headers={
                    "X-OMAI-Automation-Key": webhook_secret,
                },
            )
            response.raise_for_status()

            if not response.content:
                return {}

            try:
                result = response.json()
                if isinstance(result, (dict, list)):
                    return result
                return {"result": str(result)[:20000]}
            except ValueError:
                return {"text": response.text[:20000]}

    except httpx.HTTPStatusError as exc:
        raise N8NServiceError(
            f"Webhook automation merespons HTTP {exc.response.status_code}."
        ) from None
    except httpx.TimeoutException:
        raise N8NServiceError(
            "Eksekusi melewati batas waktu tunggu. Status hasil perlu diperiksa."
        ) from None
    except httpx.RequestError:
        raise N8NServiceError(
            "Backend tidak dapat menghubungi webhook automation."
        ) from None
