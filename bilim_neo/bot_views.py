"""Pure formatting and analysis helpers for the Telegram interface."""

from datetime import date, datetime
from html import escape


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


def parse_day(value):
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return None


def day(schedule, target: date):
    return next((row for row in schedule.get("days", []) if parse_day(row.get("date")) == target), None)


def lesson_lines(lessons, show_homework=False):
    if not lessons:
        return ["Занятий нет. Можно выдохнуть ✨"]
    result = []
    for index, item in enumerate(lessons, 1):
        result.append(f"<b>{index:02d} · {h(item.get('label') or 'Урок')}</b>  <code>{h(item.get('timeslot') or '—')}</code>")
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
    title = {"lessons": "Расписание", "homework": "Домашнее задание", "bells": "Звонки"}[mode]
    lines = [f"<b>{title} · {target:%d.%m.%Y}</b>", ""]
    if not selected:
        lines.append("В дневнике нет данных на этот день.")
    elif selected.get("isHoliday"):
        lines.append("Сегодня выходной 🌿")
    elif mode == "bells":
        lines += [f"{i:02d}  <code>{h(s.get('timeslot') or '—')}</code>  {h(s.get('label') or 'Урок')}" for i, s in enumerate(selected.get("subjects", []), 1)] or ["Звонков нет."]
    elif mode == "homework":
        homework = [s for s in selected.get("subjects", []) if s.get("homeworkBody")]
        lines += [f"<b>{h(s.get('label') or 'Урок')}</b>\n{h(s['homeworkBody'])[:900]}" for s in homework] or ["Домашних заданий на этот день нет."]
    else:
        lines += lesson_lines(selected.get("subjects", []))
    return fit(lines)


def week_view(schedule):
    lines = ["<b>Неделя в школе</b>", ""]
    for row in schedule.get("days", []):
        subjects = row.get("subjects") or []
        lines.append(f"<b>{h(row.get('day') or row.get('date'))} · {h(row.get('date'))}</b>  {len(subjects)} уроков")
        if subjects:
            lines.append("  " + " · ".join(h(s.get("label") or "Урок") for s in subjects)[:220])
    return fit(lines or ["Нет расписания."])


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
