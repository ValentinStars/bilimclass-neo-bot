"""Compact, readable Telegram cards. Personal images exist only in memory."""

import html
import io
import math
import re
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

WIDTH = 960
BG = "#151916"
SURFACE = "#202720"
INK = "#f2f0e5"
MUTED = "#aab3a6"
ACCENT = "#d4f66a"
GREEN = "#a8ed91"
YELLOW = "#f4d177"
RED = "#ff8b82"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

TITLES = {"home": "Сегодня", "day": "Уроки", "week": "Неделя", "bells": "Звонки",
          "homework": "Домашка", "marks": "Оценки", "grades": "Табель", "attendance": "Посещаемость",
          "task": "План ДЗ", "advice": "Подсказки", "bonus": "BONUS LORD", "welcome": "Твой дневник"}
VISUAL_SECTIONS = frozenset(TITLES)
MARK_TYPES = (("regular", "ФО"), ("sor", "СОР"), ("soch", "СОЧ"), ("po", "ПО"))


def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def plain(content):
    text = html.unescape(re.sub(r"<[^>]+>", "", content))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def clean(text):
    """DejaVu does not ship color emoji; keep the readable text around them."""
    return "".join(c for c in str(text) if ord(c) < 0x2000 or c in "→←•—–…№✓§")


def fit(draw, text, width, size=29, bold=False):
    text = clean(text)
    face = font(size, bold)
    if draw.textlength(text, font=face) <= width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if draw.textlength(text[:middle] + "…", font=face) <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "…"


def grade_color(score, maximum):
    try:
        value, total = float(str(score).replace(",", ".")), float(str(maximum).replace(",", "."))
        if total <= 0:
            return MUTED
        ratio = value / total
    except (TypeError, ValueError):
        return MUTED
    if ratio >= .8:
        return GREEN
    if ratio >= .6:
        return YELLOW
    return RED


def grade_rows(marks, limit=6):
    """Group latest diary marks by subject, retaining the real score denominator."""
    rows = {}
    for entry in reversed(marks):
        subject = str(entry.get("subject") or "Предмет")
        for key, label in MARK_TYPES:
            score = entry.get(key + "_mark")
            if score is None:
                continue
            group = rows.setdefault(subject, {"subject": subject, "date": entry.get("date") or "", "marks": []})
            if len(group["marks"]) < 2:
                group["marks"].append({"type": label, "score": str(score), "max": entry.get(key + "_max")})
    return list(rows.values())[:limit]


def _header(draw, title, subtitle=""):
    draw.text((28, 19), "НЭО", font=font(24, True), fill=ACCENT)
    draw.text((105, 18), fit(draw, title, 675, 32, True), font=font(32, True), fill=INK)
    if subtitle:
        draw.text((31, 63), fit(draw, subtitle, 880, 21), font=font(21), fill=MUTED)


def _score_chip(draw, score, maximum, x, y):
    color = grade_color(score, maximum)
    value = score if maximum is None else f"{score}/{maximum}"
    draw.rounded_rectangle((x, y, x+98, y+48), radius=12, fill=color)
    draw.text((x+49, y+24), fit(draw, value, 90, 22, True), anchor="mm", font=font(22, True), fill=BG)


def grade_card(rows, period=None):
    rows = rows[:6]
    height = max(176, 102 + 70 * len(rows))
    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    subtitle = f"{period} четверть · последние оценки по предметам" if period else "Последние оценки по предметам"
    _header(draw, "Оценки", subtitle)
    draw.line((28, 92, WIDTH-28, 92), fill="#384238", width=2)
    if not rows:
        draw.text((30, 119), "Пока оценок нет", font=font(29), fill=MUTED)
        return image
    for index, row in enumerate(rows):
        y = 99 + index * 70
        if index % 2 == 0:
            draw.rounded_rectangle((23, y, WIDTH-23, y+67), radius=12, fill=SURFACE)
        draw.text((37, y+8), fit(draw, row["subject"], 475, 29, True), font=font(29, True), fill=INK)
        if row.get("date"):
            draw.text((38, y+41), fit(draw, row["date"], 170, 18), font=font(18), fill=MUTED)
        for mark_index, mark in enumerate(row["marks"][:2]):
            x = 560 + mark_index * 181
            draw.text((x, y+24), mark["type"], font=font(21, True), fill=MUTED)
            _score_chip(draw, mark["score"], mark.get("max"), x+49, y+9)
    return image


def summary_rows(section, content):
    lines = plain(content).splitlines()
    if lines:
        lines = lines[2:] if section == "home" and len(lines) > 1 else lines[1:]
    lines = [line for line in lines if line and not line.startswith(("Открой нужный раздел", "Выбери день кнопкой"))]
    if section in ("day", "bells"):
        lessons = [line for line in lines if re.match(r"^(?:\d{2} ·|\d{1,2}:\d{2}.*\d{2} ·)", line)]
        if lessons:
            lines = lessons
    elif section == "week":
        days = [line for line in lines if re.match(r"^(?:Пн|Вт|Ср|Чт|Пт|Сб|Вс)\s+\d", line)]
        if days:
            lines = days
    if section == "welcome" and not lines:
        lines = ["Расписание, задания и оценки", "Всё на одном экране"]
    return lines[:6]


def card(section, content="", marks=None, period=None):
    if section == "marks" and marks is not None:
        return grade_card(marks, period)
    rows = summary_rows(section, content)
    height = max(160, 98 + len(rows) * 58)
    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    source = plain(content).splitlines()
    headline = source[1] if section == "home" and len(source) > 1 else (source[0] if source else "")
    subtitle = headline.split("·", 1)[1].strip() if "·" in headline else ""
    _header(draw, TITLES.get(section, "Дневник"), subtitle)
    draw.line((28, 82, WIDTH-28, 82), fill="#384238", width=2)
    if not rows:
        rows = ["Нет данных"]
    for index, line in enumerate(rows):
        y = 94 + index * 58
        if index % 2 == 0:
            draw.rounded_rectangle((23, y, WIDTH-23, y+55), radius=11, fill=SURFACE)
        draw.text((37, y+10), fit(draw, line, WIDTH-80, 29, index == 0), font=font(29, index == 0), fill=INK if index == 0 else MUTED)
    return image


def card_bytes(section, content="", marks=None, period=None):
    output = io.BytesIO()
    card(section, content, marks, period).save(output, "PNG", optimize=True)
    return output.getvalue()


@lru_cache(maxsize=1)
def welcome_animation():
    base = card("welcome", "НЭО · Твой дневник\nРасписание по дням\nЗадания и оценки")
    frames = []
    for index in range(14):
        image = base.copy()
        draw = ImageDraw.Draw(image)
        x = 25 + round((1-math.cos(index/13 * math.pi*2))/2 * 795)
        draw.rounded_rectangle((x, image.height-8, x+100, image.height-4), radius=2, fill=ACCENT)
        frames.append(image)
    output = io.BytesIO()
    frames[0].save(output, "GIF", save_all=True, append_images=frames[1:], duration=85, loop=0, optimize=True)
    return output.getvalue()


def section_for(data):
    parts = (data or "view:home").split(":")
    return parts[1] if parts[0] == "view" and len(parts) > 1 else parts[0]
