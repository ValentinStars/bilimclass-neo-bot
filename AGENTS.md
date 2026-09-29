# Agent guide: НЭО

Read this first, then `docs/NEO.md` for the autonomous roadmap and `docs/ARCHITECTURE.md` for data flow. The user wants a complete standalone Telegram product. Do not add a dependency on the sibling `bilimclass-api` repository.

## Product contract

НЭО serves an individual BilimClass student in a private Telegram chat. The primary action is checking today's school day. Only new grade alerts are enabled by default; homework, schedule, attendance and reminder categories require opt-in. Messages should be concise, friendly, and useful: title, concrete fact, next action. Avoid generic praise, excessive emoji, and walls of text. Use inline keyboards for navigation, native commands for shortcuts. Every state needs a clear way back.

## Files and commands

- `bilim_neo/client.py`: sync BilimClass HTTP client. This is the sole source of API parsing. Anonymize any captured fixture.
- `bilim_neo/attachments.py`: validate file metadata and stream an attachment directly to Telegram in bounded chunks. Do not persist files or signed links, log signed URLs, or loosen the storage host check without live evidence.
- `bilim_neo/planner.py`: derive homework checklist tasks and stable opaque keys from diary data. Completion state lives encrypted in the `homework_done` snapshot, trimmed to 30 dates. Callback keys must match a fresh diary result before toggling.
- `bilim_neo/bot.py`: aiogram 3 router, auth FSM, notification workers. Call sync I/O using `asyncio.to_thread`.
- `bilim_neo/bot_views.py`: pure rendering. Escape all external text with `h()` and keep Telegram messages under 4096 characters.
- `bilim_neo/automation.py`: pure scheduling logic. Use aware `Asia/Almaty` datetimes.
- `bilim_neo/bot_store.py`: encrypted SQLite state and outbox. Evolve schema with migrations before changing stored shapes on a deployed bot.
- `bilim_neo/admin_panel.py` and `admin_tools.py`: allowlisted admin UI, audience matching, read-only metrics, in-memory exports and PNGs. Check effective admin access (allowlist or current recovery session) **and** `prefs.admin_enabled` at every entry; broadcasts require preview and confirmation. Never log or cache a credential export.
- `bilim_neo/lord_bonus.py`: public Google Sheet discovery and A7 read. The student's class must come from BilimClass profile; reject non-LORD schools and never accept arbitrary gids from callbacks. Admin global switch is `app_settings.lord_bonus_enabled`.
- `bilim_neo/experience.py`: onboarding, appearance selector, Telegram identity middleware and feedback inbox/replies. State handlers must check private chat and re-check current admin access before delivering a reply.
- `bilim_neo/visuals.py`: compact Pillow cards; see `docs/DESIGN.md`. Grade chips use the actual BilimClass score/maximum, with neutral color when maximum is absent. Keep full grade history reachable by button and long diary text readable; never cache personal images or write them to disk.
- `bilim_neo/recovery.py`: local recovery account with deployment-only login/hash/profile. Never send this identity to BilimClass or save its plaintext password. `admin_tools.allowed(chat_id, store)` accepts either an allowlisted ID or an unexpired recovery session; the admin preference is still required.
- Run: `pip install -e .`, `python -m bilim_neo`; verify: `python -m unittest discover -s tests -v` and `python -m compileall -q bilim_neo`.
- Runtime: `manage.sh` and `configure.sh` provide interactive setup without echoing the token. `start.sh`, `stop.sh`, `restart.sh`, `status.sh`, `logs.sh` manage a local daemon or an installed user systemd service. `scripts/deploy.sh` copies code without secrets. `scripts/apply_brand.py` uploads the committed JPG avatar.

## Hard rules

1. Never commit `.env`, tokens, passwords, raw student records, or the SQLite database. Do not log complete HTTP responses, request headers, or credentials. Keep errors shown to students generic.
2. Do not send personal diary data to groups. Keep `protect_content=True` on messages with diary data.
3. New change notifications need a baseline so old events do not look new. Enqueue before advancing the snapshot; mark an outbox item sent only after Telegram confirms sending. Treat an unavailable API as an error, not an empty diary.
4. Keep quiet hours and each notification preference respected. Bell reminders must not be sent after their useful moment has passed.
5. The API is unofficial. Add a fixture and tests for each newly discovered response shape. Do not invent endpoint support or claim a field exists without evidence.
6. Prefer small reversible commits. Run tests and inspect `git diff --check` before committing. Update README and this guide when behavior changes.
7. Homework attachments come from `GET /api/v4/os/clientoffice/homeworks/simple-homework/info` with `homeworkUuid`, `schoolId`, `eduYear`. Live responses contain `files[]` entries with `name`, `extension`, `sizeInBytes`, and short-lived `link` on `storage.yandexcloud.kz`. The bot resolves list/send callback indexes against fresh diary data and streams after a deliberate tap. Keep the 49 MB cap and private-chat check.
8. Keep homework completion separate from BilimClass data: the checklist is a local planning aid, not a submission to the school. Do not label a task submitted or completed in BilimClass when it is merely checked locally.
9. `prefs.planner` is a view preference, separate from `prefs.homework` notifications. When off, hide planner entry points and dashboard progress, reject old `task:` callbacks, and keep encrypted completion state so re-enabling restores it. The UI uses Bot API button `style`, `tg-time`, and expandable blockquotes; keep plain text fallbacks and tests.
10. `prefs.button_colors` defaults on and controls *every* keyboard for that student. Route new inline buttons through `buttons(..., prefs=user["prefs"])` or a keyboard helper that accepts prefs; keep a single semantic style mapping in `action_style()` and verify color-off removes all styles, including explicit ones. Existing sent messages retain their old markup until navigated or edited.
11. Referral links are HMAC signed. Credit only a first authenticated registration; pending visits alone do not count. Do not reveal credentials, Telegram ID or diary content in inviter notices.
12. The admin export of `login,password,class` is deliberately sensitive. Keep it restricted to configured IDs or valid recovery sessions, require in-panel confirmation, construct it only in memory, and send only to the owner's private chat. Never exercise this export in live testing. Test with fake credentials.
13. Recovery access is authorized by the owner as a second admin entry. Keep PBKDF2 at 600,000 iterations, per-chat and global rate limits, a 12-hour expiry and hash-bound session revocation. Do not grant access just because a stored profile says `local_admin`. That flag only suppresses BilimClass requests. `/logout` must remove the session.
14. Appearance choices and current Telegram username live encrypted in `visitors` before registration. Existing users default to compact. Feedback content is encrypted with a 90-day retention and deleted on `/logout`; media is copied on demand from Telegram, never downloaded. Only a verified admin may reply, and the recipient comes from the saved ticket.
15. `notification_defaults_v2` is a one-time migration: enable marks, disable homework/schedule/attendance/morning/bell/weekly, and remove queued alerts of disabled categories. Keep it one-time so later opt-ins survive restarts. Every outbox notification gets a short italic settings footnote; Telegram offers no smaller font size for bot messages.

## Definition of done for a feature

Implement the client field or pure rule, wire a private-chat view and navigation, cover its failure/empty state, test it offline, document how it works, then check that no secret or student data is staged. For production features, also consider restart behavior, duplicate notifications, API throttling, and timezone boundaries.
