"""Endpoint generator dokumen: buat DOCX/PDF dari jawaban Master AI."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.models.user import User
from app.services.document_service import (
    create_docx,
    create_pdf,
    resolve_stored_file,
)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class GenerateRequest(BaseModel):
    title: str = Field(default="Dokumen ONE MASTER AI", max_length=120)
    content: str = Field(..., min_length=1, max_length=50000)
    format: str = Field(default="docx", pattern="^(docx|pdf)$")


@router.post("/generate")
def generate_document(
    data: GenerateRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        if data.format == "pdf":
            path = create_pdf(data.title, data.content)
        else:
            path = create_docx(data.title, data.content)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Gagal membuat dokumen. Coba lagi nanti.",
        )

    media_types = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
    }
    return {
        "filename": path.name,
        "download_url": f"/api/v1/documents/download/{path.name}",
        "format": data.format,
        "media_type": media_types[data.format],
    }


@router.get("/download/{filename}")
def download_document(
    filename: str,
    current_user: User = Depends(get_current_user),
):
    path = resolve_stored_file(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

    media_type = (
        "application/pdf"
        if path.suffix == ".pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(
        str(path),
        media_type=media_type,
        filename=path.name,
    )
