"""Handler slash command & bahasa alami bergaya Telegram untuk Web Chat OM.AI."""
import json, re
from typing import Any
from sqlalchemy.orm import Session
from app.models.automation import AutomationExecution, AutomationWorkflow, AutomationWorkflowAccess
from app.models.user import User
from app.services.quota_service import daily_token_usage
from app.models.subscription import TokenWallet
from app.services import telegram_workspace_service as ws_tg

SLASH_HELP = (
    "📋 Daftar Perintah Chat OM.AI (Web & Telegram):\n\n"
    "⚙️ Otomatisasi:\n"
    "• /automation — Daftar otomatisasi & tombol eksekusi\n"
    "• /automation_run ID [JSON] — Jalankan workflow\n"
    "• /automation_history — Riwayat 10 eksekusi terakhir\n\n"
    "📁 File & Folder (Tanpa Menu Workspace):\n"
    "• /daftar_file — Lihat daftar file tersimpan\n"
    "• /cari_file kata_kunci — Cari file\n"
    "• /folder [path] — Lihat daftar folder\n"
    "• /buat_folder nama — Buat folder baru\n"
    "• /baca ID_FILE — Baca isi dokumen\n"
    "• /unduh ID_FILE — Munculkan tombol unduh file\n"
    "• /rename_file ID_FILE nama_baru — Ubah nama file\n"
    "• /hapus_file ID_FILE — Hapus file\n"
    "• /hapus_folder path — Hapus folder\n"
    "• /storage — Cek pemakaian penyimpanan\n\n"
    "ℹ️ Akun & Bantuan:\n"
    "• /status — Cek status akun, paket & kuota token\n"
    "• /menu atau /help — Tampilkan panduan ini"
)

def _user_workflows(db: Session, user: User) -> list[AutomationWorkflow]:
    rows = db.query(AutomationWorkflow).filter(
        AutomationWorkflow.is_published.is_(True),
        AutomationWorkflow.is_active.is_(True),
        AutomationWorkflow.webhook_path.isnot(None),
    ).order_by(AutomationWorkflow.name.asc()).all()
    out = []
    for wf in rows:
        if wf.access_mode == "all":
            out.append(wf)
        elif wf.access_mode == "selected":
            acc = db.query(AutomationWorkflowAccess).filter(
                AutomationWorkflowAccess.workflow_id == wf.id,
                AutomationWorkflowAccess.user_id == user.id,
                AutomationWorkflowAccess.is_allowed.is_(True),
            ).first()
            if acc:
                out.append(wf)
    return out

