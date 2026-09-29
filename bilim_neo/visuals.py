"""NEO school-board artwork, drawn in code; personal cards stay in memory."""

import html
import io
import math
import re
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont

BG = "#151916"
INK = "#f2f0e5"
ACCENT = "#d4f66a"
MUTED = "#9ca69b"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TITLES = {"home": ("01", "Твой день"), "day": ("02", "Расписание"), "week": ("03", "Вся неделя"),
          "bells": ("04", "Звонки"), "homework": ("05", "Домашка"), "marks": ("06", "Оценки"),
          "grades": ("07", "Табель"), "settings": ("08", "Настройки"), "profile": ("09", "Профиль"),
          "advice": ("10", "На заметку"), "attendance": ("11", "Посещаемость"), "bonus": ("12", "BONUS LORD"),
          "task": ("13", "План ДЗ"), "files": ("14", "Материалы"), "ref": ("15", "Вместе проще"),
          "support": ("16", "Есть мысль?"), "quiet": ("17", "Тихие часы"),
          "admin": ("00", "Управление"), "welcome": ("→", "Твой темп.")}


def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def plain(content):
    text = html.unescape(re.sub(r"<[^>]+>", "", content))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def fit(draw, text, width, size=30):
    face = font(size)
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


def card(section, content=""):
    number, title = TITLES.get(section, ("•", "Дневник"))
    image = Image.new("RGB", (1200, 680), BG)
    draw = ImageDraw.Draw(image)
    for x in range(760, 1200, 44):
        draw.line((x, 0, x, 680), fill="#242c23", width=1)
    for y in range(20, 680, 44):
        draw.line((760, y, 1200, y), fill="#242c23", width=1)
    draw.text((62, 38), "НЭО", font=font(44, True), fill=INK)
    draw.text((238, 58), "ЛИЧНЫЙ ДНЕВНИК", font=font(18), fill=MUTED)
    draw.line((62, 112, 1138, 112), fill="#4b5347", width=2)
    draw.text((62, 150), title, font=font(65, True), fill=INK)
    draw.text((915, 128), number, font=font(112, True), fill=ACCENT)
    lines = plain(content).splitlines()
    if lines:
        lines = lines[:5]
    else:
        lines = ["Уроки. Задания. Оценки.", "Всё под рукой."]
    for index, line in enumerate(lines):
        y = 268 + index * 53
        draw.rectangle((65, y+9, 72, y+30), fill=ACCENT if index == 0 else "#526047")
        # Skip emoji glyphs unsupported by the typeface; Telegram retains the full text.
        line = "".join(c for c in line if ord(c) < 0x2000 or c in "→←•—–…№✓")
        draw.text((92, y), fit(draw, line, 970, 34), font=font(34), fill=INK if index == 0 else MUTED)
    draw.rectangle((0, 601, 1200, 680), fill=ACCENT)
    draw.text((64, 622), "УРОКИ · ЗАДАНИЯ · ОЦЕНКИ", font=font(25, True), fill=BG)
    draw.text((948, 628), "NEO / DAILY", font=font(19), fill=BG)
    return image


def card_bytes(section, content=""):
    output = io.BytesIO()
    card(section, content).save(output, "PNG", optimize=True)
    return output.getvalue()


@lru_cache(maxsize=1)
def welcome_animation():
    base = card("welcome", "Расписание на сегодня\nЗадания без потерянных файлов\nОценки и напоминания")
    frames = []
    for index in range(20):
        image = base.copy()
        draw = ImageDraw.Draw(image)
        x = 64 + round((1-math.cos(index/19 * math.pi*2))/2 * 940)
        draw.rounded_rectangle((x, 577, x+132, 583), radius=3, fill=ACCENT)
        frames.append(image.resize((900, 510)))
    output = io.BytesIO()
    frames[0].save(output, "GIF", save_all=True, append_images=frames[1:], duration=90, loop=0, optimize=True)
    return output.getvalue()


def section_for(data):
    parts = (data or "view:home").split(":")
    return parts[1] if parts[0] == "view" and len(parts) > 1 else parts[0]
