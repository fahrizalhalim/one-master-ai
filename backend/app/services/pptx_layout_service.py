import io
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_WIT = timezone(timedelta(hours=9))
_NAVY = (15, 23, 42)
_BLUE = (37, 99, 235)
_SKY = (56, 189, 248)
_BG = (248, 250, 252)
_WHITE = (255, 255, 255)
_TEXT = (30, 41, 59)
_MUTED = (100, 116, 139)
_BORDER = (226, 232, 240)
_TINT = (239, 246, 255)

_SLIDE_RE = re.compile(r"^(?:#{1,6}\s*)?(?:\*\*)?(?:slide|salindia)\s*(\d+)(?:\s*[—–:\-|]\s*(.+?))?(?:\*\*)?\s*$", re.I)
_VISUAL_RE = re.compile(r"^(?:[-*•]\s*)?(?:\*\*)?(?:visual|gambar|ilustrasi|image(?:\s*query)?)\s*:\s*(?:\*\*)?\s*(.+)$", re.I)
_RULE_RE = re.compile(r"^[-*_─═=]{3,}$")
_CHATTER_RE = re.compile(r"^(halo|berikut|semoga|jika anda|kalau kamu|silakan|mau dikembangkan|catatan:)", re.I)
_SKIP_IMG_RE = re.compile(r"(flag_of|coat_of_arms|icon|logo|blank|stub|\.svg)", re.I)


def _rgb(t):
    from pptx.dml.color import RGBColor
    return RGBColor(*t)


def _clean(s: str) -> str:
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", str(s or ""))
    s = re.sub(r"\*([^*\n]+)\*", r"\1", s)
    return re.sub(r"`([^`]+)`", r"\1", s).strip()


def _is_kv(s: str) -> bool:
    if _VISUAL_RE.match(s.strip()):
        return False
    return bool(re.match(r"^(?:\*\*[^*]{1,36}:\*\*|[A-Za-z][A-Za-z0-9 /()-]{1,28}:)\s*\S", s.strip()))


def _strip_outro(lines: list[str]) -> list[str]:
    out = list(lines)
    while out and not out[-1].strip():
        out.pop()
    for idx in range(len(out) - 1, max(-1, len(out) - 8), -1):
        if _RULE_RE.match(out[idx].strip()):
            tail = [x.strip() for x in out[idx + 1 :] if x.strip()]
            if tail and _CHATTER_RE.match(_clean(tail[0])):
                return out[:idx]
            break
    if out and _CHATTER_RE.match(_clean(out[-1])):
        out.pop()
    return out


def _normalize_image_bytes(raw_bytes: bytes) -> tuple[int, int, io.BytesIO] | None:
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        w, h = img.size
        if w < 240 or h < 160 or (w / max(1, h)) > 2.8 or (w / max(1, h)) < 0.4:
            return None
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, rgba).convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return w, h, buf
    except Exception:
        return None


def _parse_deck(title: str, body_text: str) -> tuple[dict, list[dict]]:
    lines = _strip_outro(str(body_text or "").splitlines())
    meta, raw_slides, cur = [], [], None
    has_exp = any(_SLIDE_RE.match(x.strip()) for x in lines)
    for ln in lines:
        s = ln.strip()
        m = _SLIDE_RE.match(s) if has_exp else None
        mh = re.match(r"^(#{1,3})\s+(.+)$", s) if not has_exp else None
        if m or mh:
            if cur and (has_exp or any(x.strip() for x in cur["lines"])):
                raw_slides.append(cur)
            num = m.group(1) if m else str(len(raw_slides) + 1)
            st = _clean((m.group(2) if m else mh.group(2)) or "")
            cur = {"num": num, "title": st, "lines": []}
        elif cur is None:
            if _is_kv(s):
                meta.append(s)
            elif not has_exp and s and not _RULE_RE.match(s) and not _CHATTER_RE.match(_clean(s)):
                cur = {"num": "1", "title": title, "lines": [ln]}
        else:
            cur["lines"].append(ln)
    if cur and (has_exp or any(x.strip() for x in cur["lines"])):
        raw_slides.append(cur)
    cover = {"eyebrow": title.upper(), "title": title, "meta": list(meta)}
    if raw_slides and (raw_slides[0]["title"].lower() in ("judul", "cover", "title", "") or raw_slides[0]["title"].lower() == title.lower()):
        first, hero = raw_slides.pop(0), None
        for ln in first["lines"]:
            s = ln.strip()
            if not s or _RULE_RE.match(s) or _VISUAL_RE.match(s):
                continue
            if re.match(r"^[-*•]\s+", s):
                item = re.sub(r"^[-*•]\s+", "", s).strip()
                if _clean(item) not in [_clean(x) for x in cover["meta"]]:
                    cover["meta"].append(item)
            elif hero is None:
                hero = _clean(s)
            elif _clean(s) not in [_clean(x) for x in cover["meta"]]:
                cover["meta"].append(s)
        if hero:
            cover["title"] = hero
    return cover, raw_slides


