# Architecture

```text
BilimClass API → client.py → bot.py ──────────→ Telegram private chat
                                │
                                ├─ bot_views.py (safe HTML)
                                ├─ automation.py (clock rules)
                                └─ bot_store.py (encrypted SQLite + outbox)
```

The existing BilimClass parser was copied into this repository so deployment and future changes are independent of `bilimclass-api`. It supports profile, weekly diary, period list, journal marks/attendance, and year report. `client.py` has synchronous requests and 15 second timeouts. Bot handlers and background sync run it in a thread, leaving the Telegram event loop responsive.

The diary returns localized day dates such as `29 сентября` without a year, while its query uses `29.09.2026`. `bot_views.parse_day()` resolves the localized date against the requested week, including weeks that cross New Year. Keep this normalization when adding views or notifications; a raw string comparison silently hides lessons, bells and homework.

Homework file metadata is fetched on demand from `clientoffice/homeworks/simple-homework/info`. The file menu stores only short callback indexes; a send action resolves them against a fresh diary and metadata response, so signed storage links never enter Telegram callbacks or SQLite. `attachments.py` validates the observed storage host and size, then streams HTTPS chunks through aiogram's multipart upload with redirects disabled and a 49 MB ceiling. No local file or cache is created. A student must choose each document explicitly.

The local homework checklist is derived from the diary by `planner.py`. It uses an opaque hash of lesson position and homework UUID (or label fallback), and stores completion keys in the existing encrypted snapshots table. A toggle verifies the key against a fresh schedule before writing, avoiding stale buttons marking a different task. Completion never changes BilimClass itself. Subject-specific marks are filtered from the same quarter data as the all-marks view; no extra endpoint or stored copy is required.

`prefs.planner` controls only the checklist UI and home progress; it does not change homework alerts or erase completion data. The menu uses Bot API inline button styles for a restrained primary/complete hierarchy. The dashboard uses a `tg-time` relative entity for a nearby lesson, and long homework text uses an expandable blockquote. Older Telegram clients may ignore the presentation enhancement while keeping the text and callbacks usable. Native Telegram checklists require a connected business account, so they are unsuitable for this private student bot.

The student sends credentials in a private chat. The FSM holds them only until login succeeds. The bot attempts to delete both input messages. The store then encrypts the credentials and derived student data using a deployment-specific Fernet key. Each action creates a fresh BilimClass client and logs in; this favors simple recovery over keeping short-lived remote tokens. Token caching may be added after its lifetime and refresh contract are verified.

Every background cycle derives the active quarter from `get_periods()`, reads current marks, tomorrow's diary and today's lessons, compares snapshots, and queues relevant messages. The first snapshot creates a baseline. A separate 30 second tick looks for bell reminders in cached lessons and delivers queued messages. An outbox row is deleted only after a successful `send_message`. Quiet hours pause checks and delivery; after quiet hours, comparison resumes. Manual views always fetch fresh data.

Homework and schedule snapshots carry `schema: 2`. The older snapshot format was created before localized day dates were parsed and may contain false empty days. On upgrade, the worker treats such snapshots as a new baseline to avoid announcing old assignments as fresh changes.

`POLL_INTERVAL_SECONDS` defaults to 1800 with a hard minimum of 300. At one login and several API calls per user per interval, SQLite and sequential checks are suitable for a small private deployment. Before large-scale use, introduce bounded concurrency, backoff and per-school rate limiting, a proper migration tool, health metrics, and an explicit retention policy. An API field can be null or absent; renderers must handle both.

## UX rules

The interface is a compact school notebook: one message per useful answer, bold section title, time and subject first, supporting details below. The home keyboard groups day, week/bells, homework/marks, report/attendance, plan/advice and settings. Details have a back action. Empty days, missing marks, authentication errors and upstream downtime use direct language. Do not disclose student identifiers such as IIN in the chat.
