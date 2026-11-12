"""Pillow-rendered images: the /files stats card and milestone celebration cards.

Everything is drawn at 2x and downscaled, which gives smooth (anti-aliased)
lines and circles. Rendering is CPU work, so call these via asyncio.to_thread.
"""

import io
import math
import random
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
W, H = 1200, 675  # output size (16:9)
SS = 2  # supersampling factor

YELLOW = (255, 205, 63)
WHITE = (242, 242, 244)
SOFT = (200, 200, 204)
MUTED = (138, 138, 142)
DIM = (92, 92, 98)
GREEN = (110, 231, 150)
RED = (255, 122, 122)
BG_TOP = (24, 24, 27)
BG_BOTTOM = (10, 10, 11)
CHIP_BG = (30, 30, 34)
CHIP_BORDER = (46, 46, 51)
TRACK = (40, 40, 45)
GRID = (36, 36, 40)

CONFETTI = [YELLOW, YELLOW, WHITE, (255, 107, 107), (78, 205, 196), (167, 139, 250), (255, 159, 28)]


# --------------------------------------------------------------------------- #
# Basics
# --------------------------------------------------------------------------- #


def s(v: float) -> int:
    return int(round(v * SS))


@lru_cache(maxsize=64)
def font(weight: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"Inter-{weight}.ttf"), s(size))


def text_width(txt: str, f: ImageFont.FreeTypeFont, tracking: float = 0) -> float:
    return (f.getlength(txt) + s(tracking) * max(len(txt) - 1, 0)) / SS


def fit_font(txt: str, weight: str, start: float, max_width: float) -> ImageFont.FreeTypeFont:
    size = start
    while size > 12 and text_width(txt, font(weight, size)) > max_width:
        size -= 2
    return font(weight, size)


def compact(n: float) -> str:
    sign = "-" if n < 0 else ""
    n = abs(n)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if round(n / div, 2) >= 1:
            return f"{sign}{n / div:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{sign}{n:,.0f}"


def new_canvas(w: int = W, h: int = H) -> Image.Image:
    size = (s(w), s(h))
    mask = Image.linear_gradient("L").resize(size)
    img = Image.composite(Image.new("RGB", size, BG_BOTTOM), Image.new("RGB", size, BG_TOP), mask)
    return img.convert("RGBA")


def glow(img: Image.Image, cx: float, cy: float, rx: float, ry: float, alpha: int, blur: float):
    layer = Image.new("RGBA", img.size, YELLOW + (0,))
    a = Image.new("L", img.size, 0)
    ImageDraw.Draw(a).ellipse([s(cx - rx), s(cy - ry), s(cx + rx), s(cy + ry)], fill=alpha)
    layer.putalpha(a.filter(ImageFilter.GaussianBlur(s(blur))))
    img.alpha_composite(layer)


def draw_text(d, x, y, txt, f, fill, anchor="la"):
    d.text((s(x), s(y)), txt, font=f, fill=fill, anchor=anchor)


def draw_tracked(d, x, y, txt, f, fill, tracking, align="left"):
    if align == "center":
        x -= text_width(txt, f, tracking) / 2
    elif align == "right":
        x -= text_width(txt, f, tracking)
    px = s(x)
    for ch in txt:
        d.text((px, s(y)), ch, font=f, fill=fill)
        px += f.getlength(ch) + s(tracking)


def finish(img: Image.Image, w: int = W, h: int = H) -> bytes:
    out = img.resize((w, h), Image.LANCZOS).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Stats card pieces
# --------------------------------------------------------------------------- #


def chip(d, x: float, y: float, parts: Sequence[Tuple[str, tuple]]) -> float:
    f = font("SemiBold", 20)
    label_w = sum(text_width(t, f) for t, _ in parts)
    h, pad = 40, 16
    w = label_w + 2 * pad
    d.rounded_rectangle([s(x), s(y), s(x + w), s(y + h)], radius=s(12),
                        fill=CHIP_BG, outline=CHIP_BORDER, width=s(1.5))
    cx = x + pad
    for t, color in parts:
        draw_text(d, cx, y + h / 2, t, f, color, anchor="lm")
        cx += text_width(t, f)
    return w


def ring(d, cx: float, cy: float, r: float, width: float, frac: float):
    box = [s(cx - r), s(cy - r), s(cx + r), s(cy + r)]
    d.arc(box, 0, 360, fill=TRACK, width=s(width))
    frac = min(max(frac, 0.0), 1.0)
    if frac <= 0:
        return
    end = -90 + 360 * frac
    d.arc(box, -90, end, fill=YELLOW, width=s(width))
    rr = r - width / 2
    for ang in (-90, end):  # rounded caps
        a = math.radians(ang)
        x, y = cx + rr * math.cos(a), cy + rr * math.sin(a)
        d.ellipse([s(x - width / 2), s(y - width / 2), s(x + width / 2), s(y + width / 2)], fill=YELLOW)


