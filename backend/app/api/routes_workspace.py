"""API Folder Workspace personal."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.models.user import User
from app.services.workspace_service import (
    WorkspaceError,
    list_files,
    search_files,
    read_file,
)
from app.services.workspace_folders import (
    create_folder,
    list_folders,
    rename_folder,
    delete_empty_folder,
)

router = APIRouter(
    prefix="/api/v1/files/workspace",
    tags=["workspace"],
)


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_path: str = ""


class FolderRenameRequest(BaseModel):
    folder_path: str = Field(min_length=1)
    new_name: str = Field(min_length=1, max_length=80)


@router.get("/folders")
def get_workspace_folders(
    parent_path: str = "",
    current_user: User = Depends(get_current_user),
):
    try:
        return {
            "folders": list_folders(current_user.id, parent_path)
        }
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/folders")
def post_workspace_folder(
    payload: FolderCreateRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        return create_folder(
            current_user.id,
            payload.name,
            payload.parent_path,
        )
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/folders/rename")
def patch_workspace_folder(
    payload: FolderRenameRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        return rename_folder(
            current_user.id,
            payload.folder_path,
            payload.new_name,
        )
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/folders")
def remove_workspace_folder(
    folder_path: str,
    current_user: User = Depends(get_current_user),
):
    try:
        return delete_empty_folder(
            current_user.id,
            folder_path,
        )
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.get("/files")
def get_workspace_files(
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Depends(get_current_user),
):
    """Daftar file personal di seluruh folder Workspace."""
    try:
        return {
            "files": list_files(current_user.id, limit=limit)
        }
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/files/search")
def search_workspace_files(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """Cari file berdasarkan nama atau isi teks ekstraksi."""
    try:
        return {
            "files": search_files(
                current_user.id,
                query=q,
                limit=limit,
            )
        }
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/files/{file_id}")
def get_workspace_file_content(
    file_id: str,
    current_user: User = Depends(get_current_user),
):
    """Baca isi teks file personal berdasarkan ID."""
    try:
        return read_file(current_user.id, file_id)
    except WorkspaceError as exc:
        message = str(exc).lower()
        if "tidak ditemukan" in message:
            status = 404
        elif "lebih dari satu" in message:
            status = 409
        else:
            status = 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc

