"""Ducky Wrapped: portrait story slides (1080x1350) rendered with Pillow.

render_user_slides() / render_server_slides() return a list of PNG bytes, the
last one being the shareable summary card. CPU-bound: call via asyncio.to_thread.
"""

import io
import math
import random
from calendar import month_abbr, month_name
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter

from utils.cards import (
    HEAT_LEVELS,
    WHITE,
    YELLOW,
    blend,
    compact,
    ducky_image,
    draw_text,
    draw_tracked,
    fit_font,
    font,
    s,
    text_width,
)

SW, SH = 1080, 1350
INK = (20, 20, 22)
BEAK = (255, 138, 40)
DUCK_WING = (236, 174, 28)
HEART = (125, 214, 105)
GOLD = (255, 196, 0)
SILVER = (205, 212, 222)
BRONZE = (214, 140, 82)
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #


@dataclass
class UserWrapped:
    year: int
    name: str
    avatar: Optional[bytes]
    daily: Dict[date, int]
    hours: List[int]  # 24 buckets, UTC
    rank: Optional[int] = None
    members: int = 0
    joined: Optional[date] = None
    starboard_posts: int = 0
    hearts: int = 0
    best_post: Optional[Tuple[int, date]] = None  # (hearts, date)
    questions_solved: int = 0

    @property
    def total(self) -> int:
        return sum(self.daily.values())

    @property
    def months(self) -> List[int]:
        out = [0] * 12
        for d, n in self.daily.items():
            out[d.month - 1] += n
        return out

    @property
    def active_days(self) -> int:
        return sum(1 for n in self.daily.values() if n > 0)

    @property
    def beat_pct(self) -> Optional[float]:
        if not self.rank or self.members < 2:
            return None
        return (self.members - self.rank) / self.members * 100

    @property
    def weekend_share(self) -> float:
        total = self.total
        return sum(n for d, n in self.daily.items() if d.weekday() >= 5) / total if total else 0

    @property
    def busiest_weekday(self) -> Optional[int]:
        sums = [0] * 7
        for d, n in self.daily.items():
            sums[d.weekday()] += n
        return max(range(7), key=lambda i: sums[i]) if any(sums) else None

    @property
    def longest_streak(self) -> Tuple[int, Optional[date], Optional[date]]:
        best, best_end, run = 0, None, 0
        day, end = date(self.year, 1, 1), date(self.year, 12, 31)
        while day <= end:
            run = run + 1 if self.daily.get(day, 0) > 0 else 0
            if run > best:
                best, best_end = run, day
            day += timedelta(days=1)
        if not best:
            return 0, None, None
        return best, best_end - timedelta(days=best - 1), best_end


@dataclass
class ServerWrapped:
    year: int
    name: str
    icon: Optional[bytes]
    daily: Dict[date, int]
    hours: List[int]
    active_members: int = 0
    top: List[Tuple[str, Optional[bytes], int]] = field(default_factory=list)  # name, avatar, count
    new_members: int = 0
    solved_threads: int = 0
    starboard_posts: int = 0
    hearts: int = 0
    github_stars: Optional[int] = None
    files_protected: Optional[int] = None

    @property
    def total(self) -> int:
        return sum(self.daily.values())

    @property
    def months(self) -> List[int]:
        out = [0] * 12
        for d, n in self.daily.items():
            out[d.month - 1] += n
        return out


# --------------------------------------------------------------------------- #
# Personas
# --------------------------------------------------------------------------- #

PERSONAS = {
    "legend": ("The Legend", "legend",
               "Basically part of the furniture. The pond just wouldn't be the same without you."),
    "hearts": ("The Heart Collector", "hearts",
               "When you post, the green hearts follow. People love what you share."),
    "streak": ("The Streak Master", "flame",
               "Day after day after day. Consistency is your superpower."),
    "chatter": ("The Chatterbox", "bubbles",
                "The conversation starts (and very often ends) with you."),
    "weekend": ("The Weekend Duck", "shades",
                "Weekdays are for work. Weekends are for the Ente Discord."),
    "paddler": ("The Steady Paddler", "ripples",
                "Calm on the surface, always paddling underneath. Always around."),
    "duckling": ("The Fresh Duckling", "egg",
                 "New to the pond this year, and already making waves."),
    "quiet": ("The Quiet Quacker", "thought",
              "Few words, well chosen. Quality over quantity, always."),
}


def choose_persona(w: UserWrapped) -> Tuple[str, str, str]:
    beat = w.beat_pct or 0
    longest = w.longest_streak[0]
    if beat >= 99 and w.total >= 200:
        key = "legend"
    elif w.hearts >= 20 or w.starboard_posts >= 5:
        key = "hearts"
    elif longest >= 21:
        key = "streak"
    elif beat >= 90:
        key = "chatter"
    elif w.weekend_share >= 0.4 and w.total >= 30:
        key = "weekend"
    elif w.active_days >= 100:
        key = "paddler"
    elif w.joined and w.joined.year == w.year:
        key = "duckling"
    else:
        key = "quiet"
    return PERSONAS[key]


# --------------------------------------------------------------------------- #
# Drawing helpers
# --------------------------------------------------------------------------- #


def canvas(top: tuple, bottom: tuple) -> Image.Image:
    size = (s(SW), s(SH))
    mask = Image.linear_gradient("L").resize(size)
    return Image.composite(Image.new("RGB", size, bottom), Image.new("RGB", size, top), mask).convert("RGBA")


def blob(img: Image.Image, cx, cy, r, color, alpha, blur=140):
    layer = Image.new("RGBA", img.size, color + (0,))
    a = Image.new("L", img.size, 0)
    ImageDraw.Draw(a).ellipse([s(cx - r), s(cy - r), s(cx + r), s(cy + r)], fill=alpha)
    layer.putalpha(a.filter(ImageFilter.GaussianBlur(s(blur))))
    img.alpha_composite(layer)


