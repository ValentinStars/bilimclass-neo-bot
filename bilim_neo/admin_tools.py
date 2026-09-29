"""Pure admin reports, audience selection and in-memory chart rendering."""

import csv
import io
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont


def allowed(chat_id):
    return str(chat_id) in {part.strip() for part in os.getenv("ADMIN_IDS", "").split(",") if part.strip().isdigit()}


def city(profile):
    address = profile.get("schoolAddress") or ""
    region = profile.get("region") or ""
    match = re.search(r"(?:город|г\.)\s*([А-Яа-яЁёA-Za-z\- ]+?)(?:,|$)", address) or re.search(r"^г\.\s*(.+)$", region)
    return match.group(1).strip() if match else None


def audiences(users):
    profiles = [row[1]["profile"] for row in users]
    return {
        "class": sorted({str(p.get("group")) for p in profiles if p.get("group")}),
        "school": sorted({str(p.get("schoolName")) for p in profiles if p.get("schoolName")}),
        "city": sorted({city(p) for p in profiles if city(p)}),
        "student": [str(chat_id) for chat_id, _ in users],
    }


def recipients(users, scope, value=""):
    if scope == "all":
        return [chat_id for chat_id, _ in users]
    if scope == "student":
        return [chat_id for chat_id, _ in users if str(chat_id) == str(value)]
    field = {"class": "group", "school": "schoolName"}.get(scope)
    if field:
        return [chat_id for chat_id, user in users if user["profile"].get(field) == value]
    if scope == "city":
        return [chat_id for chat_id, user in users if city(user["profile"]) == value]
    return []


def csv_bytes(rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return "\ufeff".encode() + output.getvalue().encode("utf-8")


def credentials_csv(users):
    return csv_bytes([["login", "password", "class"]] + [[u["login"], u["password"], u["profile"].get("group") or ""] for _, u in users])


def activity_csv(counts):
    return csv_bytes([["telegram_id", "events_14d", "last_event_utc"]] + counts["users"])


def server_report(db_path):
    uptime = None
    try:
        uptime = int(float(open("/proc/uptime", encoding="ascii").read().split()[0]))
    except (OSError, ValueError):
        pass
    bot_uptime = None
    bot_memory = None
    try:
        stat = open("/proc/self/stat", encoding="ascii").read().split()
        bot_uptime = max(0, uptime - int(stat[21]) / os.sysconf("SC_CLK_TCK")) if uptime is not None else None
        bot_memory = int(open("/proc/self/statm", encoding="ascii").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1024**2
    except (OSError, ValueError, IndexError):
        pass
    usage = os.statvfs(os.path.dirname(os.path.abspath(db_path)))
    free_gb = usage.f_bavail * usage.f_frsize / 1024**3
    total_gb = usage.f_blocks * usage.f_frsize / 1024**3
    memory = {}
    try:
        for line in open("/proc/meminfo", encoding="ascii"):
            key, _, value = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                memory[key] = int(value.strip().split()[0]) / 1024**2
    except OSError:
        pass
    return {"uptime_h": round(uptime / 3600, 1) if uptime is not None else None,
            "bot_uptime_h": round(bot_uptime / 3600, 2) if bot_uptime is not None else None,
            "bot_memory_mb": round(bot_memory, 1) if bot_memory is not None else None,
            "disk_free_gb": round(free_gb, 1), "disk_total_gb": round(total_gb, 1),
            "ram_free_gb": round(memory.get("MemAvailable", 0), 1),
            "ram_total_gb": round(memory.get("MemTotal", 0), 1),
            "load": tuple(round(x, 2) for x in os.getloadavg()),
            "database_kb": round(os.path.getsize(db_path) / 1024)}


def journal(limit=None):
    command = ["journalctl", "--user", "-u", "bilim-neo.service", "--no-pager", "--output=short-iso"]
    if limit:
        command.extend(["-n", str(limit)])
    result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    if result.returncode:
        raise RuntimeError("Журнал сервиса недоступен")
    return result.stdout


def activity_chart(counts, registrations):
    """Render a compact, legible PNG without writing student data to disk."""
    image = Image.new("RGB", (1200, 690), "#0c1728")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    regular = ImageFont.truetype(font_path, 26)
    bold = ImageFont.truetype(font_path, 42)
    small = ImageFont.truetype(font_path, 18)
    draw.rounded_rectangle((32, 32, 1168, 658), radius=34, fill="#14243c")
    draw.text((75, 68), "НЭО / АКТИВНОСТЬ", font=bold, fill="#f5f8ff")
    draw.text((76, 135), "Последние 14 дней · действия в боте", font=regular, fill="#98afc7")
    today = datetime.now(timezone.utc).date()
    daily = dict(counts["daily"])
    values = [(today - timedelta(days=13-index), daily.get((today - timedelta(days=13-index)).isoformat(), 0)) for index in range(14)]
    peak = max(1, *(v for _, v in values))
    left, bottom, width = 86, 510, 1000
    for index, (day, value) in enumerate(values):
        x = left + index * (width / 14)
        height = max(3, round(value / peak * 245))
        draw.rounded_rectangle((x, bottom-height, x+42, bottom), radius=10, fill="#47d6b0" if value else "#30435d")
        if value:
            draw.text((x+11, bottom-height-29), str(value), font=small, fill="#e9fff7")
        draw.text((x-2, bottom+20), day.strftime("%d.%m"), font=small, fill="#98afc7")
    draw.text((76, 592), f"Подключено: {registrations}   •   Событий: {sum(v for _, v in values)}   •   Пик: {peak}", font=regular, fill="#e9f1fb")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def classes_chart(users):
    counts = Counter((user["profile"].get("group") or "Класс неизвестен") for _, user in users)
    top = counts.most_common(8)
    image = Image.new("RGB", (1200, 690), "#0c1728")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    title = ImageFont.truetype(font_path, 42)
    regular = ImageFont.truetype(font_path, 24)
    draw.rounded_rectangle((32, 32, 1168, 658), radius=34, fill="#14243c")
    draw.text((75, 68), "НЭО / КЛАССЫ", font=title, fill="#f5f8ff")
    draw.text((75, 134), f"Подключено учеников: {len(users)} · классов: {len(counts)}", font=regular, fill="#98afc7")
    peak = max([n for _, n in top], default=1)
    for index, (label, value) in enumerate(top):
        y = 207 + index * 53
        draw.text((80, y), str(label)[:16], font=regular, fill="#e9f1fb")
        draw.rounded_rectangle((300, y, 300 + max(5, round(value / peak * 690)), y+28), radius=10, fill="#5caaf4" if index % 2 else "#47d6b0")
        draw.text((1020, y), str(value), font=regular, fill="#e9f1fb")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
