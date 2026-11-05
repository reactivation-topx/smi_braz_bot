"""Карточки-картинки в едином стиле (Pillow)."""
from __future__ import annotations

import datetime as dt
import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .ai import NEWS_CATEGORIES
from .calendar_engine import CATEGORIES, Occurrence
from .formatting import MONTHS_GEN, MONTHS_SHORT, WEEKDAYS, countdown, date_long
from .states import scope_label

W, H = 1280, 720
PAD = 64

BG_TOP = (12, 22, 40)
BG_BOTTOM = (20, 38, 66)
WHITE = (255, 255, 255)
MUTED = (168, 182, 204)
FAINT = (98, 114, 140)
PANEL = (255, 255, 255, 16)
RED = (239, 68, 68)

BRAND_GREEN = (0, 156, 59)
BRAND_YELLOW = (255, 223, 0)
BRAND_BLUE = (0, 39, 118)

EVENT_COLORS = {
    "holiday": (34, 197, 94),
    "state_holiday": (20, 184, 166),
    "festival": (250, 179, 8),
    "sport": (59, 130, 246),
    "commercial": (236, 72, 153),
    "culture": (168, 85, 247),
}
NEWS_COLORS = {
    "politics": (99, 102, 241),
    "economy": (16, 185, 129),
    "sport": (59, 130, 246),
    "showbiz": (236, 72, 153),
    "incident": (239, 68, 68),
    "weather": (6, 182, 212),
    "society": (245, 158, 11),
    "tech": (139, 92, 246),
    "world": (100, 116, 139),
    "other": (148, 163, 184),
}

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


@lru_cache(maxsize=64)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    files = {
        "regular": "Inter-Regular.otf",
        "medium": "Inter-Medium.otf",
        "semibold": "Inter-SemiBold.otf",
        "bold": "Inter-Bold.otf",
        "italic": "Inter-Italic.otf",
        "display": "InterDisplay-Bold.otf",
        "black": "InterDisplay-Black.otf",
    }
    return ImageFont.truetype(str(FONTS_DIR / files[name]), size)


# ───────────── базовые элементы ─────────────

def _canvas(h: int = H) -> Image.Image:
    img = Image.new("RGB", (W, h), BG_TOP)
    grad = Image.linear_gradient("L").resize((W, h))
    bottom = Image.new("RGB", (W, h), BG_BOTTOM)
    return Image.composite(bottom, img, grad)


def _brand_stripe(img: Image.Image) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    third = W // 3
    d.rectangle([0, 0, third, 8], fill=BRAND_GREEN)
    d.rectangle([third, 0, 2 * third, 8], fill=BRAND_YELLOW)
    d.rectangle([2 * third, 0, W, 8], fill=BRAND_BLUE)


def _header(img: Image.Image, right_chips: list[tuple[str, tuple]] | None = None, y: int = 44) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    f = font("bold", 22)
    d.text((PAD, y), "СМИ БОТ", font=f, fill=WHITE)
    x = PAD + d.textlength("СМИ БОТ", font=f) + 12
    d.text((x, y), "·  БРАЗИЛИЯ", font=font("medium", 22), fill=MUTED)
    if right_chips:
        x = W - PAD
        for text, color in reversed(right_chips):
            w = _chip_width(text)
            x -= w
            _chip(d, x, y - 8, text, color)
            x -= 12


def _chip_width(text: str, size: int = 20) -> int:
    f = font("bold", size)
    return int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=f)) + 36


def _chip(d: ImageDraw.ImageDraw, x: int, y: int, text: str, color: tuple, size: int = 20, filled: bool = True) -> int:
    f = font("bold", size)
    w = int(d.textlength(text, font=f)) + 36
    h = size + 22
    if filled:
        d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=color)
        txt = (15, 23, 42) if _is_light(color) else WHITE
    else:
        d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, outline=color, width=2)
        txt = color
    d.text((x + 18, y + h / 2), text, font=f, fill=txt, anchor="lm")
    return w