def _parse_items(lines: list[str]) -> dict:
    lead, bullets, nums, groups, table, note, visual = [], [], [], [], None, None, None
    i, n, cur_grp = 0, len(lines), None
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s or _RULE_RE.match(s):
            i += 1
            continue
        mv = _VISUAL_RE.match(s)
        if mv:
            visual = _clean(mv.group(1)).strip("[]")
            i += 1
            continue
        if "|" in s and i + 1 < n and "|" in lines[i + 1] and re.search(r"-{2,}", lines[i + 1]):
            def _cells(r):
                return [c.strip() for c in r.strip().strip("|").split("|")]
            hdr = _cells(s)
            i += 2
            rows = [hdr]
            while i < n and "|" in lines[i] and lines[i].strip():
                r = _cells(lines[i])
                rows.append((r + [""] * len(hdr))[:len(hdr)])
                i += 1
            table, cur_grp = rows, None
            continue
        if re.match(r"^(?:\*\*)?kesimpulan\s*:(?:\*\*)?", s, re.I):
            note = s
            i += 1
            continue
        mn = re.match(r"^\d+[.)]\s+(.+)$", s)
        if mn:
            nums.append(mn.group(1).strip())
            cur_grp = None
            i += 1
            continue
        mb = re.match(r"^[-*•▪▸]\s+(.+)$", s)
        if mb:
            indent = len(raw) - len(raw.lstrip(" \t"))
            txt = mb.group(1).strip()
            (cur_grp[1] if cur_grp is not None else bullets).append((indent >= 2, txt))
            i += 1
            continue
        is_bh = bool(re.fullmatch(r"\*\*[^*]{2,46}:?\*\*:?", s))
        is_sh = s.endswith(":") and len(_clean(s)) <= 36
        nxt = next((lines[k].strip() for k in range(i + 1, n) if lines[k].strip()), "")
        if (is_bh or is_sh) and re.match(r"^[-*•▪▸]\s+", nxt):
            cur_grp = (_clean(s).rstrip(":"), [])
            groups.append(cur_grp)
            i += 1
            continue
        if bullets or groups or nums:
            if cur_grp is not None:
                cur_grp[1].append((True, s))
            else:
                note = f"{note} {s}".strip() if note else s
        else:
            lead.append(s)
        i += 1
    return {"lead": lead, "bullets": bullets, "nums": nums, "groups": groups, "table": table, "note": note, "visual": visual}


def _render_fallback_visual(title: str, query: str) -> tuple[int, int, io.BytesIO]:
    from PIL import Image, ImageDraw, ImageFont
    w, h = 900, 675
    img = Image.new("RGB", (w, h), _TINT)
    draw = ImageDraw.Draw(img)
    draw.ellipse((540, -110, 1020, 370), fill=(219, 234, 254))
    draw.ellipse((-120, 410, 310, 840), fill=(224, 242, 254))
    draw.rectangle((0, 0, w, 18), fill=_BLUE)
    draw.rounded_rectangle((56, 56, 290, 104), radius=14, fill=_NAVY)
    try:
        fb = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        ft = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 38)
        fs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except Exception:
        fb = ft = fs = ImageFont.load_default()
    draw.text((82, 68), "ILUSTRASI SLIDE", fill=_WHITE, font=fb)
    lines, cur = [], ""
    for wd in _clean(title).split():
        cand = f"{cur} {wd}".strip()
        if len(cand) <= 24:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    y = 155
    for ln in lines[:4]:
        draw.text((58, y), ln, fill=_NAVY, font=ft)
        y += 52
    draw.rectangle((58, y + 18, 190, y + 26), fill=_BLUE)
    topic = _clean(query or title)[:44]
    draw.rounded_rectangle((56, 555, 844, 622), radius=14, fill=_WHITE, outline=(191, 219, 254), width=2)
    draw.text((80, 575), f"Topik: {topic}", fill=_TEXT, font=fs)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return w, h, buf


def _fetch_slide_visual(query: str, fallback_title: str, used_urls: set[str]) -> tuple[int, int, io.BytesIO]:
    import httpx
    q = " ".join(re.sub(r"[^A-Za-z0-9\s-]", " ", str(query or fallback_title)).split()[:5]) or "technology presentation"
    headers = {"User-Agent": "OneMasterAI-PPTX/1.0 (https://gamalama-mht.my.id)"}
    pexels_key = os.getenv("PEXELS_API_KEY", "").strip()
    try:
        with httpx.Client(timeout=6.0, follow_redirects=True, headers=headers) as client:
            if pexels_key:
                r = client.get("https://api.pexels.com/v1/search", params={"query": q, "per_page": 5, "orientation": "landscape"}, headers={"Authorization": pexels_key})
                if r.status_code == 200:
                    for ph in r.json().get("photos") or []:
                        src = (ph.get("src") or {}).get("large") or ""
                        if src and src not in used_urls:
                            ir = client.get(src)
                            norm = _normalize_image_bytes(ir.content) if ir.status_code == 200 else None
                            if norm:
                                used_urls.add(src)
                                return norm
            r = client.get("https://commons.wikimedia.org/w/api.php", params={"action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {q}", "gsrnamespace": 6, "gsrlimit": 6, "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": 960})
            if r.status_code == 200:
                for pg in ((r.json().get("query") or {}).get("pages") or {}).values():
                    if _SKIP_IMG_RE.search(str(pg.get("title") or "")):
                        continue
                    infos = pg.get("imageinfo") or []
                    if not infos:
                        continue
                    info = infos[0]
                    mime = str(info.get("mime") or "")
                    u = info.get("thumburl") or info.get("url") or ""
                    if mime.startswith("image/") and "svg" not in mime and u and u not in used_urls:
                        ir = client.get(u)
                        norm = _normalize_image_bytes(ir.content) if ir.status_code == 200 else None
                        if norm:
                            used_urls.add(u)
                            return norm
    except Exception:
        pass
    return _render_fallback_visual(fallback_title, q)


def _set_bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(color)


def _rect(slide, x, y, w, h, fill, border=None, rounded=False):
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt
    st = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(st, Inches(x), Inches(y), Inches(w), Inches(h))
    if rounded:
        sh.adjustments[0] = 0.05
    sh.fill.solid()
    sh.fill.fore_color.rgb = _rgb(fill)
    if border:
        sh.line.color.rgb = _rgb(border)
        sh.line.width = Pt(1)
    else:
        sh.line.fill.background()
    return sh


def _runs(p, text: str, size: float, color=_TEXT, bold=False):
    from pptx.util import Pt
    for tok in re.split(r"(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`)", str(text or "")):
        if not tok:
            continue
        r = p.add_run()
        r.font.name = "Calibri"
        r.font.size = Pt(size)
        r.font.color.rgb = _rgb(color)
        if tok.startswith("**") and tok.endswith("**"):
            r.text = tok[2:-2]
            r.font.bold = True
        elif tok.startswith("*") and tok.endswith("*"):
            r.text = tok[1:-1]
            r.font.italic = True
            r.font.bold = bold
        elif tok.startswith("`") and tok.endswith("`"):
            r.text = tok[1:-1]
            r.font.name = "Courier New"
            r.font.size = Pt(max(10, size - 1))
        else:
            r.text = tok
            r.font.bold = bold


