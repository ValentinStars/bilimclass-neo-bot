"""Pure formatting and analysis helpers for the Telegram interface."""

import re
from datetime import date, datetime, timedelta
from html import escape


MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
SHORT_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def h(value):
    return escape(str(value if value is not None else "—"))


def fit(lines, separator="\n", limit=3900):
    """Keep complete HTML lines under Telegram's text limit."""
    kept = []
    size = 0
    for line in lines:
        addition = len(line) + (len(separator) if kept else 0)
        if size + addition > limit:
            break
        kept.append(line)
        size += addition
    if len(kept) < len(lines):
        kept.append("…")
    return separator.join(kept)


def parse_day(value, reference: date | None = None):
    """Parse API dates including Russian `29 сентября` without a year.

    Infer the year nearest to the requested week, including New Year weeks.
    """
    if isinstance(value, date):
        return value.date() if isinstance(value, datetime) else value
    if not isinstance(value, str):
        return None
    for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            pass
    match = re.fullmatch(r"\s*(\d{1,2})\s+([а-яё]+)\.?\s*", value.casefold())
    if not match:
        return None
    month = MONTHS.get(match.group(2))
    if month is None:
        return None
    anchor = reference or date.today()
    candidates = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidates.append(date(year, month, int(match.group(1))))
        except ValueError:
            pass
    return min(candidates, key=lambda candidate: abs((candidate - anchor).days)) if candidates else None


def day(schedule, target: date):
    return next((row for row in schedule.get("days", []) if parse_day(row.get("date"), target) == target), None)


def lesson_count(number):
    if number % 10 == 1 and number % 100 != 11:
        return f"{number} урок"
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return f"{number} урока"
    return f"{number} уроков"


def progress_bar(completed, total, width=5):
    if total <= 0:
        return ""
    filled = round(width * completed / total)
    return "▰" * filled + "▱" * (width - filled)


