"""Read only the public BONUS LORD sheet for the student's own class."""

import csv
import io
import re
import time

import requests

SHEET_ID = "1Bd4FcI-Og7tuGHxeECW0RgGbbea9oz_eHjz9hqkthYA"
BASE = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
_cache = ({}, 0.0)


def is_lord_school(name):
    return bool(re.search(r"\bЛОРД\b", name or "", re.IGNORECASE))


def class_key(group):
    match = re.search(r"(?<!\d)(\d{1,2})\s*([АБВГA-D])\b", (group or "").upper())
    if match:
        return match[1] + match[2].translate(str.maketrans("ABCD", "АБВГ"))
    match = re.search(r"(?<!\d)(\d{1,2})(?!\d)", group or "")
    return match[1] if match else None


def parse_sheets(html):
    """The public Sheets HTML embeds gid and title pairs in its initial data."""
    pairs = re.findall(r'\\"(\d{5,12})\\",\[\{\\"1\\":\[\[0,0,\\"([^\\"]+)\\"', html)
    return {key: gid for gid, key in pairs if re.fullmatch(r"\d{1,2}[АБВГ]?", key)}


def sheets():
    global _cache
    if _cache[0] and time.monotonic() - _cache[1] < 3600:
        return _cache[0]
    response = requests.get(BASE + "/edit", timeout=15)
    response.raise_for_status()
    found = parse_sheets(response.text)
    if not found:
        raise RuntimeError("Не удалось прочитать список классов BONUS LORD")
    _cache = (found, time.monotonic())
    return found


def bonus_for_class(group):
    key = class_key(group)
    gid = sheets().get(key) if key else None
    if not gid:
        return key, None
    response = requests.get(BASE + "/gviz/tq", params={"tqx": "out:csv", "gid": gid, "range": "A7"}, timeout=15)
    response.raise_for_status()
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError("Неожиданный ответ BONUS LORD")
    raw = rows[0][0].strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        raise RuntimeError("В ячейке A7 нет числа")
    return key, float(raw)