def _tb(slide, x, y, w, h):
    from pptx.util import Inches
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    return tf


def _place_visual_card(slide, x: float, y: float, w: float, h: float, visual_data: tuple, caption: str, dark: bool = False):
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches
    img_w, img_h, img_buf = visual_data
    _rect(slide, x, y, w, h, (30, 41, 59) if dark else _WHITE, border=(56, 189, 248) if dark else _BORDER, rounded=True)
    cap_h, pad = (0.42 if caption else 0.0), 0.14
    bw, bh = max(0.5, w - pad * 2), max(0.5, h - pad * 2 - cap_h)
    sc = min(bw / max(1, img_w), bh / max(1, img_h))
    dw, dh = img_w * sc, img_h * sc
    img_buf.seek(0)
    slide.shapes.add_picture(img_buf, Inches(x + (w - dw) / 2), Inches(y + pad + (bh - dh) / 2), width=Inches(dw), height=Inches(dh))
    if caption:
        ctf = _tb(slide, x + 0.14, y + h - cap_h + 0.04, w - 0.28, cap_h - 0.06)
        p = ctf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        _runs(p, _clean(caption)[:48], 9.5, (203, 213, 225) if dark else _MUTED, bold=True)


def _add_cover(prs, cover: dict, total: int, cover_visual: tuple | None = None):
    from pptx.util import Pt
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(slide, _NAVY)
    _rect(slide, 0, 0, 0.28, 7.5, _BLUE)
    _rect(slide, 9.6, 0, 3.733, 7.5, (23, 37, 84))
    _rect(slide, 9.6, 0, 0.06, 7.5, _SKY)
    tf = _tb(slide, 0.9, 1.35, 8.2, 0.5)
    _runs(tf.paragraphs[0], cover["eyebrow"], 13, _SKY, bold=True)
    tf2 = _tb(slide, 0.9, 1.95, 8.3, 2.3)
    _runs(tf2.paragraphs[0], cover["title"], 38 if len(cover["title"]) <= 42 else 30, _WHITE, bold=True)
    _rect(slide, 0.9, 4.35, 1.8, 0.07, _SKY)
    if cover["meta"]:
        mtf = _tb(slide, 0.9, 4.75, 8.2, 1.8)
        for idx, item in enumerate(cover["meta"][:5]):
            p = mtf.paragraphs[0] if idx == 0 else mtf.add_paragraph()
            p.space_after = Pt(6)
            _runs(p, item, 14, (203, 213, 225))
    if cover_visual is not None:
        _place_visual_card(slide, 9.95, 0.72, 3.05, 2.25, cover_visual, "", dark=True)
        stf = _tb(slide, 10.1, 3.25, 2.8, 2.2)
    else:
        stf = _tb(slide, 10.1, 2.5, 2.8, 2.2)
    _runs(stf.paragraphs[0], f"{total}", 56, _SKY, bold=True)
    _runs(stf.add_paragraph(), "SLIDE PRESENTASI", 12, (203, 213, 225), bold=True)
    p3 = stf.add_paragraph()
    p3.space_before = Pt(14)
    _runs(p3, "ONE MASTER AI", 11, _WHITE, bold=True)
    _runs(stf.add_paragraph(), datetime.now(_WIT).strftime("%d/%m/%Y %H:%M WIT"), 10.5, (148, 163, 184))


