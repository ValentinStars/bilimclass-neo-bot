"""Pure scheduling rules for proactive, opt-in messages."""

import re
from datetime import datetime, timedelta


def first_bell(timeslot):
    """BilimClass timeslots vary; only an explicit HH:MM can schedule a bell."""
    match = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)", str(timeslot or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def due_bells(lessons, now: datetime, lead_minutes=10):
    """Return lessons whose reminder time falls in the current minute."""
    result = []
    for index, lesson in enumerate(lessons, 1):
        bell = first_bell(lesson.get("timeslot"))
        if bell is None:
            continue
        start = now.replace(hour=bell[0], minute=bell[1], second=0, microsecond=0)
        reminder = start - timedelta(minutes=lead_minutes)
        if reminder <= now < reminder + timedelta(minutes=1):
            result.append((index, lesson, start))
    return result


def quiet_now(prefs, hour):
    start, end = prefs["quiet_from"], prefs["quiet_to"]
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end