def _is_light(c: tuple) -> bool:
    r, g, b = c[:3]
    return (0.299 * r + 0.587 * g + 0.114 * b) > 165


def _wrap(d: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if d.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            # очень длинное слово — режем
            while d.textlength(w, font=f) > max_w and len(w) > 4:
                cut = len(w)
                while cut > 1 and d.textlength(w[:cut] + "-", font=f) > max_w:
                    cut -= 1
                lines.append(w[:cut] + "-")
                w = w[cut:]
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _ellipsize(d, line: str, f, max_w: int) -> str:
    if d.textlength(line, font=f) <= max_w:
        return line
    while line and d.textlength(line + "…", font=f) > max_w:
        line = line[:-1]
    return line.rstrip(" ,.;:—-") + "…"


def _fit_text(d, text: str, face: str, max_size: int, min_size: int, max_w: int, max_lines: int):
    """Подбирает кегль, чтобы текст влез в max_lines строк."""
    size = max_size
    while size >= min_size:
        f = font(face, size)
        lines = _wrap(d, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
        size -= 2
    f = font(face, min_size)
    lines = _wrap(d, text, f, max_w)[:max_lines]
    last = lines[-1]
    while last and d.textlength(last + "…", font=f) > max_w:
        last = last[:-1]
    lines[-1] = last.rstrip(" ,.;:—-") + "…"
    return f, lines


def _draw_lines(d, lines, f, x, y, fill, spacing: float = 1.18) -> int:
    lh = int(f.size * spacing)
    for ln in lines:
        d.text((x, y), ln, font=f, fill=fill)
        y += lh
    return y


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _glow(img: Image.Image, color: tuple, center: tuple[int, int], radius: int, alpha: int = 70) -> Image.Image:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    cx, cy = center
    ld.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(*color[:3], alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(radius // 2))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


# ───────────── карточка события ─────────────

def event_card(occ: Occurrence, today: dt.date) -> bytes:
    ev = occ.event
    color = EVENT_COLORS.get(ev.category, EVENT_COLORS["holiday"])
    cat_name = CATEGORIES.get(ev.category, ("Событие", ""))[0].upper()

    img = _canvas()
    img = _glow(img, color, (W - 120, 140), 300, 60)
    _brand_stripe(img)
    _header(img, [(cat_name, color)])
    d = ImageDraw.Draw(img, "RGBA")

    # плитка с датой
    tx, ty, tw, th = PAD, 128, 300, 400
    d.rounded_rectangle([tx, ty, tx + tw, ty + th], radius=36, fill=color)
    dark = (15, 23, 42) if _is_light(color) else WHITE
    d.text((tx + tw / 2, ty + 52), WEEKDAYS[occ.start.weekday()].upper(), font=font("bold", 24), fill=dark, anchor="mm")
    if occ.is_multiday() and occ.start.month == occ.end.month:
        day_txt, day_size = f"{occ.start.day}–{occ.end.day}", 120
    elif occ.is_multiday():
        day_txt, day_size = f"{occ.start.day}", 190
    else:
        day_txt, day_size = f"{occ.start.day}", 190
    f_day = font("black", day_size)
    while d.textlength(day_txt, font=f_day) > tw - 40 and day_size > 60:
        day_size -= 8
        f_day = font("black", day_size)
    d.text((tx + tw / 2, ty + 200), day_txt, font=f_day, fill=dark, anchor="mm")
    d.text((tx + tw / 2, ty + 318), MONTHS_GEN[occ.start.month - 1].upper(), font=font("bold", 34), fill=dark, anchor="mm")
    sub = str(occ.start.year)
    if occ.is_multiday() and occ.start.month != occ.end.month:
        sub = f"до {occ.end.day} {MONTHS_SHORT[occ.end.month - 1]}"
    d.text((tx + tw / 2, ty + 362), sub, font=font("medium", 24), fill=dark, anchor="mm")

    # правая колонка
    x0 = tx + tw + 56
    max_w = W - PAD - x0
    y = 132
    cd = countdown(occ, today).upper()
    d.text((x0, y), cd, font=font("black", 44), fill=color)
    y += 66
    f_title, lines = _fit_text(d, ev.name_ru, "display", 66, 40, max_w, 3)
    y = _draw_lines(d, lines, f_title, x0, y, WHITE, 1.12) + 6
    if ev.name_pt and ev.name_pt.lower() != ev.name_ru.lower():
        f_pt, pt_lines = _fit_text(d, ev.name_pt, "italic", 28, 22, max_w, 1)
        y = _draw_lines(d, pt_lines, f_pt, x0, y, MUTED) + 10
    # где
    where = scope_label(ev.scope)
    if ev.place:
        where = f"{where} · {ev.place}"
    f_where, where_lines = _fit_text(d, where, "semibold", 26, 20, max_w - 44, 2)
    _pin(d, x0, y + 4, color)
    y = _draw_lines(d, where_lines, f_where, x0 + 36, y, WHITE) + 14
    # короткое описание, если осталось место
    if ev.about:
        room = (528 - y) // 32
        if room >= 1:
            f_ab, ab_lines = _fit_text(d, ev.about, "regular", 24, 20, max_w, min(room, 4))
            _draw_lines(d, ab_lines, f_ab, x0, y, MUTED, 1.3)

    # подсказка менеджерам
    tip = ev.manager_tip
    if tip:
        by = 568
        panel = Image.new("RGBA", img.size, (0, 0, 0, 0))
        pd = ImageDraw.Draw(panel)
        pd.rounded_rectangle([PAD, by, W - PAD, H - 40], radius=24, fill=(255, 255, 255, 18))
        img = Image.alpha_composite(img.convert("RGBA"), panel).convert("RGB")
        d = ImageDraw.Draw(img, "RGBA")
        d.rounded_rectangle([PAD, by, PAD + 8, H - 40], radius=4, fill=color)
        d.text((PAD + 32, by + 18), "МЕНЕДЖЕРАМ", font=font("bold", 18), fill=color)
        f_tip, tip_lines = _fit_text(d, tip, "medium", 25, 19, W - 2 * PAD - 64, 2)
        _draw_lines(d, tip_lines, f_tip, PAD + 32, by + 46, WHITE, 1.25)
    return _to_png(img)


def _pin(d, x: int, y: int, color: tuple) -> None:
    d.ellipse([x, y, x + 22, y + 22], fill=color)
    d.ellipse([x + 7, y + 7, x + 15, y + 15], fill=BG_TOP)


# ───────────── карточка новости ─────────────

def news_card(item: dict, tz, breaking: bool, photo: bytes | None = None, tz_note: str = "") -> bytes:
    cat = item.get("category") or "other"
    color = NEWS_COLORS.get(cat, NEWS_COLORS["other"])
    cat_name = NEWS_CATEGORIES.get(cat, NEWS_CATEGORIES["other"])[0].upper()

    img = _canvas()
    text_w = W - 2 * PAD
    if photo:
        try:
            ph = Image.open(io.BytesIO(photo))
            ph.draft("RGB", (W * 2, H * 2))
            ph = ImageOps.fit(ph.convert("RGB"), (W, H), method=Image.Resampling.LANCZOS)
            img = ph.convert("RGBA")
            # затемнение слева направо, чтобы текст читался
            mask = Image.linear_gradient("L").rotate(90, expand=True).resize((W, H))
            mask = mask.point(lambda v: int(252 - v * 0.55))
            shade = Image.new("RGBA", (W, H), (*BG_TOP, 0))
            shade.putalpha(mask)
            img = Image.alpha_composite(img, shade)
            bottom = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(v * 0.55))
            shade2 = Image.new("RGBA", (W, H), (*BG_TOP, 0))
            shade2.putalpha(bottom)
            img = Image.alpha_composite(img, shade2).convert("RGB")
            text_w = int(W * 0.62)
        except Exception:  # битая картинка — рисуем без неё
            img = _canvas()
    if not photo or text_w == W - 2 * PAD:
        img = _glow(img, color, (W - 140, 160), 320, 70)
    _brand_stripe(img)
    _header(img)
    d = ImageDraw.Draw(img, "RGBA")

    y = 112
    x = PAD
    if breaking:
        x += _chip(d, x, y, "СРОЧНО", RED, 22) + 12
    x += _chip(d, x, y, cat_name, color, 22) + 12
    st = item.get("state")
    if st and st not in ("BR", "INT"):
        _chip(d, x, y, st, WHITE, 22, filled=False)

    y = 196
    title = item.get("title_ru") or item.get("title") or ""
    f_title, lines = _fit_text(d, title, "display", 64, 38, text_w, 4)
    y = _draw_lines(d, lines, f_title, PAD, y, WHITE, 1.12) + 18
    summary = item.get("summary_ru") or ""
    if summary:
        room = max(1, min(3, (H - 120 - y) // 38))
        f_sum, s_lines = _fit_text(d, summary, "regular", 28, 22, text_w, room)
        _draw_lines(d, s_lines, f_sum, PAD, y, (214, 222, 234), 1.3)

    # подвал
    fy = H - 72
    d.line([PAD, fy - 18, W - PAD, fy - 18], fill=(255, 255, 255, 40), width=1)
    src = item.get("source") or ""
    when = ""
    if item.get("published_at"):
        t = dt.datetime.fromisoformat(item["published_at"]).astimezone(tz)
        when = f"{date_long(t.date(), with_weekday=False)}, {t:%H:%M}" + (f" {tz_note}" if tz_note else "")
    d.text((PAD, fy), f"{src}  ·  {when}" if when else src, font=font("medium", 22), fill=MUTED)
    imp = int(item.get("importance") or 0)
    if imp:
        label = "ВАЖНОСТЬ"
        fl = font("bold", 18)
        bx = W - PAD - 10 * 18
        lx = bx - d.textlength(label, font=fl) - 16
        d.text((lx, fy + 3), label, font=fl, fill=FAINT)
        for i in range(10):
            c = color if i < imp else (255, 255, 255, 50)
            d.rounded_rectangle([bx + i * 18, fy + 4, bx + i * 18 + 12, fy + 20], radius=3, fill=c)
    return _to_png(img)


# ───────────── дайджест ─────────────

def digest_card(items: list[dict], day: dt.date) -> bytes:
    items = items[:6]
    row_h = 92
    h = max(H, 210 + row_h * len(items) + 60)
    img = _canvas(h)
    img = _glow(img, BRAND_GREEN, (W - 160, 120), 300, 55)
    _brand_stripe(img)
    _header(img, [("ДАЙДЖЕСТ", BRAND_YELLOW)])
    d = ImageDraw.Draw(img, "RGBA")
    d.text((PAD, 100), "Главное в Бразилии", font=font("display", 58), fill=WHITE)
    d.text((PAD, 172), f"{WEEKDAYS[day.weekday()]}, {date_long(day, with_weekday=False)}", font=font("medium", 26), fill=MUTED)
    y = 232
    for i, it in enumerate(items, 1):
        color = NEWS_COLORS.get(it.get("category") or "other", NEWS_COLORS["other"])
        d.ellipse([PAD, y + 4, PAD + 48, y + 52], fill=color)
        d.text((PAD + 24, y + 28), str(i), font=font("bold", 24), fill=(15, 23, 42) if _is_light(color) else WHITE, anchor="mm")
        title = it.get("title_ru") or it.get("title") or ""
        f_t, lines = _fit_text(d, title, "semibold", 28, 22, W - 2 * PAD - 80, 2)
        _draw_lines(d, lines, f_t, PAD + 72, y + (12 if len(lines) == 1 else 0), WHITE, 1.2)
        y += row_h
        if i < len(items):
            d.line([PAD + 72, y - 14, W - PAD, y - 14], fill=(255, 255, 255, 28), width=1)
    return _to_png(img)


# ───────────── сводка событий ─────────────

def agenda_card(occs: list[Occurrence], today: dt.date, title: str, subtitle: str = "") -> bytes:
    occs = occs[:7]
    row_h = 96
    h = max(H, 200 + row_h * len(occs) + 50)
    img = _canvas(h)
    img = _glow(img, BRAND_YELLOW, (W - 160, 120), 300, 40)
    _brand_stripe(img)
    _header(img, [("КАЛЕНДАРЬ", BRAND_GREEN)])
    d = ImageDraw.Draw(img, "RGBA")
    d.text((PAD, 100), title, font=font("display", 56), fill=WHITE)
    if subtitle:
        d.text((PAD, 170), subtitle, font=font("medium", 26), fill=MUTED)
    y = 226
    for occ in occs:
        ev = occ.event
        color = EVENT_COLORS.get(ev.category, EVENT_COLORS["holiday"])
        dark = (15, 23, 42) if _is_light(color) else WHITE
        d.rounded_rectangle([PAD, y, PAD + 84, y + 78], radius=16, fill=color)
        d.text((PAD + 42, y + 30), str(occ.start.day), font=font("black", 38), fill=dark, anchor="mm")
        d.text((PAD + 42, y + 62), MONTHS_SHORT[occ.start.month - 1].upper(), font=font("bold", 16), fill=dark, anchor="mm")
        right = countdown(occ, today)
        f_r = font("bold", 22)
        rw = d.textlength(right, font=f_r)
        d.text((W - PAD - rw, y + 8), right, font=f_r, fill=color)
        max_w = int(W - 2 * PAD - 110 - rw - 24)
        f_t, lines = _fit_text(d, ev.name_ru, "semibold", 30, 22, max_w, 1)
        d.text((PAD + 108, y + 4), lines[0], font=f_t, fill=WHITE)
        where = scope_label(ev.scope, short=True)
        if occ.is_multiday():
            where = f"до {occ.end.day} {MONTHS_SHORT[occ.end.month - 1]} · {where}"
        f_w, wl = _fit_text(d, where, "medium", 22, 18, W - 2 * PAD - 110, 1)
        d.text((PAD + 108, y + 46), wl[0], font=f_w, fill=MUTED)
        y += row_h
    return _to_png(img)


def welcome_card() -> bytes:
    img = _canvas()
    img = _glow(img, BRAND_GREEN, (220, 560), 340, 70)
    img = _glow(img, BRAND_YELLOW, (W - 200, 160), 300, 55)
    _brand_stripe(img)
    _header(img)
    d = ImageDraw.Draw(img, "RGBA")
    d.text((PAD, 150), "Инфоповоды Бразилии", font=font("display", 72), fill=WHITE)
    d.text((PAD, 240), "в одном месте — заранее", font=font("display", 72), fill=BRAND_YELLOW)
    rows = [
        ("⚡", "Громкие новости — сразу, как только появились", RED),
        ("📰", "Утренний дайджест главного за сутки", NEWS_COLORS["economy"]),
        ("🗓", "Праздники и матчи — за 5, 3, 1 день и в день события", EVENT_COLORS["festival"]),
        ("📍", "Региональные события — по выбранным штатам", EVENT_COLORS["sport"]),
    ]
    y = 380
    for _, text, color in rows:
        d.rounded_rectangle([PAD, y + 6, PAD + 18, y + 24], radius=5, fill=color)
        d.text((PAD + 40, y), text, font=font("medium", 30), fill=WHITE)
        y += 62
    return _to_png(img)