def _chrome(slide, deck_title: str, slide_title: str, idx: int, total: int):
    from pptx.enum.text import PP_ALIGN
    _set_bg(slide, _BG)
    _rect(slide, 0, 0, 13.333, 0.1, _NAVY)
    _runs(_tb(slide, 0.75, 0.28, 9.5, 0.32).paragraphs[0], f"SLIDE {idx:02d}  •  {deck_title.upper()[:60]}", 9.5, _MUTED, bold=True)
    _runs(_tb(slide, 0.75, 0.56, 11.8, 0.68).paragraphs[0], slide_title, 24 if len(slide_title) <= 52 else 20, _NAVY, bold=True)
    _rect(slide, 0.75, 1.24, 1.15, 0.05, _BLUE)
    _rect(slide, 0.75, 6.95, 11.83, 0.015, _BORDER)
    _runs(_tb(slide, 0.75, 7.02, 7.5, 0.3).paragraphs[0], "ONE MASTER AI  •  Slide Siap Pakai", 9, _MUTED)
    p = _tb(slide, 10.5, 7.02, 2.08, 0.3).paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    _runs(p, f"{idx} / {total}", 9.5, _NAVY, bold=True)


def _draw_callout(slide, text: str, y: float, width: float = 11.83) -> float:
    h = 0.72 if len(_clean(text)) <= 95 else 1.02
    _rect(slide, 0.75, y, width, h, _TINT, border=(191, 219, 254), rounded=True)
    _rect(slide, 0.75, y, 0.09, h, _BLUE)
    _runs(_tb(slide, 1.0, y + 0.1, width - 0.4, h - 0.16).paragraphs[0], text, 13.5 if h < 0.9 else 12.5, _NAVY)
    return y + h + 0.22


def _draw_bullets(slide, items: list[tuple[bool, str]], x: float, y: float, w: float, h: float, size: float):
    from pptx.util import Pt
    tf = _tb(slide, x, y, w, h)
    for i, (is_sub, txt) in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(5 if size >= 14 else 3.5)
        p.level = 1 if is_sub else 0
        _runs(p, "◦  " if is_sub else "▸  ", size, _BLUE if not is_sub else _MUTED, bold=not is_sub)
        _runs(p, txt, size - (1 if is_sub else 0), _TEXT)