def handle_web_chat_command(db: Session, user: User, raw_text: str) -> dict[str, Any] | None:
    text = (raw_text or "").strip()
    if not text:
        return None
    low = text.lower()

    # Deteksi bahasa alami untuk fitur Telegram di Web Chat
    if not text.startswith("/"):
        if re.fullmatch(r"(?:daftar\s+file|file\s+saya|lihat\s+file|cek\s+file)", low):
            text = "/daftar_file"
        elif re.fullmatch(r"(?:cek\s+storage|kapasitas\s+penyimpanan|penyimpanan\s+saya)", low):
            text = "/storage"
        elif re.fullmatch(r"(?:cek\s+kuota|sisa\s+kuota|status\s+akun|cek\s+status)", low):
            text = "/status"
        elif re.fullmatch(r"(?:menu\s+otomatisasi|daftar\s+automation|jalankan\s+automation|list\s+automation)", low):
            text = "/automation"
        elif re.fullmatch(r"(?:riwayat\s+automation|riwayat\s+otomatisasi|automation\s+history)", low):
            text = "/automation_history"
        else:
            return None

    parts = text.split(None, 2)
    cmd = parts[0].lower()
    arg1 = parts[1].strip() if len(parts) > 1 else ""
    arg2 = parts[2].strip() if len(parts) > 2 else ""
    rest = text[len(parts[0]):].strip()

    if cmd in {"/menu", "/help", "/start"}:
        return {
            "skill": "command",
            "answer": SLASH_HELP,
            "actions": [
                {"type": "command", "label": "⚙️ Automation", "value": "/automation"},
                {"type": "command", "label": "📁 Daftar File", "value": "/daftar_file"},
                {"type": "command", "label": "💾 Storage", "value": "/storage"},
                {"type": "command", "label": "📊 Status Kuota", "value": "/status"},
            ],
        }

    if cmd == "/status":
        if user.role == "admin":
            ans = f"👑 Akun Administrator: {user.full_name or user.email}\nStatus: Aktif · Kuota AI: Tanpa batas"
        else:
            lim, used = daily_token_usage(db, user)
            rem = max(0, lim - used)
            w = db.query(TokenWallet).filter(TokenWallet.user_id == user.id).first()
            bal = int(w.topup_balance or 0) if w else 0
            ans = (
                f"👤 Akun: {user.full_name or user.email}\n"
                f"• Kuota Harian: {used:,} / {lim:,} token (Sisa: {rem:,})\n"
                f"• Saldo Top Up: {bal:,} token"
            )
        return {"skill": "command", "answer": ans}

    if cmd == "/automation":
        wfs = _user_workflows(db, user)
        if not wfs:
            return {"skill": "automation", "answer": "Belum ada workflow automation aktif yang tersedia untuk akun Anda."}
        lines = ["⚙️ Daftar Automation Tersedia:\n"]
        actions = []
        for idx, wf in enumerate(wfs, 1):
            props = (wf.input_schema or {}).get("properties") or {}
            req = (wf.input_schema or {}).get("required") or []
            param_txt = ", ".join(props.keys()) if props else "Tanpa parameter"
            lines.append(f"{idx}. {wf.name}\n   ID: {wf.id}\n   Parameter: {param_txt}")
            if wf.description:
                lines.append(f"   Ket: {wf.description[:140]}")
            actions.append({
                "type": "run_automation",
                "label": f"▶️ {wf.name}",
                "workflow_id": wf.id,
                "workflow_name": wf.name,
                "fields": [
                    {"name": k, "label": (v.get("description") or k) if isinstance(v, dict) else k, "required": k in req}
                    for k, v in props.items()
                ],
            })
        lines.append("\nKlik tombol di bawah atau ketik /automation_run ID_WORKFLOW")
        return {"skill": "automation", "answer": "\n".join(lines), "actions": actions}

    if cmd == "/automation_history":
        recs = db.query(AutomationExecution).filter(AutomationExecution.user_id == user.id).order_by(AutomationExecution.created_at.desc()).limit(10).all()
        if not recs:
            return {"skill": "automation", "answer": "Belum ada riwayat eksekusi automation."}
        lines = ["📜 10 Riwayat Eksekusi Automation Terakhir:\n"]
        for r in recs:
            res_preview = ""
            if isinstance(r.result_data, dict):
                res_preview = str(r.result_data.get("message") or r.result_data.get("text") or "")[:180]
            lines.append(f"• {r.workflow_name} [{r.status.upper()}] ({r.created_at.strftime('%d/%m %H:%M')})")
            if res_preview:
                lines.append(f"  Hasil: {res_preview}")
            elif r.error_message:
                lines.append(f"  Error: {r.error_message[:140]}")
        return {"skill": "automation", "answer": "\n".join(lines)}

    uid = str(user.id)
    if cmd == "/daftar_file":
        return {
            "skill": "workspace",
            "answer": ws_tg.list_user_files(uid),
            "actions": [
                {"type": "command", "label": "📄 Buat dalam DOCX", "value": "buat dalam docx"},
                {"type": "command", "label": "📕 Buat dalam PDF", "value": "buat dalam pdf"},
                {"type": "command", "label": "📂 Daftar Folder", "value": "/folder"},
                {"type": "command", "label": "💾 Cek Storage", "value": "/storage"},
            ],
        }
    if cmd == "/cari_file":
        return {"skill": "workspace", "answer": ws_tg.search_user_files(uid, rest) if rest else "Gunakan: /cari_file kata_kunci"}
    if cmd == "/folder":
        return {"skill": "workspace", "answer": ws_tg.list_user_folders(uid, rest)}
    if cmd == "/buat_folder":
        if not rest:
            return {"skill": "workspace", "answer": "Gunakan: /buat_folder nama_folder"}
        return {"skill": "workspace", "answer": ws_tg.create_user_folder(uid, rest)}
    if cmd == "/storage":
        return {"skill": "workspace", "answer": ws_tg.storage_user_info(db, user)}
    if cmd == "/baca":
        return {"skill": "workspace", "answer": ws_tg.preview_user_file(uid, arg1) if arg1 else "Gunakan: /baca ID_FILE"}
    if cmd == "/unduh":
        if not arg1:
            return {"skill": "workspace", "answer": "Gunakan: /unduh ID_FILE"}
        return {
            "skill": "workspace",
            "answer": f"File siap diunduh (ID: {arg1}). Klik tombol di bawah:",
            "actions": [{"type": "download_file", "label": f"⬇️ Unduh File ({arg1})", "file_id": arg1}],
        }
    if cmd == "/rename_file":
        if not arg1 or not arg2:
            return {"skill": "workspace", "answer": "Gunakan: /rename_file ID_FILE nama_baru"}
        return {"skill": "workspace", "answer": ws_tg.rename_user_file(uid, arg1, arg2)}
    if cmd == "/hapus_file":
        if not arg1:
            return {"skill": "workspace", "answer": "Gunakan: /hapus_file ID_FILE"}
        return {"skill": "workspace", "answer": ws_tg.delete_user_file(uid, arg1)}
    if cmd == "/hapus_folder":
        if not rest:
            return {"skill": "workspace", "answer": "Gunakan: /hapus_folder path_folder"}
        return {"skill": "workspace", "answer": ws_tg.delete_user_folder(uid, rest)}

    return None


