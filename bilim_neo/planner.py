"""Stable homework checklist identities and small progress summaries."""

import hashlib

from .bot_views import day


def tasks_for_day(schedule, target):
    selected = day(schedule, target) or {}
    tasks = []
    for index, lesson in enumerate(selected.get("subjects") or []):
        if not (lesson.get("homeworkBody") or lesson.get("hasFiles") or lesson.get("homeworkBooks")):
            continue
        identity = str(lesson.get("homeworkUuid") or lesson.get("label") or "Урок")
        key = hashlib.sha256(f"{index}:{identity}".encode()).hexdigest()[:20]
        tasks.append({"index": index, "key": key, "lesson": lesson})
    return tasks


def task_progress(tasks, done):
    completed = sum(task["key"] in done for task in tasks)
    return completed, len(tasks)