def _render_content(slide, data: dict, slide_title: str, visual_data: tuple | None = None):
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Inches
    has_vis = visual_data is not None and not data["table"]
    main_w = 7.25 if has_vis else 11.83
    y = _draw_callout(slide, " ".join(data["lead"]), 1.48, width=main_w) if data["lead"] else 1.48
    avail_h = max(1.6, (6.18 if data["note"] else 6.82) - y)
    if has_vis:
        _place_visual_card(slide, 8.22, y, 4.36, avail_h, visual_data, data.get("visual") or slide_title)
    if data["table"]:
        rows = data["table"]
        tbl = slide.shapes.add_table(len(rows), len(rows[0]), Inches(0.75), Inches(y), Inches(11.83), Inches(min(avail_h, 0.55 * len(rows) + 0.3))).table
        for r_i, row in enumerate(rows):
            for c_i, val in enumerate(row):
                cell = tbl.cell(r_i, c_i)
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                cell.fill.solid()
                cell.fill.fore_color.rgb = _rgb(_NAVY if r_i == 0 else (_WHITE if r_i % 2 else _BG))
                _runs(cell.text_frame.paragraphs[0], val, 12.5, _WHITE if r_i == 0 else _TEXT, bold=(r_i == 0))
    elif 2 <= len(data["groups"]) <= 4:
        grps = data["groups"]
        cols = 2 if (has_vis or len(grps) != 3) else 3
        rc = (len(grps) + cols - 1) // cols
        cw, ch = (main_w - 0.24 * (cols - 1)) / cols, (avail_h - 0.22 * (rc - 1)) / rc
        fs = 12.0 if (has_vis or rc > 1 or max(len(g[1]) for g in grps) > 4) else 13.5
        for idx, (gt, gi) in enumerate(grps):
            gx, gy = 0.75 + (idx % cols) * (cw + 0.24), y + (idx // cols) * (ch + 0.22)
            _rect(slide, gx, gy, cw, ch, _WHITE, border=_BORDER, rounded=True)
            _rect(slide, gx, gy, cw, 0.08, _BLUE if idx % 2 == 0 else _NAVY)
            _runs(_tb(slide, gx + 0.16, gy + 0.14, cw - 0.3, 0.38).paragraphs[0], gt, 13.5 if has_vis else 14.5, _NAVY, bold=True)
            _draw_bullets(slide, gi, gx + 0.16, gy + 0.52, cw - 0.3, ch - 0.6, fs)
    elif data["nums"]:
        nums = data["nums"]
        sh = min(0.86, avail_h / max(1, len(nums)))
        fs = 13.5 if (has_vis or len(nums) > 5) else 15.0
        for idx, txt in enumerate(nums):
            ny = y + idx * sh
            _rect(slide, 0.75, ny + 0.04, 0.44, 0.40, _BLUE, rounded=True)
            p = _tb(slide, 0.75, ny + 0.07, 0.44, 0.32).paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            _runs(p, f"{idx + 1:02d}", 11.5, _WHITE, bold=True)
            _runs(_tb(slide, 1.32, ny + 0.05, main_w - 0.62, sh - 0.05).paragraphs[0], txt, fs, _TEXT)
    else:
        items = list(data["bullets"])
        for gt, gi in data["groups"]:
            items.append((False, f"**{gt}**"))
            items.extend((True, x[1]) for x in gi)
        tch = sum(len(x[1]) for x in items)
        if not has_vis and len(items) >= 6:
            mid = (len(items) + 1) // 2
            fs = 14 if tch <= 620 else 12.5
            _rect(slide, 0.75, y, 5.75, avail_h, _WHITE, border=_BORDER, rounded=True)
            _rect(slide, 6.83, y, 5.75, avail_h, _WHITE, border=_BORDER, rounded=True)
            _draw_bullets(slide, items[:mid], 0.95, y + 0.18, 5.35, avail_h - 0.3, fs)
            _draw_bullets(slide, items[mid:], 7.03, y + 0.18, 5.35, avail_h - 0.3, fs)
        elif items:
            fs = (14.5 if len(items) <= 4 and tch <= 300 else (13.0 if tch <= 480 else 11.8)) if has_vis else (16 if len(items) <= 5 and tch <= 420 else 14.5)
            _rect(slide, 0.75, y, main_w, avail_h, _WHITE, border=_BORDER, rounded=True)
            _draw_bullets(slide, items, 0.96, y + 0.2, main_w - 0.42, avail_h - 0.32, fs)
    if data["note"]:
        _rect(slide, 0.75, 6.26, 11.83, 0.56, _NAVY, rounded=True)
        _runs(_tb(slide, 0.95, 6.33, 11.4, 0.44).paragraphs[0], data["note"], 11.5, _WHITE)


def build_pptx_print_ready(title: str, body_text: str, path: Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches
    cover, raw_slides = _parse_deck(title, body_text)
    expanded = []
    for sl in raw_slides:
        data = _parse_items(sl["lines"])
        if not data["table"] and not (2 <= len(data["groups"]) <= 4) and len(data["bullets"]) > 10:
            half = (len(data["bullets"]) + 1) // 2
            expanded.append((f"{sl['title']} (1/2)", {**data, "bullets": data["bullets"][:half], "note": None}))
            expanded.append((f"{sl['title']} (2/2)", {**data, "lead": [], "bullets": data["bullets"][half:]}))
        else:
            expanded.append((sl["title"] or title, data))
    used_urls: set[str] = set()
    cover_vis = _fetch_slide_visual(cover["title"], cover["title"], used_urls)
    total = 1 + len(expanded)
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    _add_cover(prs, cover, total, cover_visual=cover_vis)
    for idx, (s_title, data) in enumerate(expanded, start=2):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _chrome(slide, title, s_title, idx, total)
        vis = None if data["table"] else _fetch_slide_visual(data.get("visual") or s_title, s_title, used_urls)
        _render_content(slide, data, s_title, visual_data=vis)
    prs.save(str(path))