def _detect_export_request(low_text: str) -> str | None:
    t = re.sub(r"\s+", " ", (low_text or "").strip())
    if ":" in t:
        return None
    if re.search(r"\b(outline|materi|makalah|proposal|skripsi|rangkuman|ringkasan|analisis|penjelasan|panduan|soal|kode|program|tentang|mengenai)\b", t):
        return None
    patterns = [
        (r"^(?:tolong\s+|coba\s+|silakan\s+)?(?:buat|jadikan|simpan|ekspor|export|ubah|konversi|download|unduh)\s+(?:ini\s+|itu\s+|jawaban\s+(?:ini|terakhir|di\s+atas)|daftar\s+(?:ini|tersebut))?\s*(?:dalam|ke|menjadi|sebagai|format)?\s*(?:file\s+)?(docx|word)\s*$", "docx"),
        (r"^(?:tolong\s+|coba\s+|silakan\s+)?(?:buat|jadikan|simpan|ekspor|export|ubah|konversi|download|unduh)\s+(?:ini\s+|itu\s+|jawaban\s+(?:ini|terakhir|di\s+atas)|daftar\s+(?:ini|tersebut))?\s*(?:dalam|ke|menjadi|sebagai|format)?\s*(?:file\s+)?pdf\s*$", "pdf"),
    ]
    for pat, fmt in patterns:
        if re.search(pat, t):
            return fmt
    return None


def _infer_smart_filename(content: str, fmt: str) -> tuple[str, str]:
    low = (content or "").lower()
    if "daftar file" in low or "folder: hasil ai" in low or "/daftar_file" in low:
        base = "Daftar_File_Workspace"
        title = "Daftar File Workspace"
    elif "riwayat eksekusi automation" in low or "/automation_history" in low:
        base = "Riwayat_Eksekusi_Automation"
        title = "Riwayat Eksekusi Automation"
    elif "daftar automation tersedia" in low or "/automation" in low:
        base = "Daftar_Otomatisasi_OMAI"
        title = "Daftar Otomatisasi OM.AI"
    elif "pemakaian penyimpanan" in low or "/storage" in low:
        base = "Informasi_Penyimpanan_OMAI"
        title = "Informasi Penyimpanan OM.AI"
    else:
        lines = [ln.strip("#*•- \t") for ln in (content or "").splitlines() if ln.strip()]
        cand = lines[0] if lines else "Dokumen_OM_AI"
        if len(cand) < 8 and len(lines) > 1:
            cand = f"{cand} - {lines[1]}"
        words = re.findall(r"[A-Za-z0-9]+", cand)[:6]
        base = "_".join(words).strip("_") or "Dokumen_OM_AI"
        title = cand[:70].strip() or "Dokumen OM.AI"
    base = re.sub(r"_+", "_", base)[:48].strip("_") or "Dokumen_OM_AI"
    return title, f"{base}.{fmt}"


def try_export_last_assistant_message(db: Session, user: User, conv_id: str, raw_text: str) -> dict[str, Any] | None:
    fmt = _detect_export_request((raw_text or "").lower())
    if not fmt:
        return None
    import secrets
    from app.models.conversation import Conversation, Message
    last_asst = (
        db.query(Message)
        .filter(Message.conversation_id == conv_id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
        .first()
    )
    if not last_asst:
        last_asst = (
            db.query(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .filter(Conversation.user_id == user.id, Message.role == "assistant")
            .order_by(Message.created_at.desc())
            .first()
        )
    if not last_asst or not (last_asst.content or "").strip():
        return None
    content = last_asst.content.strip()
    title, smart_filename = _infer_smart_filename(content, fmt)
    file_id = secrets.token_hex(6)
    ws_tg.create_user_document(
        db=db,
        user=user,
        title=title,
        body_text=content,
        file_format=fmt,
        file_id=file_id,
    )
    return {
        "skill": "workspace",
        "answer": f"✅ Dokumen berhasil dibuat dalam format {fmt.upper()}:\n📄 {smart_filename}\nID File: {file_id}",
        "actions": [
            {
                "type": "download_file",
                "label": f"⬇️ Unduh {smart_filename}",
                "file_id": file_id,
                "filename": smart_filename,
            },
            {"type": "command", "label": "📁 Lihat Daftar File", "value": "/daftar_file"},
        ],
    }
