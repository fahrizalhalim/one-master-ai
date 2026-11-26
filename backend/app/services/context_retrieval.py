"""Retrieval konteks percakapan dan dokumen lama milik pengguna."""

import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.conversation import Conversation, Message


STOPWORDS = {
    "yang", "dan", "atau", "untuk", "dari", "dengan", "pada",
    "adalah", "ini", "itu", "saya", "aku", "anda", "kamu",
    "tolong", "mohon", "bisa", "buat", "bagaimana", "apakah",
    "jelaskan", "berikan", "sebuah", "dalam", "agar", "juga",
    "lebih", "sekarang", "maka", "akan", "dapat", "sudah",
    "belum", "tentang", "pembahasan", "lanjutkan", "lanjut",
    "chat", "percakapan", "sebelumnya", "file", "dokumen",
    "berkas", "tersebut", "mereka", "kapan", "dimana",
    "mengapa", "siapa", "oleh", "terhadap", "sebagai",
}


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9_]{3,}", str(text or "").lower())
    unique = []

    for word in words:
        if word not in STOPWORDS and word not in unique:
            unique.append(word)

    return unique[:8]


def search_past_conversations(
    db: Session,
    user_id: str,
    query: str,
    limit: int = 3,
    max_chars: int = 4500,
    exclude_conversation_id: str | None = None,
) -> str:
    """Cari konteks percakapan lama dan pasangan pesan yang relevan."""

    keywords = _keywords(query)

    if not keywords:
        return ""

    conditions = [
        Message.content.ilike(f"%{word}%")
        for word in keywords
    ]

    query_db = (
        db.query(Message, Conversation)
        .join(
            Conversation,
            Message.conversation_id == Conversation.id,
        )
        .filter(
            Conversation.user_id == str(user_id),
            or_(*conditions),
        )
    )

    if exclude_conversation_id:
        query_db = query_db.filter(
            Conversation.id != exclude_conversation_id
        )

    matches = (
        query_db
        .order_by(Message.created_at.desc())
        .limit(100)
        .all()
    )

    if not matches:
        return ""

    scored = {}

    for message, conversation in matches:
        content = str(message.content or "").lower()
        matched_words = {
            word for word in keywords if word in content
        }

        if not matched_words:
            continue

        entry = scored.setdefault(
            conversation.id,
            {
                "conversation": conversation,
                "matched_words": set(),
                "matched_ids": set(),
            },
        )

        entry["matched_words"].update(matched_words)
        entry["matched_ids"].add(message.id)

    for entry in scored.values():
        entry["score"] = len(entry["matched_words"])

    ranked = sorted(
        scored.values(),
        key=lambda item: (
            item["score"],
            item["conversation"].updated_at
            or item["conversation"].created_at,
        ),
        reverse=True,
    )[:max(1, min(int(limit), 10))]

    output = []
    remaining = max(500, int(max_chars))

    for entry in ranked:
        conversation = entry["conversation"]

        all_messages = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.asc())
            .all()
        )

        if not all_messages:
            continue

        matched_indexes = {
            index
            for index, message in enumerate(all_messages)
            if message.id in entry["matched_ids"]
        }

        # Ambil pesan yang cocok beserta konteks di sekitarnya.
        selected_indexes = set()

        for index in matched_indexes:
            selected_indexes.update(
                range(
                    max(0, index - 1),
                    min(len(all_messages), index + 2),
                )
            )

        selected = [
            all_messages[index]
            for index in sorted(selected_indexes)
        ]

        block = [
            f"Percakapan lama: {conversation.title or 'Percakapan lama'}",
        ]

        for message in selected:
            role = "User" if message.role == "user" else "Asisten"
            content = str(message.content or "").strip()

            if content:
                block.append(f"{role}: {content[:900]}")

        text = "\n".join(block)[:remaining]

        if text:
            output.append(text)
            remaining -= len(text)

        if remaining <= 0:
            break

    if not output:
        return ""

    return (
        "KONTEKS PERCAKAPAN TERDAHULU (hasil pencarian):\n\n"
        + "\n\n---\n\n".join(output)
    )


def search_past_files(
    user_id: str,
    query: str,
    limit: int = 2,
    max_chars: int = 4500,
) -> str:
    """Cari dan baca file relevan dari Workspace milik pengguna."""

    keywords = _keywords(query)

    if not keywords:
        return ""

    try:
        from app.services.workspace_service import (
            search_files,
            read_file,
            WorkspaceError,
        )
    except ImportError:
        return ""

    candidates = {}

    # Cari berdasarkan kata kunci, bukan hanya file terbaru.
    for keyword in keywords[:6]:
        try:
            results = search_files(
                user_id,
                query=keyword,
                limit=10,
            )
        except (WorkspaceError, OSError, ValueError):
            continue

        for item in results:
            file_id = str(item.get("id") or "")

            if not file_id:
                continue

            entry = candidates.setdefault(
                file_id,
                {
                    "item": item,
                    "score": 0,
                },
            )

            entry["score"] += 1

    if not candidates:
        return ""

    ranked = sorted(
        candidates.values(),
        key=lambda item: item["score"],
        reverse=True,
    )[:max(1, min(int(limit), 5))]

    output = []
    remaining = max(500, int(max_chars))

    for entry in ranked:
        item = entry["item"]

        try:
            result = read_file(
                user_id,
                str(item["id"]),
                max_chars=min(3500, remaining),
            )
        except (WorkspaceError, OSError, ValueError):
            continue

        content = str(result.get("content") or "").strip()

        if not content:
            continue

        block = (
            f"Nama file: {result.get('filename', item.get('filename', 'File'))}\n"
            f"Lokasi: {result.get('folder_path', item.get('folder_path', ''))}\n"
            f"Isi file:\n{content}"
        )

        block = block[:remaining]

        if block:
            output.append(block)
            remaining -= len(block)

        if remaining <= 0:
            break

    if not output:
        return ""

    return (
        "DOKUMEN LAMA YANG DITEMUKAN (hasil pencarian Workspace):\n\n"
        + "\n\n---\n\n".join(output)
    )
