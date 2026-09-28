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

The student sends credentials in a private chat. The FSM holds them only until login succeeds. The bot attempts to delete both input messages. The store then encrypts the credentials and derived student data using a deployment-specific Fernet key. Each action creates a fresh BilimClass client and logs in; this favors simple recovery over keeping short-lived remote tokens. Token caching may be added after its lifetime and refresh contract are verified.

Every background cycle derives the active quarter from `get_periods()`, reads current marks, tomorrow's diary and today's lessons, compares snapshots, and queues relevant messages. The first snapshot creates a baseline. A separate 30 second tick looks for bell reminders in cached lessons and delivers queued messages. An outbox row is deleted only after a successful `send_message`. Quiet hours pause checks and delivery; after quiet hours, comparison resumes. Manual views always fetch fresh data.

Homework and schedule snapshots carry `schema: 2`. The older snapshot format was created before localized day dates were parsed and may contain false empty days. On upgrade, the worker treats such snapshots as a new baseline to avoid announcing old assignments as fresh changes.

`POLL_INTERVAL_SECONDS` defaults to 1800 with a hard minimum of 300. At one login and several API calls per user per interval, SQLite and sequential checks are suitable for a small private deployment. Before large-scale use, introduce bounded concurrency, backoff and per-school rate limiting, a proper migration tool, health metrics, and an explicit retention policy. An API field can be null or absent; renderers must handle both.

## UX rules

The interface is a compact school notebook: one message per useful answer, bold section title, time and subject first, supporting details below. The home keyboard has five paired rows grouped by task: day, week/bells, homework/marks, report/attendance, advice/settings. Details have a back action. Empty days, missing marks, authentication errors and upstream downtime use direct language. Do not disclose student identifiers such as IIN in the chat.