def times(timeslot):
    found = re.findall(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d", str(timeslot or ""))
    return found[:2]


def minutes(clock):
    hours, minute = map(int, clock.split(":"))
    return hours * 60 + minute


def lesson_lines(lessons, show_homework=False):
    if not lessons:
        return ["Занятий нет. Можно выдохнуть ✨"]
    result = []
    for index, item in enumerate(lessons, 1):
        slot = "–".join(times(item.get("timeslot"))) or "—"
        result.append(f"<b>{index:02d} · {h(item.get('label') or 'Урок')}</b>  <code>{slot}</code>")
        details = []
        if item.get("cabinet"):
            details.append(f"каб. {h(item['cabinet'])}")
        if item.get("teacherFio"):
            details.append(h(item["teacherFio"]))
        if details:
            result.append("   " + " · ".join(details))
        if item.get("theme"):
            result.append("   📖 " + h(item["theme"])[:300])
        if show_homework and item.get("homeworkBody"):
            result.append("   📝 " + h(item["homeworkBody"])[:700])
    return result


def schedule_view(schedule, target: date, mode="lessons"):
    selected = day(schedule, target)
    title = {"lessons": "📅 Уроки", "homework": "📝 Домашнее задание", "bells": "🔔 Звонки"}[mode]
    lines = [f"<b>{title}</b> · {WEEKDAYS[target.weekday()]}, {target:%d.%m}", ""]
    if not selected:
        lines.append("На этот день BilimClass не вернул расписание. Попробуй выбрать соседний день.")
    elif selected.get("isHoliday"):
        lines.append("Выходной день 🌿")
    elif mode == "bells":
        lessons = selected.get("subjects") or []
        lines.append(f"{lesson_count(len(lessons))} · время из дневника")
        lines.append("")
        for index, item in enumerate(lessons, 1):
            slot = times(item.get("timeslot"))
            lines.append(f"<code>{'–'.join(slot) or '—'}</code>  <b>{index:02d}</b> · {h(item.get('label') or 'Урок')}")
            if len(slot) == 2 and index < len(lessons):
                next_slot = times(lessons[index].get("timeslot"))
                if next_slot:
                    pause = minutes(next_slot[0]) - minutes(slot[1])
                    if 0 < pause <= 60:
                        lines.append(f"   ↳ перемена {pause} мин")
        if not lessons:
            lines.append("Звонков нет.")
    elif mode == "homework":
        homework = [s for s in selected.get("subjects", []) if s.get("homeworkBody") or s.get("hasFiles") or s.get("homeworkBooks")]
        lines.append(f"Задания по {len(homework)} предметам" if homework else "Домашних заданий на этот день нет.")
        for item in homework:
            lines.extend(("", f"<b>{h(item.get('label') or 'Урок')}</b>"))
            if item.get("homeworkBody"):
                body = str(item["homeworkBody"])
                shown = h(body[:800])
                lines.append(f"<blockquote expandable>{shown}</blockquote>" if len(body) > 240 else shown)
            if item.get("hasFiles"):
                lines.append("📎 К заданию прикреплён файл в BilimClass")
            if item.get("homeworkBooks") and not item.get("homeworkBody"):
                lines.append("📚 Есть задание в учебнике")
    else:
        lessons = selected.get("subjects") or []
        lines.append(lesson_count(len(lessons)))
        lines.append("")
        lines += lesson_lines(lessons)
    return fit(lines)


def week_view(schedule, reference: date | None = None):
    anchor = reference or parse_day(schedule.get("date")) or date.today()
    rows = schedule.get("days") or []
    if not rows:
        return "<b>🗓 Неделя</b>\nBilimClass не вернул расписание на эту неделю."
    lines = [f"<b>🗓 Неделя · {anchor:%d.%m}–{(anchor + timedelta(days=6)):%d.%m}</b>", "Выбери день кнопкой ниже, чтобы открыть уроки, звонки или ДЗ.", ""]
    for row in rows:
        subjects = row.get("subjects") or []
        parsed = parse_day(row.get("date"), anchor)
        weekday = SHORT_WEEKDAYS[parsed.weekday()] if parsed else h(row.get("day") or "День")
        date_label = parsed.strftime("%d.%m") if parsed else h(row.get("date"))
        if row.get("isHoliday") or not subjects:
            lines.append(f"<b>{weekday} {date_label}</b> · {'выходной' if row.get('isHoliday') else 'уроков нет'}")
            continue
        homework = sum(bool(s.get("homeworkBody") or s.get("hasFiles") or s.get("homeworkBooks")) for s in subjects)
        first, last = times(subjects[0].get("timeslot")), times(subjects[-1].get("timeslot"))
        span = f" · {first[0]}–{last[-1]}" if first and last else ""
        lines.append(f"<b>{weekday} {date_label}</b> · {lesson_count(len(subjects))}{span} · ДЗ: {homework}")
    return fit(lines)


def dashboard_view(schedule, target: date, now: datetime, progress=None):
    selected = day(schedule, target)
    lines = [f"<b>НЭО · {WEEKDAYS[target.weekday()]}, {target:%d.%m}</b>", ""]
    if not selected:
        lines.append("Расписание на сегодня пока недоступно. Открой неделю или попробуй позже.")
        return fit(lines)
    lessons = selected.get("subjects") or []
    if selected.get("isHoliday") or not lessons:
        lines.append("Сегодня свободный день 🌿")
        return fit(lines)
    lines.append(f"<b>{lesson_count(len(lessons))}</b> сегодня")
    now_minute = now.hour * 60 + now.minute
    current = None
    upcoming = None
    for item in lessons:
        slot = times(item.get("timeslot"))
        if not slot:
            continue
        start = minutes(slot[0])
        end = minutes(slot[1]) if len(slot) > 1 else start + 45
        if start <= now_minute < end:
            current = (item, slot)
        elif start > now_minute and upcoming is None:
            upcoming = (item, slot)
    if current:
        item, slot = current
        lines.append(f"Сейчас · <b>{h(item.get('label') or 'Урок')}</b> до {slot[-1]}")
    if upcoming:
        item, slot = upcoming
        wait = minutes(slot[0]) - now_minute
        if wait <= 60 and now.tzinfo is not None:
            hour, minute = map(int, slot[0].split(":"))
            start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            when = f'<tg-time unix="{int(start.timestamp())}" format="r">через {wait} мин</tg-time>'
        else:
            when = f"через {wait} мин" if wait <= 60 else f"в {slot[0]}"
        lines.append(f"Дальше · <b>{h(item.get('label') or 'Урок')}</b> {when}")
    elif not current:
        lines.append("Уроки на сегодня закончились.")
    homework = sum(bool(item.get("homeworkBody") or item.get("hasFiles") or item.get("homeworkBooks")) for item in lessons)
    if homework:
        if progress is None:
            lines.append(f"📝 ДЗ на сегодня: {homework}")
        else:
            lines.append(f"📝 ДЗ: {progress_bar(*progress)}  {progress[0]}/{progress[1]}")
    lines.append("\nОткрой нужный раздел ниже.")
    return fit(lines)


def mark_lines(marks, limit=20):
    result = []
    for row in reversed(marks):
        for key, label in (("regular", "ФО"), ("sor", "СОР"), ("soch", "СОЧ"), ("po", "ПО")):
            score = row.get(key + "_mark")
            if score is None:
                continue
            maximum = row.get(key + "_max")
            suffix = f"/{h(maximum)}" if maximum is not None else ""
            result.append(f"<b>{h(row.get('subject'))}</b> · {h(row.get('date'))}\n{label}: <b>{h(score)}{suffix}</b>")
            if row.get(key + "_comment"):
                result.append("  💬 " + h(row[key + "_comment"])[:400])
            if len(result) >= limit * 2:
                return result
    return result


def marks_view(marks, period):
    entries = mark_lines(marks)
    return fit([f"<b>Оценки · {period} четверть</b>", *entries], "\n\n") if entries else f"<b>Оценки · {period} четверть</b>\nПока оценок нет."


def subject_marks_view(marks, subject, period):
    selected = [row for row in marks if row.get("subject") == subject]
    entries = []
    for row in reversed(selected):
        scores = []
        comments = []
        for key, label in (("regular", "ФО"), ("sor", "СОР"), ("soch", "СОЧ"), ("po", "ПО")):
            score = row.get(key + "_mark")
            if score is None:
                continue
            maximum = row.get(key + "_max")
            scores.append(f"{label} <b>{h(score)}{'/' + h(maximum) if maximum is not None else ''}</b>")
            if row.get(key + "_comment"):
                comments.append("💬 " + h(row[key + "_comment"])[:250])
        if scores:
            entries.append(f"<b>{h(row.get('date'))}</b> · " + "  ·  ".join(scores))
            entries.extend(comments)
        if len(entries) >= 60:
            break
    title = f"<b>{h(subject)}</b> · {period} четверть"
    return fit([title, *entries], "\n\n") if entries else title + "\nОценок по предмету пока нет."


def checklist_view(tasks, done, target):
    completed = sum(task["key"] in done for task in tasks)
    lines = [f"<b>✅ План ДЗ · {target:%d.%m}</b>",
             f"{progress_bar(completed, len(tasks))}  {completed} из {len(tasks)} выполнено" if tasks else "На этот день заданий нет.", ""]
    for task in tasks:
        lesson = task["lesson"]
        marker = "✅" if task["key"] in done else "○"
        lines.append(f"{marker} <b>{h(lesson.get('label') or 'Урок')}</b>")
        if lesson.get("homeworkBody"):
            lines.append("   " + h(lesson["homeworkBody"])[:160])
        if lesson.get("hasFiles"):
            lines.append("   📎 Есть файлы")
    return fit(lines)


def attendance_view(marks, grades):
    absences = [x for x in marks if x.get("attendance") and x["attendance"] != "was_in_class"]
    sick = sum(x["attendance"] == "missing_by_sick" for x in absences)
    due = sum(x["attendance"] == "missing_due" for x in absences)
    lines = ["<b>Посещаемость</b>", f"За текущую четверть: <b>{len(absences)}</b> пропусков", f"Болезнь: {sick} · уважительная причина: {due} · другое: {len(absences)-sick-due}"]
    if grades:
        lines += ["", "<b>За год по предметам</b>"]
        lines += [f"{h(x.get('subject'))}: {h(x.get('totalMisses') or 0)}" for x in grades if x.get("totalMisses")]
    return fit(lines)


def grades_view(grades, year):
    lines = [f"<b>Табель · {year}/{year+1}</b>", ""]
    for row in grades:
        scores = " · ".join(f"{n}: {h(row.get(k))}" for k, n in (("q1", "1ч"), ("q2", "2ч"), ("q3", "3ч"), ("q4", "4ч"), ("yearScore", "год"), ("finalScore", "итог")))
        lines.append(f"<b>{h(row.get('subject'))[:160]}</b>\n{scores}")
    return fit(lines, "\n\n")


def advice_view(schedule, marks, target):
    tomorrow = day(schedule, target)
    homework = [x for x in (tomorrow or {}).get("subjects", []) if x.get("homeworkBody")]
    lines = ["<b>Что полезно сделать сейчас</b>", ""]
    if homework:
        lines.append(f"📝 На ближайший учебный день есть {len(homework)} заданий. Начни с одного короткого.")
    low = []
    for row in marks:
        for kind in ("regular", "sor", "soch"):
            try:
                if float(row[kind + "_mark"]) / float(row[kind + "_max"]) < .6:
                    low.append(row.get("subject") or "предмет")
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                pass
    if low:
        lines.append("🎯 Повтори темы по: " + h(", ".join(dict.fromkeys(low[-3:]))))
    if not homework and not low:
        lines.append("🌿 Срочных задач не видно. Проверь дневник позже и отдохни.")
    lines.append("\nПодсказки строятся по данным дневника и не заменяют совет учителя.")
    return fit(lines)