def overlay(img: Image.Image, fn):
    """Draw translucent shapes on a separate layer, then blend them in."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fn(ImageDraw.Draw(layer))
    img.alpha_composite(layer)


def wrap(txt: str, f, max_width: float) -> List[str]:
    lines, cur = [], ""
    for word in txt.split():
        trial = f"{cur} {word}".strip()
        if text_width(trial, f) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def draw_lines(d, x, y, lines, f, fill, leading, anchor="la"):
    for i, line in enumerate(lines):
        draw_text(d, x, y + i * leading, line, f, fill, anchor=anchor)


def story_frame(img: Image.Image, page: int, total: int, year: int, ink: tuple, label="ENTE COMMUNITY"):
    """Story progress bar on top, small branding at the bottom."""
    gap, x0, x1 = 8, 48, SW - 48
    seg = (x1 - x0 - gap * (total - 1)) / total

    def bars(d):
        for i in range(total):
            a = 235 if i <= page else 70
            xa = x0 + i * (seg + gap)
            d.rounded_rectangle([s(xa), s(34), s(xa + seg), s(41)], radius=s(4), fill=ink + (a,))
    overlay(img, bars)
    d = ImageDraw.Draw(img)
    draw_tracked(d, 64, 72, f"DUCKY WRAPPED {year}", font("Bold", 20), ink, 3)
    draw_tracked(d, 64, SH - 70, label, font("SemiBold", 17), blend(ink, (128, 128, 128), 0.45), 3)


def finish_story(img: Image.Image) -> bytes:
    out = img.resize((SW, SH), Image.LANCZOS).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def avatar(img: Image.Image, data: Optional[bytes], cx, cy, r, ring=YELLOW, fallback="?"):
    d = ImageDraw.Draw(img)
    d.ellipse([s(cx - r - 9), s(cy - r - 9), s(cx + r + 9), s(cy + r + 9)], fill=ring)
    try:
        av = Image.open(io.BytesIO(data)).convert("RGBA").resize((s(2 * r), s(2 * r)), Image.LANCZOS)
        mask = Image.new("L", av.size, 0)
        ImageDraw.Draw(mask).ellipse([0, 0, av.width - 1, av.height - 1], fill=255)
        img.paste(av, (s(cx - r), s(cy - r)), mask)
    except Exception:
        d.ellipse([s(cx - r), s(cy - r), s(cx + r), s(cy + r)], fill=(48, 48, 58))
        draw_text(d, cx, cy, (fallback[:1] or "?").upper(), font("ExtraBold", r * 0.9), WHITE, anchor="mm")


def heart(d, cx, cy, size, fill):
    r = size / 4
    d.ellipse([s(cx - 2 * r), s(cy - r), s(cx), s(cy + r)], fill=fill)
    d.ellipse([s(cx), s(cy - r), s(cx + 2 * r), s(cy + r)], fill=fill)
    d.polygon([(s(cx - 1.93 * r), s(cy + 0.45 * r)), (s(cx + 1.93 * r), s(cy + 0.45 * r)),
               (s(cx), s(cy + 2.3 * r))], fill=fill)


def star(d, cx, cy, r, fill):
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * 0.45
        a = math.radians(-90 + i * 36)
        pts.append((s(cx + rr * math.cos(a)), s(cy + rr * math.sin(a))))
    d.polygon(pts, fill=fill)


def flame(d, cx, cy, h, colors=((255, 86, 48), (255, 156, 40), (255, 226, 120))):
    for i, c in enumerate(colors):
        k = 1 - i * 0.28
        hh, ww = h * k, h * 0.55 * k
        base = cy - i * h * 0.05
        pts = []
        for t in range(0, 181, 12):  # round bottom
            a = math.radians(t)
            pts.append((cx + ww / 2 * math.cos(a), base + ww / 2 * math.sin(a) * 0.9))
        pts += [(cx - ww / 2, base - hh * 0.2), (cx - ww * 0.15, base - hh * 0.65),
                (cx + ww * 0.05, base - hh), (cx + ww * 0.3, base - hh * 0.55), (cx + ww / 2, base - hh * 0.15)]
        d.polygon([(s(x), s(y)) for x, y in pts], fill=c)


# --------------------------------------------------------------------------- #
# Ducky (the real mascot artwork) + duck-type props
# --------------------------------------------------------------------------- #

DUCKY_ASPECT = 1400 / 1291  # width / height of assets/ducky.png


def draw_ducky(img: Image.Image, cx: float, bottom: float, height: float, prop: Optional[str] = None):
    """Paste Ducky centred on cx, feet on `bottom`, plus an optional duck-type prop.

    Feature positions below are fractions of the artwork's box (eyes, beak, chest...).
    """
    w = height * DUCKY_ASPECT
    x0, y0 = cx - w / 2, bottom - height

    def F(fx, fy):  # fraction of the artwork box -> design units
        return x0 + fx * w, y0 + fy * height

    u = height / 560  # prop scale; props were tuned at height 560

    if prop == "flame":  # behind Ducky
        d = ImageDraw.Draw(img)
        lx, ly = F(0.06, 0.92)
        flame(d, lx, ly, 260 * u)
        rx, ry = F(0.98, 0.92)
        flame(d, rx, ry, 190 * u)
    if prop == "legend":
        blob(img, *F(0.55, 0.55), 330 * u, GOLD, 120, 60 * u)

    im = ducky_image(s(height))
    img.alpha_composite(im, (s(x0), s(y0)))
    d = ImageDraw.Draw(img)

    if prop == "legend":  # gold medal on the chest + sparkles
        mx, my = F(0.60, 0.74)
        d.polygon([(s(mx - 30 * u), s(my - 62 * u)), (s(mx - 8 * u), s(my - 62 * u)), (s(mx + 4 * u), s(my - 10 * u)),
                   (s(mx - 18 * u), s(my - 10 * u))], fill=(66, 133, 244), outline=INK, width=s(3 * u))
        d.polygon([(s(mx + 30 * u), s(my - 62 * u)), (s(mx + 8 * u), s(my - 62 * u)), (s(mx - 4 * u), s(my - 10 * u)),
                   (s(mx + 18 * u), s(my - 10 * u))], fill=(230, 70, 90), outline=INK, width=s(3 * u))
        d.ellipse([s(mx - 40 * u), s(my - 22 * u), s(mx + 40 * u), s(my + 58 * u)], fill=GOLD, outline=INK, width=s(5 * u))
        star(d, mx, my + 18 * u, 22 * u, (255, 236, 150))
        for fx, fy, r in ((0.06, 0.30, 26), (0.97, 0.18, 20), (0.92, 0.80, 16), (0.02, 0.62, 14), (0.70, 0.02, 14)):
            star(d, *F(fx, fy), r * u, (255, 226, 110))
    elif prop == "hearts":
        for fx, fy, size in ((0.04, 0.40, 70), (0.96, 0.10, 56), (1.02, 0.66, 44), (0.10, 0.78, 36), (0.76, 0.02, 34)):
            heart(d, *F(fx, fy), size * u, HEART)
    elif prop == "bubbles":
        for bx, by, bw, bh in ((0.80, 0.05, 140, 88), (0.98, 0.26, 96, 62)):
            x, y = F(bx, by)
            d.rounded_rectangle([s(x), s(y), s(x + bw * u), s(y + bh * u)], radius=s(28 * u), fill=WHITE,
                                outline=INK, width=s(5 * u))
            for i in range(3):
                dx = x + bw * u / 2 + (i - 1) * bw * u * 0.22
                rr = 7 * u
                d.ellipse([s(dx - rr), s(y + bh * u / 2 - rr), s(dx + rr), s(y + bh * u / 2 + rr)], fill=INK)
    elif prop == "shades":  # sunglasses over Ducky's happy eyes
        for fx in (0.493, 0.674):
            x, y = F(fx, 0.47)
            d.rounded_rectangle([s(x - 52 * u), s(y - 32 * u), s(x + 52 * u), s(y + 30 * u)], radius=s(18 * u), fill=INK)
            d.line([(s(x - 30 * u), s(y - 16 * u)), (s(x - 8 * u), s(y - 16 * u))], fill=(120, 120, 130), width=s(5 * u))
        (ax, ay), (bx, by) = F(0.493, 0.47), F(0.674, 0.462)
        d.line([(s(ax + 50 * u), s(ay - 12 * u)), (s(bx - 50 * u), s(by - 8 * u))], fill=INK, width=s(9 * u))
    elif prop == "ripples":
        def waves(dd):
            cx_, cy_ = F(0.52, 0.985)
            for i, a in enumerate((230, 180, 130, 80)):
                k = 1 + i * 0.22
                dd.ellipse([s(cx_ - 300 * u * k), s(cy_ - 16 * u * k), s(cx_ + 300 * u * k), s(cy_ + 20 * u * k)],
                           outline=(140, 210, 255, a), width=s(6 * u))
            for fx, fy, r in ((0.02, 0.86, 12), (-0.03, 0.78, 8), (1.02, 0.84, 11), (1.06, 0.76, 7), (0.08, 0.72, 6)):
                x, y = F(fx, fy)
                dd.ellipse([s(x - r * u), s(y - r * 1.3 * u), s(x + r * u), s(y + r * u)], fill=(160, 220, 255, 235))
        overlay(img, waves)
    elif prop == "egg":  # cracked eggshell around the bottom
        pts = []
        for i in range(13):
            fx = 0.12 + i * 0.065
            pts.append(F(fx, 0.79 if i % 2 == 0 else 0.85))
        for t in range(0, 181, 15):  # rounded bottom of the shell
            a = math.radians(t)
            pts.append(F(0.51 + 0.40 * math.cos(a), 0.86 + 0.15 * math.sin(a)))
        d.polygon([(s(x), s(y)) for x, y in pts], fill=(252, 248, 238), outline=INK, width=s(6 * u))
    elif prop == "thought":
        x, y = F(0.86, 0.10)
        for k, (dx, dy, r) in enumerate(((-40, 70, 9), (-14, 40, 14))):
            d.ellipse([s(x + dx * u - r * u), s(y + dy * u - r * u), s(x + dx * u + r * u), s(y + dy * u + r * u)],
                      fill=WHITE, outline=INK, width=s(4 * u))
        d.ellipse([s(x), s(y - 70 * u), s(x + 150 * u), s(y + 10 * u)], fill=WHITE, outline=INK, width=s(5 * u))
        for i in range(3):
            dx = x + 75 * u + (i - 1) * 32 * u
            d.ellipse([s(dx - 8 * u), s(y - 38 * u), s(dx + 8 * u), s(y - 22 * u)], fill=INK)


# --------------------------------------------------------------------------- #
# Shared slide parts
# --------------------------------------------------------------------------- #


def month_bars(img, months: List[int], x0, x1, y_top, y_bottom, base_color, best_color, label_color):
    d = ImageDraw.Draw(img)
    best = max(range(12), key=lambda i: months[i])
    peak = max(months) or 1
    pitch = (x1 - x0) / 12
    bw = pitch * 0.68
    for i, n in enumerate(months):
        h = max((y_bottom - y_top) * n / peak, 6)
        bx = x0 + i * pitch + (pitch - bw) / 2
        color = best_color if i == best else base_color
        d.rounded_rectangle([s(bx), s(y_bottom - h), s(bx + bw), s(y_bottom)], radius=s(12), fill=color)
        draw_text(d, bx + bw / 2, y_bottom + 34, month_abbr[i + 1][0], font("SemiBold", 28),
                  best_color if i == best else label_color, anchor="mm")
        if i == best:
            draw_text(d, bx + bw / 2, y_bottom - h - 30, compact(n), font("Bold", 30), best_color, anchor="mm")
    return best


def clock(img, hours: List[int], cx, cy, inner, length, base, peak_color, label_color):
    d = ImageDraw.Draw(img)
    peak_h = max(range(24), key=lambda h: hours[h])
    top = max(hours) or 1
    for h in range(24):
        a = math.radians(-90 + h * 15)
        r1 = inner + 10 + (length - 10) * hours[h] / top
        x0, y0 = cx + inner * math.cos(a), cy + inner * math.sin(a)
        x1, y1 = cx + r1 * math.cos(a), cy + r1 * math.sin(a)
        color = peak_color if h == peak_h else blend(base, peak_color, 0.15 + 0.45 * hours[h] / top)
        w = 24
        d.line([(s(x0), s(y0)), (s(x1), s(y1))], fill=color, width=s(w))
        for x, y in ((x0, y0), (x1, y1)):
            d.ellipse([s(x - w / 2), s(y - w / 2), s(x + w / 2), s(y + w / 2)], fill=color)
    for h in (0, 6, 12, 18):
        a = math.radians(-90 + h * 15)
        r = inner + length + 40
        draw_text(d, cx + r * math.cos(a), cy + r * math.sin(a), f"{h:02d}", font("SemiBold", 26),
                  label_color, anchor="mm")
    return peak_h


def year_heatmap(img, daily: Dict[date, int], year: int, x0, x1, y0, highlight=(), months_labels=True,
                 label_color=(120, 120, 128)):
    d = ImageDraw.Draw(img)
    first, last = date(year, 1, 1), date(year, 12, 31)
    start = first - timedelta(days=first.weekday())
    cols = (last - start).days // 7 + 1
    pitch = (x1 - x0) / cols
    cell = pitch * 0.78
    vals = sorted(v for v in daily.values() if v > 0)

    def q(p):
        return vals[min(int(len(vals) * p), len(vals) - 1)] if vals else 0

    q1, q2, q3 = q(0.25), q(0.5), q(0.75)
    hl = set(highlight)
    day = first
    while day <= last:
        col, row = (day - start).days // 7, day.weekday()
        v = daily.get(day, 0)
        lvl = 0 if v <= 0 else 1 if v <= q1 else 2 if v <= q2 else 3 if v <= q3 else 4
        x, y = x0 + col * pitch, y0 + row * pitch
        d.rounded_rectangle([s(x), s(y), s(x + cell), s(y + cell)], radius=s(cell * 0.22), fill=HEAT_LEVELS[lvl])
        if day in hl:
            d.rounded_rectangle([s(x - 2), s(y - 2), s(x + cell + 2), s(y + cell + 2)],
                                radius=s(cell * 0.3), outline=WHITE, width=s(2))
        if months_labels and day.day == 1:
            draw_text(d, x, y0 - 30, month_abbr[day.month], font("Medium", 20), label_color)
        day += timedelta(days=1)
    return y0 + 7 * pitch


def fmt_range(a: date, b: date) -> str:
    if a == b:
        return f"{a.day} {month_name[a.month]}"
    if a.month == b.month:
        return f"{a.day} – {b.day} {month_name[a.month]}"
    return f"{a.day} {month_abbr[a.month]} – {b.day} {month_abbr[b.month]}"


def stat_tile(d, x, y, w, h, label, value, value_color=YELLOW, bg=(32, 32, 37)):
    d.rounded_rectangle([s(x), s(y), s(x + w), s(y + h)], radius=s(30), fill=bg)
    draw_tracked(d, x + 34, y + 30, label, font("SemiBold", 19), (150, 150, 158), 2.4)
    draw_text(d, x + 32, y + h - 26, value, fit_font(value, "ExtraBold", 74, w - 64), value_color, anchor="ls")


# --------------------------------------------------------------------------- #
# Personal slides
# --------------------------------------------------------------------------- #


def slide_intro(title: str, subtitle: str, image: Optional[bytes], year: int, page: int, total: int) -> bytes:
    img = canvas((30, 12, 64), (8, 12, 34))
    blob(img, 920, 230, 380, YELLOW, 95)
    blob(img, 120, 1120, 420, (255, 80, 160), 80)
    blob(img, 980, 1180, 300, (60, 200, 220), 60)

    def outline_year(d):
        d.text((s(SW / 2), s(470)), str(year), font=font("ExtraBold", 400), anchor="mm",
                fill=(0, 0, 0, 0), stroke_width=s(3), stroke_fill=YELLOW + (90,))
    overlay(img, outline_year)
    avatar(img, image, SW / 2, 470, 150, fallback=title)
    d = ImageDraw.Draw(img)
    draw_text(d, SW / 2, 735, title, fit_font(title, "ExtraBold", 96, 920), WHITE, anchor="mm")
    draw_tracked(d, SW / 2, 800, subtitle.upper(), font("SemiBold", 24), (205, 200, 230), 4, align="center")
    draw_ducky(img, SW / 2, 1190, 330)
    d = ImageDraw.Draw(img)
    draw_text(d, SW / 2, 1222, "Grab some bread. Let's look back.", font("Medium", 30), (205, 200, 230), anchor="mm")
    story_frame(img, page, total, year, WHITE)
    return finish_story(img)


def slide_messages(w: UserWrapped, page: int, total: int) -> bytes:
    img = canvas((255, 216, 92), (255, 186, 36))
    blob(img, 120, 160, 360, WHITE, 90)
    blob(img, 980, 1220, 420, (255, 128, 40), 90)
    d = ImageDraw.Draw(img)
    draw_tracked(d, 80, 220, "YOU SENT", font("Bold", 34), INK, 5)
    draw_text(d, 70, 262, f"{w.total:,}", fit_font(f"{w.total:,}", "ExtraBold", 270, 930), INK)
    draw_text(d, 80, 560, "messages", font("ExtraBold", 84), INK)

    y = 700
    if w.beat_pct is not None:
        line = f"More than {w.beat_pct:.0f}% of the community" if w.beat_pct >= 1 else "Every message counts"
        f = font("Bold", 38)
        tw = text_width(line, f)
        d.rounded_rectangle([s(80), s(y), s(80 + tw + 56), s(y + 74)], radius=s(37), fill=INK)
        draw_text(d, 108, y + 37, line, f, YELLOW, anchor="lm")
        draw_text(d, 82, y + 120, f"#{w.rank:,} of {w.members:,} members who spoke up this year",
                  font("Medium", 32), blend(INK, (255, 200, 60), 0.25))
        y += 190
    hours_per = 8760 / w.total if w.total else 0
    if w.total:
        every = f"{hours_per:.1f} hours" if hours_per < 48 else f"{hours_per / 24:.0f} days"
        metres = w.total * 0.15
        dist = f"{metres / 1000:.1f} km" if metres >= 1000 else f"{metres:.0f} metres"
        lines = wrap(f"That's one message every {every}. If each one were a duck footprint, "
                     f"you'd have waddled {dist}.", font("Medium", 36), 560)
        draw_lines(d, 82, y + 60, lines, font("Medium", 36), INK, 50)
    draw_ducky(img, 850, 1250, 330)
    d = ImageDraw.Draw(img)
    for i in range(4):  # footprints
        fx, fy = 210 + i * 95, 1225 - (i % 2) * 26
        for tx, ty in ((-9, -10), (0, -14), (9, -10)):
            d.ellipse([s(fx + tx - 6), s(fy + ty - 9), s(fx + tx + 6), s(fy + ty + 5)],
                      fill=blend(INK, YELLOW, 0.55))
    story_frame(img, page, total, w.year, INK)
    return finish_story(img)


def slide_months(months: List[int], year: int, page: int, total: int, who="your", weekday: Optional[int] = None) -> bytes:
    img = canvas((14, 50, 52), (5, 22, 24))
    blob(img, 900, 260, 380, (40, 170, 160), 80)
    d = ImageDraw.Draw(img)
    best = max(range(12), key=lambda i: months[i])
    draw_tracked(d, 80, 200, "MONTH BY MONTH", font("Bold", 26), (150, 220, 210), 5)
    head = f"{month_name[best + 1]} was {who} month"
    lines = wrap(head, font("ExtraBold", 84), 900)
    draw_lines(d, 78, 250, lines, font("ExtraBold", 84), WHITE, 96)
    y = 250 + 96 * len(lines) + 10
    draw_text(d, 80, y, f"{months[best]:,} messages", font("Bold", 46), YELLOW)
    month_bars(img, months, 80, 1000, y + 170, 1080, (52, 120, 116), YELLOW, (130, 190, 182))
    d = ImageDraw.Draw(img)
    if weekday is not None:
        f = font("Medium", 36)
        pre = "Favourite day of the week: "
        draw_text(d, 80, 1180, pre, f, (190, 225, 220))
        draw_text(d, 80 + text_width(pre, f), 1180, WEEKDAYS[weekday], font("Bold", 36), YELLOW)
    story_frame(img, page, total, year, WHITE)
    return finish_story(img)


def slide_clock(hours: List[int], year: int, page: int, total: int, title="AROUND THE CLOCK",
                caption_who="you") -> bytes:
    img = canvas((12, 16, 50), (4, 6, 20))
    rng = random.Random(year)

    def stars(d):
        for _ in range(140):
            x, y, r = rng.uniform(0, SW), rng.uniform(90, SH - 90), rng.uniform(1, 2.6)
            d.ellipse([s(x - r), s(y - r), s(x + r), s(y + r)], fill=WHITE + (rng.randint(60, 200),))
    overlay(img, stars)
    blob(img, SW / 2, 650, 300, (90, 110, 255), 70)
    d = ImageDraw.Draw(img)
    draw_tracked(d, 80, 200, title, font("Bold", 26), (170, 180, 255), 5)
    peak = clock(img, hours, SW / 2, 650, 118, 200, (40, 50, 110), YELLOW, (150, 160, 220))
    d = ImageDraw.Draw(img)
    draw_text(d, SW / 2, 636, f"{peak:02d}:00", font("ExtraBold", 62), WHITE, anchor="mm")
    draw_text(d, SW / 2, 688, "peak hour", font("Medium", 24), (170, 180, 230), anchor="mm")
    window = max(range(24), key=lambda h: hours[h] + hours[(h + 1) % 24] + hours[(h + 2) % 24])
    tot = sum(hours) or 1
    share = (hours[window] + hours[(window + 1) % 24] + hours[(window + 2) % 24]) / tot * 100
    lines = wrap(f"{share:.0f}% of {'your' if caption_who == 'you' else 'all'} messages land between "
                 f"{window:02d}:00 and {(window + 3) % 24:02d}:00.", font("Bold", 42), 900)
    draw_lines(d, SW / 2, 1090, lines, font("Bold", 42), WHITE, 56, anchor="ma")
    draw_text(d, SW / 2, 1090 + 56 * len(lines) + 22, "All times in UTC", font("Medium", 26), (130, 140, 200), anchor="ma")
    story_frame(img, page, total, year, WHITE)
    return finish_story(img)


def slide_streak(w: UserWrapped, page: int, total: int) -> bytes:
    img = canvas((28, 28, 32), (10, 10, 12))
    blob(img, 200, 260, 380, YELLOW, 55)
    d = ImageDraw.Draw(img)
    longest, a, b = w.longest_streak
    draw_tracked(d, 80, 200, "LONGEST STREAK", font("Bold", 26), (200, 190, 150), 5)
    draw_text(d, 70, 236, f"{longest}", font("ExtraBold", 300), YELLOW)
    nx = 70 + text_width(f"{longest}", font("ExtraBold", 300)) + 28
    draw_text(d, nx, 330, "day" if longest == 1 else "days", font("ExtraBold", 80), WHITE)
    draw_text(d, nx, 430, "in a row", font("Medium", 44), (200, 200, 205))
    if a:
        draw_text(d, 82, 600, fmt_range(a, b), font("Bold", 40), WHITE)
    streak_days = [a + timedelta(days=i) for i in range(longest)] if a else []
    year_heatmap(img, w.daily, w.year, 80, 1000, 760, highlight=streak_days)
    d = ImageDraw.Draw(img)
    pct = w.active_days / (366 if w.year % 4 == 0 else 365) * 100
    draw_text(d, 80, 1000, f"You showed up on {w.active_days} days", font("ExtraBold", 50), WHITE)
    draw_text(d, 80, 1068, f"That's {pct:.0f}% of the year. The outlined squares are your streak.",
              font("Medium", 30), (170, 170, 178))
    story_frame(img, page, total, w.year, WHITE)
    return finish_story(img)


def slide_starboard(w: UserWrapped, page: int, total: int) -> bytes:
    img = canvas((14, 70, 46), (4, 26, 18))
    rng = random.Random(w.year * 7 + w.hearts)

    def hearts(d):
        for _ in range(26):
            heart(d, rng.uniform(40, SW - 40), rng.uniform(120, SH - 120), rng.uniform(18, 60),
                  HEART + (rng.randint(30, 90),))
    overlay(img, hearts)
    d = ImageDraw.Draw(img)
    draw_tracked(d, 80, 200, "THE STARBOARD", font("Bold", 26), (170, 230, 170), 5)
    draw_text(d, 80, 250, "Your messages earned", font("Medium", 46), WHITE)
    num = f"{w.hearts:,}"
    draw_text(d, 70, 300, num, fit_font(num, "ExtraBold", 280, 700), HEART)
    hx = 70 + text_width(num, fit_font(num, "ExtraBold", 280, 700)) + 70
    heart(d, hx, 420, 120, HEART)
    draw_text(d, 80, 620, "green hearts", font("ExtraBold", 80), WHITE)
    times = f"{w.starboard_posts} time{'s' if w.starboard_posts != 1 else ''}"
    draw_text(d, 80, 730, f"and made the starboard {times}.", font("Medium", 42), (200, 235, 200))
    if w.best_post:
        n, when = w.best_post
        d.rounded_rectangle([s(80), s(860), s(1000), s(1060)], radius=s(36), fill=(22, 84, 56))
        heart(d, 170, 950, 86, HEART)
        draw_tracked(d, 250, 898, "YOUR MOST LOVED MESSAGE", font("Bold", 20), (170, 230, 170), 3)
        draw_text(d, 248, 940, f"{n} hearts · {when.day} {month_name[when.month]}", font("ExtraBold", 50), WHITE)
    if w.questions_solved:
        q = w.questions_solved
        draw_text(d, 80, 1130, f"Plus {q} of your question{'s' if q != 1 else ''} in the help forum got solved.",
                  font("Medium", 32), (190, 225, 190))
    story_frame(img, page, total, w.year, WHITE)
    return finish_story(img)


def slide_persona(w: UserWrapped, page: int, total: int) -> bytes:
    name, accessory, tagline = choose_persona(w)
    img = canvas((70, 14, 60), (24, 10, 66))
    blob(img, SW / 2, 620, 380, YELLOW, 70)
    blob(img, 140, 220, 300, (255, 90, 170), 80)
    blob(img, 960, 1180, 320, (110, 90, 255), 80)
    rng = random.Random(name)

    def sparkle(d):
        for _ in range(18):
            star(d, rng.uniform(60, SW - 60), rng.uniform(150, 900), rng.uniform(6, 16), WHITE + (rng.randint(90, 220),))
    overlay(img, sparkle)
    d = ImageDraw.Draw(img)
    draw_tracked(d, SW / 2, 200, "YOUR DUCK TYPE IS", font("Bold", 28), (240, 200, 240), 6, align="center")
    draw_ducky(img, SW / 2, 868, 590, accessory)
    d = ImageDraw.Draw(img)
    draw_text(d, SW / 2, 960, name, fit_font(name, "ExtraBold", 104, 960), YELLOW, anchor="mm")
    lines = wrap(tagline, font("Medium", 38), 820)
    draw_lines(d, SW / 2, 1040, lines, font("Medium", 38), (235, 220, 240), 54, anchor="ma")
    story_frame(img, page, total, w.year, WHITE)
    return finish_story(img)


def user_summary(w: UserWrapped) -> bytes:
    name, accessory, _ = choose_persona(w)
    img = canvas((26, 26, 30), (10, 10, 12))
    blob(img, 900, 160, 360, YELLOW, 60)
    blob(img, 100, 1250, 340, (255, 90, 170), 40)
    avatar(img, w.avatar, 160, 190, 82, fallback=w.name)
    d = ImageDraw.Draw(img)
    draw_tracked(d, 280, 120, f"DUCKY WRAPPED {w.year}", font("Bold", 22), YELLOW, 4)
    draw_text(d, 278, 152, w.name, fit_font(w.name, "ExtraBold", 66, 520), WHITE)
    pill_f = font("Bold", 26)
    pw = text_width(name.upper(), pill_f, 2) + 60
    d.rounded_rectangle([s(280), s(242), s(280 + pw), s(292)], radius=s(25), fill=(70, 58, 20))
    draw_tracked(d, 310, 252, name.upper(), pill_f, YELLOW, 2)
    draw_ducky(img, 925, 330, 220, accessory)
    d = ImageDraw.Draw(img)

    longest = w.longest_streak[0]
    months = w.months
    tiles = [
        ("MESSAGES", f"{w.total:,}"),
        ("TOP", f"{max(100 - (w.beat_pct or 0), 1):.0f}%" if w.beat_pct is not None else "—"),
        ("ACTIVE DAYS", f"{w.active_days}"),
        ("LONGEST STREAK", f"{longest} day{'s' if longest != 1 else ''}"),
        ("BEST MONTH", month_name[max(range(12), key=lambda i: months[i]) + 1] if w.total else "—"),
    ]
    if w.hearts:
        tiles.append(("GREEN HEARTS", f"{w.hearts:,}"))
    else:
        tiles.append(("PEAK HOUR", f"{max(range(24), key=lambda h: w.hours[h]):02d}:00 UTC"))
    for i, (label, value) in enumerate(tiles):
        col, row = i % 2, i // 2
        stat_tile(d, 80 + col * 470, 380 + row * 215, 450, 190, label, value,
                  YELLOW if i in (0, 3) else WHITE)
    draw_tracked(d, 80, 1048, "THE YEAR IN SQUARES", font("SemiBold", 19), (150, 150, 158), 2.4)
    year_heatmap(img, w.daily, w.year, 80, 1000, 1112, months_labels=False)
    d = ImageDraw.Draw(img)
    draw_tracked(d, SW / 2, 1278, "ENTE COMMUNITY  ·  DUCKY WRAPPED", font("SemiBold", 18), (110, 110, 118), 3,
                 align="center")
    return finish_story(img)


def render_user_slides(w: UserWrapped) -> List[bytes]:
    builders = [
        lambda p, t: slide_intro(w.name, "your year on the Ente Discord", w.avatar, w.year, p, t),
        lambda p, t: slide_messages(w, p, t),
        lambda p, t: slide_months(w.months, w.year, p, t, "your", w.busiest_weekday),
        lambda p, t: slide_clock(w.hours, w.year, p, t),
        lambda p, t: slide_streak(w, p, t),
    ]
    if w.hearts or w.starboard_posts:
        builders.append(lambda p, t: slide_starboard(w, p, t))
    builders.append(lambda p, t: slide_persona(w, p, t))
    total = len(builders) + 1
    slides = [b(i, total) for i, b in enumerate(builders)]
    slides.append(user_summary(w))
    return slides


# --------------------------------------------------------------------------- #
# Server slides
# --------------------------------------------------------------------------- #


def slide_server_messages(sw: ServerWrapped, page: int, total: int) -> bytes:
    img = canvas((255, 216, 92), (255, 186, 36))
    blob(img, 120, 160, 360, WHITE, 90)
    blob(img, 980, 1220, 420, (255, 128, 40), 90)
    d = ImageDraw.Draw(img)
    draw_tracked(d, 80, 220, "TOGETHER WE SENT", font("Bold", 34), INK, 5)
    draw_text(d, 70, 262, compact(sw.total) if sw.total >= 1_000_000 else f"{sw.total:,}",
              fit_font(f"{sw.total:,}", "ExtraBold", 270, 930), INK)
    draw_text(d, 80, 560, "messages", font("ExtraBold", 84), INK)
    line = f"from {sw.active_members:,} members"
    f = font("Bold", 38)
    d.rounded_rectangle([s(80), s(700), s(80 + text_width(line, f) + 56), s(774)], radius=s(37), fill=INK)
    draw_text(d, 108, 737, line, f, YELLOW, anchor="lm")
    days = 366 if sw.year % 4 == 0 else 365
    secs = days * 86400 / sw.total if sw.total else 0
    every = f"{secs:.0f} seconds" if secs < 120 else f"{secs / 60:.0f} minutes"
    busiest = max(sw.daily.items(), key=lambda kv: kv[1]) if sw.daily else None
    text = f"That's one message every {every}, all year long."
    if busiest:
        text += f" The busiest day was {busiest[0].day} {month_name[busiest[0].month]}, with {busiest[1]:,} messages."
    draw_lines(d, 82, 860, wrap(text, font("Medium", 38), 560), font("Medium", 38), INK, 52)
    draw_ducky(img, 850, 1250, 330)
    story_frame(img, page, total, sw.year, INK)
    return finish_story(img)


def slide_podium(sw: ServerWrapped, page: int, total: int) -> bytes:
    img = canvas((44, 16, 80), (14, 8, 34))
    blob(img, SW / 2, 520, 420, YELLOW, 60)
    d = ImageDraw.Draw(img)
    draw_tracked(d, SW / 2, 200, "TOP QUACKERS", font("Bold", 28), (220, 200, 255), 6, align="center")
    draw_text(d, SW / 2, 270, "The loudest ducks in the pond", font("ExtraBold", 56), WHITE, anchor="mm")
    # (place in top list, x, podium height, colour): 2nd left, 1st centre, 3rd right
    spots = [(1, SW / 2 - 300, 220, SILVER), (0, SW / 2, 300, GOLD), (2, SW / 2 + 300, 170, BRONZE)]
    base = 960
    for idx, x, h, color in spots:
        if idx >= len(sw.top):
            continue
        name, av, n = sw.top[idx]
        rank = idx + 1
        d.rounded_rectangle([s(x - 130), s(base - h), s(x + 130), s(base + 40)], radius=s(22),
                            fill=blend((40, 30, 70), color, 0.25))
        draw_text(d, x, base - h + 70, str(rank), font("ExtraBold", 90), color, anchor="mm")
        draw_text(d, x, base - h + 140, f"{n:,}", font("Bold", 30), WHITE, anchor="mm")
        r = 86 if rank == 1 else 70
        avatar(img, av, x, base - h - r - 30, r, ring=color, fallback=name)
        d = ImageDraw.Draw(img)
        draw_text(d, x, base - h - 2 * r - 74, name, fit_font(name, "Bold", 36, 260), WHITE, anchor="mm")
    y = 1060
    for i, (name, _av, n) in enumerate(sw.top[3:5], start=4):
        draw_text(d, 140, y, f"{i}", font("ExtraBold", 40), (200, 180, 255), anchor="lm")
        draw_text(d, 200, y, name, fit_font(name, "Bold", 38, 560), WHITE, anchor="lm")
        draw_text(d, 940, y, f"{n:,}", font("Bold", 38), (200, 180, 255), anchor="rm")
        y += 70
    story_frame(img, page, total, sw.year, WHITE)
    return finish_story(img)


def slide_pond_numbers(sw: ServerWrapped, page: int, total: int) -> bytes:
    img = canvas((14, 70, 46), (4, 26, 18))
    blob(img, 900, 300, 380, HEART, 50)
    d = ImageDraw.Draw(img)
    draw_tracked(d, 80, 200, "THE POND IN NUMBERS", font("Bold", 26), (170, 230, 170), 5)
    rows = []
    if sw.new_members:
        rows.append((f"{sw.new_members:,}", "new members joined the pond"))
    if sw.solved_threads:
        rows.append((f"{sw.solved_threads:,}", "help threads solved"))
    if sw.starboard_posts:
        rows.append((f"{sw.starboard_posts:,}", "messages made the starboard"))
    if sw.hearts:
        rows.append((f"{sw.hearts:,}", "green hearts handed out"))
    if sw.github_stars:
        rows.append((compact(sw.github_stars), "GitHub stars on ente/ente"))
    if sw.files_protected:
        rows.append((compact(sw.files_protected), "files protected by Ente"))
    y = 270
    step = min(160, 900 / max(len(rows), 1))
    for value, label in rows:
        draw_text(d, 80, y, value, fit_font(value, "ExtraBold", 108, 420), HEART if label.startswith("green") else YELLOW)
        draw_text(d, 520, y + 62, label, fit_font(label, "Medium", 38, 480), WHITE, anchor="lm")
        y += step
    story_frame(img, page, total, sw.year, WHITE)
    return finish_story(img)


def server_summary(sw: ServerWrapped) -> bytes:
    img = canvas((26, 26, 30), (10, 10, 12))
    blob(img, 900, 160, 360, YELLOW, 60)
    blob(img, 100, 1250, 340, (60, 200, 220), 40)
    avatar(img, sw.icon, 160, 190, 82, fallback=sw.name)
    d = ImageDraw.Draw(img)
    draw_tracked(d, 280, 140, f"DUCKY WRAPPED {sw.year}", font("Bold", 22), YELLOW, 4)
    draw_text(d, 278, 172, sw.name, fit_font(sw.name, "ExtraBold", 66, 520), WHITE)
    draw_ducky(img, 925, 330, 220)
    d = ImageDraw.Draw(img)
    months = sw.months
    peak_h = max(range(24), key=lambda h: sw.hours[h])
    tiles = [
        ("MESSAGES", compact(sw.total) if sw.total >= 100_000 else f"{sw.total:,}"),
        ("MEMBERS CHATTING", f"{sw.active_members:,}"),
        ("NEW MEMBERS", f"{sw.new_members:,}"),
        ("BUSIEST MONTH", month_name[max(range(12), key=lambda i: months[i]) + 1] if sw.total else "—"),
        ("HELP THREADS SOLVED", f"{sw.solved_threads:,}"),
        ("PEAK HOUR", f"{peak_h:02d}:00 UTC"),
    ]
    for i, (label, value) in enumerate(tiles):
        col, row = i % 2, i // 2
        stat_tile(d, 80 + col * 470, 380 + row * 215, 450, 190, label, value, YELLOW if i in (0, 3) else WHITE)
    draw_tracked(d, 80, 1048, "THE YEAR IN SQUARES", font("SemiBold", 19), (150, 150, 158), 2.4)
    year_heatmap(img, sw.daily, sw.year, 80, 1000, 1112, months_labels=False)
    d = ImageDraw.Draw(img)
    draw_tracked(d, SW / 2, 1278, "ENTE COMMUNITY  ·  DUCKY WRAPPED", font("SemiBold", 18), (110, 110, 118), 3,
                 align="center")
    return finish_story(img)


def render_server_slides(sw: ServerWrapped) -> List[bytes]:
    builders = [
        lambda p, t: slide_intro(sw.name, "our year together", sw.icon, sw.year, p, t),
        lambda p, t: slide_server_messages(sw, p, t),
        lambda p, t: slide_months(sw.months, sw.year, p, t, "the busiest"),
        lambda p, t: slide_clock(sw.hours, sw.year, p, t, "WHEN THE POND IS BUSIEST", "all"),
    ]
    if sw.top:
        builders.append(lambda p, t: slide_podium(sw, p, t))
    builders.append(lambda p, t: slide_pond_numbers(sw, p, t))
    total = len(builders) + 1
    slides = [b(i, total) for i, b in enumerate(builders)]
    slides.append(server_summary(sw))
    return slides
