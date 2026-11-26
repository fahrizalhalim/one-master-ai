import re

_GEN_W = set(
    "jawaban_chat dokumen_ai dokumen file berkas laporan ringkasan rangkuman "
    "catatan artikel makalah esai essay surat proposal materi modul presentasi "
    "slide tabel daftar jadwal draft draf naskah skrip script resume cv soal "
    "rpp rps silabus review analisis analisa evaluasi jawaban hasil chat "
    "pdf docx doc word xlsx xls excel pptx ppt powerpoint markdown md txt text".split()
)
_GEN_P = {"jawaban chat", "dokumen ai"}
_GEN_S = {
    "pendahuluan", "daftar isi", "latar belakang", "ringkasan", "rangkuman",
    "kesimpulan", "penutup", "abstrak", "abstract", "introduction", "overview",
    "conclusion", "pembahasan", "isi", "catatan", "referensi", "daftar pustaka",
    "tujuan", "manfaat", "metode", "metodologi",
}
_GEN_C = {"new chat", "chat baru", "percakapan baru", "obrolan baru", "telegram chat", "untitled"}
_FILL_RE = re.compile(
    r"^(?:tentu(?:nya)?|baik(?:lah)?|siap|oke|ok|halo|hai|selamat\s+\w+|"
    r"terima\s+kasih|dengan\s+senang\s+hati|mohon\s+maaf|maaf|"
    r"sure|certainly|of\s+course|here(?:'s|\s+is))\b[,!.\s\-]*", re.I,
)
_INTRO_RE = re.compile(r"^(?:berikut(?:\s+ini)?|di\s+bawah\s+ini|ini)\s+(?:adalah|merupakan)?\s*", re.I)

def is_generic_title(t):
    if not t or not t.strip(): return True
    lo = t.strip().lower()
    nm = re.sub(r"[\s_\-]+", " ", lo).strip()
    return lo in _GEN_W or nm in _GEN_W or nm in _GEN_P

def sanitize_title(raw, max_len=70):
    if not raw: return ""
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", str(raw).strip())
    t = re.sub(r"^[\s#>*`•▪▫‣⁃\-–—]+", "", t)
    t = re.sub(r"^(?:\d{1,2}|[IVX]{1,4})[.)]\s+", "", t)
    t = re.sub(r"[*_`~]+", "", t)
    t = re.sub(r"\s*:\s*", " - ", t)
    t = re.sub(r'[\\/*?"<>|\r\n\t]+', " ", t)
    t = re.sub(r"[^\w\s\-.,()&+%]", " ", t, flags=re.U)
    t = re.sub(r"\s*-\s*-\s*", " - ", t)
    t = re.sub(r"\s+", " ", t).strip(" -_.,;:")
    if len(t) > max_len:
        c = t[:max_len]
        t = (c.rsplit(" ", 1)[0] if " " in c else c).strip(" -_.,;:")
    return t
def extract_title_from_content(body):
    if not body or not body.strip(): return None
    code, lines = False, []
    for raw in body.splitlines():
        s = raw.strip()
        if s.startswith("```"): code = not code; continue
        if code or not s or s.startswith("|") or re.fullmatch(r"[-_=*]{3,}", s) or re.match(r"^https?://\S+$", s, re.I): continue
        lines.append(s)
        if len(lines) >= 25: break
    for s in lines[:15]:
        m = re.match(r"^[#>*_\-\s]*(?:judul|title|topik|tema)\s*[:\-]\s*(.+)$", s, re.I)
        if m:
            c = sanitize_title(m.group(1))
            if len(c) >= 3 and c.lower() not in _GEN_S: return c
    fb = None
    for s in lines[:20]:
        m = re.match(r"^(#{1,3})\s+(.+)$", s)
        if not m: continue
        c = sanitize_title(m.group(2))
        if len(c) >= 3 and c.lower() not in _GEN_S:
            if len(m.group(1)) == 1: return c
            fb = fb or c
    return fb or _fallback_line(lines)
def _fallback_line(lines):
    for s in lines[:12]:
        mb = re.fullmatch(r"\*\*([^*]{4,120})\*\*:?", s)
        if mb:
            c = sanitize_title(mb.group(1))
            if len(c) >= 4 and c.lower() not in _GEN_S: return c
        p = sanitize_title(s)
        al = [ch for ch in p if ch.isalpha()]
        if len(al) >= 6 and len(p) <= 80 and sum(ch.isupper() for ch in al)/len(al) >= 0.85 and p.lower() not in _GEN_S:
            return p.title()
    intro = None
    for s in lines[:10]:
        if re.match(r"^[-*•]\s+", s): continue
        cur = s
        for _ in range(3):
            nxt = _INTRO_RE.sub("", _FILL_RE.sub("", cur).strip()).strip()
            if nxt == cur: break
            cur = nxt
        c = sanitize_title(re.split(r"(?<=[.!?])\s+", cur, maxsplit=1)[0])
        if len(c) < 4 or c.lower() in _GEN_S or is_generic_title(c): continue
        c = c[0].upper() + c[1:] if c[0].islower() else c
        if cur != s: intro = intro or c; continue
        return c
    return intro
def resolve_document_title(current_title, body_text, db=None, conversation_id=None, pure_export_checker=None, conv_model=None):
    if current_title and not is_generic_title(current_title):
        cl = sanitize_title(current_title)
        if cl: return cl
    ex = extract_title_from_content(body_text)
    if ex: return ex
    if db is not None and conversation_id:
        try:
            if conv_model is None:
                from app.models import Conversation as conv_model
            cv = db.query(conv_model).filter(conv_model.id == conversation_id).first()
            ct = getattr(cv, "title", None) if cv else None
            if isinstance(ct, str) and ct.strip():
                cc = sanitize_title(ct)
                pu = pure_export_checker(ct.strip()) if pure_export_checker else False
                if len(cc) >= 3 and cc.lower() not in _GEN_C and not is_generic_title(cc) and not pu:
                    return cc
        except Exception:
            pass
    return sanitize_title(current_title) or "Dokumen AI"