def area_chart(img: Image.Image, points: List[Tuple[float, float]], box: Tuple[float, float, float, float]):
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(img)

    for i in range(4):  # dashed grid
        gy = y0 + (y1 - y0) * i / 3
        gx = x0
        while gx < x1:
            d.line([(s(gx), s(gy)), (s(min(gx + 6, x1)), s(gy))], fill=GRID, width=s(1))
            gx += 12

    ts = [p[0] for p in points]
    cs = [p[1] for p in points]
    tmin, tmax = min(ts), max(ts)
    cmin, cmax = min(cs), max(cs)
    if cmax == cmin:
        cmax = cmin + 1
    pad = (cmax - cmin) * 0.12
    lo, hi = cmin - pad, cmax + pad

    def px(t, c):
        return (x0 + (t - tmin) / (tmax - tmin) * (x1 - x0), y1 - (c - lo) / (hi - lo) * (y1 - y0))

    pts = [px(t, c) for t, c in points]

    # Gradient fill under the line
    mask = Image.new("L", img.size, 0)
    poly = [(s(x), s(y)) for x, y in pts] + [(s(pts[-1][0]), s(y1)), (s(pts[0][0]), s(y1))]
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    grad = ImageOps.invert(Image.linear_gradient("L")).resize((img.width, s(y1 - y0)))
    grad = grad.point(lambda v: v * 85 // 255)
    alpha = Image.new("L", img.size, 0)
    alpha.paste(grad, (0, s(y0)))
    fill = Image.new("RGBA", img.size, YELLOW + (0,))
    fill.putalpha(ImageChops.multiply(alpha, mask))
    img.alpha_composite(fill)

    d = ImageDraw.Draw(img)
    d.line([(s(x), s(y)) for x, y in pts], fill=YELLOW, width=s(3.5), joint="curve")

    ex, ey = pts[-1]
    glow(img, ex, ey, 16, 16, 150, 8)
    d = ImageDraw.Draw(img)
    d.ellipse([s(ex - 7), s(ey - 7), s(ex + 7), s(ey + 7)], fill=YELLOW)
    d.ellipse([s(ex - 3), s(ey - 3), s(ex + 3), s(ey + 3)], fill=BG_BOTTOM)


# --------------------------------------------------------------------------- #
# Public: /files stats card
# --------------------------------------------------------------------------- #


def render_stats_card(
    count: int,
    points: List[Tuple[float, float]],
    updated_at: datetime,
    per_day: Optional[float] = None,
    week_diff: Optional[int] = None,
    trend_pct: Optional[float] = None,
    next_milestone: Optional[int] = None,
    prev_milestone: int = 0,
) -> bytes:
    img = new_canvas()
    glow(img, 120, 40, 420, 220, 34, 120)
    d = ImageDraw.Draw(img)

    # Header
    d.ellipse([s(60), s(51), s(72), s(63)], fill=YELLOW)
    draw_tracked(d, 84, 47, "ENTE  ·  PROTECTED FILES", font("SemiBold", 17), MUTED, 2.2)
    stamp = f"{updated_at:%d %b %Y · %H:%M} UTC"
    draw_text(d, 1140, 47, stamp, font("Regular", 16), DIM, anchor="ra")

    # Big number
    big = fit_font(f"{count:,}", "ExtraBold", 98, 780)
    draw_text(d, 56, 84, f"{count:,}", big, YELLOW)
    draw_text(d, 60, 198, "files, end-to-end encrypted", font("Medium", 26), SOFT)

    # Chips
    x = 60
    chips = []
    if per_day is not None:
        chips.append([(f"+{compact(per_day)}", WHITE), (" / day", MUTED)])
    if week_diff is not None:
        chips.append([(f"{'+' if week_diff >= 0 else ''}{compact(week_diff)}", WHITE), (" this week", MUTED)])
    if trend_pct is not None:
        up = trend_pct >= 0
        chips.append([(f"{'↑' if up else '↓'} {abs(trend_pct):.1f}%", GREEN if up else RED),
                      (" vs last week", MUTED)])
    for parts in chips:
        x += chip(d, x, 248, parts) + 12

    # Milestone ring
    if next_milestone:
        frac = (count - prev_milestone) / (next_milestone - prev_milestone)
        ring(d, 1012, 192, 104, 17, frac)
        draw_text(d, 1012, 184, f"{frac * 100:.0f}%", font("Bold", 42), WHITE, anchor="mm")
        draw_text(d, 1012, 222, f"to {compact(next_milestone)}", font("Medium", 19), MUTED, anchor="mm")

    # Chart
    box = (60, 350, 1140, 598)
    draw_tracked(d, 60, 318, "LAST 30 DAYS", font("SemiBold", 14), DIM, 2.2)
    if len(points) >= 2 and points[-1][0] - points[0][0] >= 3 * 3600:
        area_chart(img, points, box)
        d = ImageDraw.Draw(img)
        start = datetime.fromtimestamp(points[0][0], tz=updated_at.tzinfo)
        draw_text(d, 60, 616, f"{start.day} {start:%b}", font("Medium", 16), DIM)
        draw_text(d, 1140, 616, "now", font("Medium", 16), DIM, anchor="ra")
    else:
        d.rounded_rectangle([s(box[0]), s(box[1]), s(box[2]), s(box[3])], radius=s(16),
                            outline=GRID, width=s(1.5))
        draw_text(d, 600, (box[1] + box[3]) / 2, "Growth chart appears once a few hours of data are collected",
                  font("Medium", 20), DIM, anchor="mm")

    return finish(img)


# --------------------------------------------------------------------------- #
# Public: milestone celebration card
# --------------------------------------------------------------------------- #


def confetti(img: Image.Image, seed: int, pieces: int = 150):
    rng = random.Random(seed)
    placed = 0
    while placed < pieces:
        x, y = rng.uniform(10, W - 10), rng.uniform(10, H - 10)
        if (170 < x < 1030 and 120 < y < 560) or (80 < x < 1120 and 210 < y < 380) \
                or (380 < x < 820 and 596 < y < 650):  # keep the text clean
            continue
        placed += 1
        color = rng.choice(CONFETTI)
        alpha = rng.randint(150, 255)
        if rng.random() < 0.3:
            r = rng.uniform(2.5, 5)
            ImageDraw.Draw(img).ellipse([s(x - r), s(y - r), s(x + r), s(y + r)], fill=color + (alpha,))
            continue
        w, h = rng.uniform(9, 20), rng.uniform(4, 8)
        piece = Image.new("RGBA", (s(w) + 2, s(h) + 2), (0, 0, 0, 0))
        ImageDraw.Draw(piece).rounded_rectangle([1, 1, s(w), s(h)], radius=s(1.5), fill=color + (alpha,))
        piece = piece.rotate(rng.uniform(0, 180), expand=True, resample=Image.BICUBIC)
        img.alpha_composite(piece, (s(x) - piece.width // 2, s(y) - piece.height // 2))


def padlock(d, cx: float, top: float):
    d.arc([s(cx - 15), s(top), s(cx + 15), s(top + 30)], 180, 360, fill=YELLOW, width=s(6))
    for sx in (cx - 12, cx + 12):
        d.line([(s(sx), s(top + 15)), (s(sx), s(top + 30))], fill=YELLOW, width=s(6))
    d.rounded_rectangle([s(cx - 23), s(top + 28), s(cx + 23), s(top + 64)], radius=s(8), fill=YELLOW)
    d.ellipse([s(cx - 5), s(top + 38), s(cx + 5), s(top + 48)], fill=BG_BOTTOM)
    d.rounded_rectangle([s(cx - 2.5), s(top + 43), s(cx + 2.5), s(top + 55)], radius=s(2), fill=BG_BOTTOM)


def render_milestone_card(
    milestone: int,
    reached_at: datetime,
    since_previous: Optional[str] = None,
) -> bytes:
    img = new_canvas()
    glow(img, 600, 300, 460, 230, 46, 140)
    confetti(img, seed=milestone)
    d = ImageDraw.Draw(img)

    padlock(d, 600, 70)
    draw_tracked(d, 600, 162, "MILESTONE REACHED", font("SemiBold", 19), MUTED, 4.5, align="center")

    if milestone >= 1_000_000_000 and milestone % 1_000_000_000 == 0:
        headline = f"{milestone // 1_000_000_000} BILLION"
    else:
        headline = f"{milestone:,}"
    draw_text(d, 600, 292, headline, fit_font(headline, "ExtraBold", 150, 980), YELLOW, anchor="mm")
    draw_text(d, 600, 398, "files protected by Ente", font("Medium", 36), WHITE, anchor="mm")

    d.rounded_rectangle([s(570), s(446), s(630), s(450)], radius=s(2), fill=YELLOW)
    meta = f"{reached_at.day} {reached_at:%B %Y}"
    if since_previous:
        meta += f"  ·  {since_previous}"
    draw_text(d, 600, 492, meta, font("Medium", 24), MUTED, anchor="mm")

    draw_tracked(d, 600, 618, "END-TO-END ENCRYPTED  ·  DUCKY", font("SemiBold", 14), DIM, 2.5, align="center")
    return finish(img)


# --------------------------------------------------------------------------- #
# Public: activity heatmap
# --------------------------------------------------------------------------- #

HEAT_EMPTY = (36, 36, 40)


def blend(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


HEAT_LEVELS = [HEAT_EMPTY] + [blend(HEAT_EMPTY, YELLOW, t) for t in (0.3, 0.52, 0.76, 1.0)]


def heatmap_start(today: date) -> date:
    """Monday 52 weeks before this week's Monday -> 53 week columns."""
    return today - timedelta(days=today.weekday()) - timedelta(weeks=52)


def avatar_circle(img: Image.Image, data: bytes, cx: float, cy: float, r: float):
    try:
        av = Image.open(io.BytesIO(data)).convert("RGBA").resize((s(2 * r), s(2 * r)), Image.LANCZOS)
    except Exception:
        return
    mask = Image.new("L", av.size, 0)
    ImageDraw.Draw(mask).ellipse([0, 0, av.width - 1, av.height - 1], fill=255)
    img.paste(av, (s(cx - r), s(cy - r)), mask)
    ImageDraw.Draw(img).ellipse([s(cx - r - 6), s(cy - r - 6), s(cx + r + 6), s(cy + r + 6)],
                                outline=YELLOW, width=s(4))


def render_heatmap_card(
    counts: Dict[date, int],
    today: date,
    label: str,
    headline: str,
    subline: str,
    chips: Sequence[Sequence[Tuple[str, tuple]]] = (),
    avatar: Optional[bytes] = None,
) -> bytes:
    w, h = 1200, 560
    img = new_canvas(w, h)
    glow(img, 120, 40, 420, 220, 30, 120)
    d = ImageDraw.Draw(img)

    d.ellipse([s(60), s(51), s(72), s(63)], fill=YELLOW)
    draw_tracked(d, 84, 47, label.upper(), font("SemiBold", 17), MUTED, 2.2)
    draw_text(d, 56, 80, headline, fit_font(headline, "ExtraBold", 84, 820), YELLOW)
    draw_text(d, 60, 180, subline, font("Medium", 26), SOFT)
    x = 60
    for parts in chips:
        x += chip(d, x, 230, parts) + 12
    if avatar:
        avatar_circle(img, avatar, 1062, 150, 74)
        d = ImageDraw.Draw(img)

    # Grid
    start = heatmap_start(today)
    cols = 53
    gx0, gx1, gy0 = 100, 1140, 336
    pitch = (gx1 - gx0) / cols
    cell = pitch * 0.8
    nonzero = sorted(v for v in counts.values() if v > 0)

    def q(p):
        return nonzero[min(int(len(nonzero) * p), len(nonzero) - 1)] if nonzero else 0

    q1, q2, q3 = q(0.25), q(0.5), q(0.75)

    def level(v):
        if v <= 0:
            return 0
        return 1 if v <= q1 else 2 if v <= q2 else 3 if v <= q3 else 4

    busiest = max(counts.items(), key=lambda kv: kv[1])[0] if nonzero else None
    last_label_col = -10
    for col in range(cols):
        week_start = start + timedelta(weeks=col)
        for row in range(7):
            day = week_start + timedelta(days=row)
            if day > today:
                continue
            x0, y0 = gx0 + col * pitch, gy0 + row * pitch
            box = [s(x0), s(y0), s(x0 + cell), s(y0 + cell)]
            d.rounded_rectangle(box, radius=s(3.5), fill=HEAT_LEVELS[level(counts.get(day, 0))])
            if day == busiest:
                d.rounded_rectangle([b - s(2.5) if i < 2 else b + s(2.5) for i, b in enumerate(box)],
                                    radius=s(5), outline=WHITE, width=s(2))
            if day.day == 1 or (col == 0 and row == 0):
                if col - last_label_col >= 3:
                    draw_text(d, gx0 + col * pitch, gy0 - 28, f"{day:%b}", font("Medium", 15), DIM)
                    last_label_col = col
    for row, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
        draw_text(d, 60, gy0 + row * pitch + cell / 2, name, font("Medium", 15), DIM, anchor="lm")

    # Legend + footer
    ly = gy0 + 7 * pitch + 26
    lx = gx1 - 5 * pitch - 46
    draw_text(d, lx - 10, ly + cell / 2, "Less", font("Medium", 15), DIM, anchor="rm")
    for i, color in enumerate(HEAT_LEVELS):
        d.rounded_rectangle([s(lx + i * pitch), s(ly), s(lx + i * pitch + cell), s(ly + cell)],
                            radius=s(3.5), fill=color)
    draw_text(d, lx + 5 * pitch + 6, ly + cell / 2, "More", font("Medium", 15), DIM, anchor="lm")
    note = "Days in UTC"
    if busiest:
        note += f"  ·  outlined: busiest day, {busiest.day} {busiest:%b %Y}"
    draw_text(d, 60, ly + cell / 2, note, font("Medium", 15), DIM, anchor="lm")
    return finish(img, w, h)
