# Neo's autonomous work map

This file is for the next coding agent. Read `AGENTS.md` before making changes. Continue independently on reversible improvements; ask the owner only for secrets, consent to use a real student account, or an irreversible deployment action that has not already been authorized.

## Start each session

1. Check `git status`, recent commits, and open issues. Preserve unrelated edits.
2. Run offline tests. Read the relevant client, view, and store code before editing.
3. Select one concrete product problem, implement it end to end, test, update docs, and commit.
4. Do not use real BilimClass credentials in tests or issue comments.

## Prioritized backlog

1. **API fixtures and contract tests.** Capture sanitized responses with explicit consent. Cover unusual schools, missing weeks, multiple groups, null scores, and archived year data. Replace broad exception swallowing in `client.py` with narrow, privacy-safe errors.
2. **Database migrations.** Add a schema version and forward-only migrations before changing persisted profiles or snapshots. Test upgrading a populated fixture database.
3. **Notification quality.** Include exact subject and changed homework in alerts; cap long text. Add user-selectable quiet hours and daily briefing time. Keep outbox semantics and prevent daily rollover from looking like a schedule edit.
4. **Operational readiness.** Add bounded concurrency, retry/backoff for BilimClass 429/5xx, health signals, and backup/restore instructions. Review rate limits and BilimClass terms before scaling beyond a small private deployment.
5. **Student experience.** Add week navigation, subject-specific mark history, homework checklist, and localized Kazakh copy after validating the underlying data. Keep flows short and accessible.

## API extension recipe

Confirm a capability against the user's parser or a sanitized response. Implement parsing in `client.py`; write a fixture test; add a pure view; wire a private-chat button; decide if it merits a separate opt-in notification; document the field and failure behavior. Avoid guessing endpoint URLs. If the API cannot supply the data, explain that clearly in the interface and roadmap.

## Acceptance gates

- `python -m unittest discover -s tests -v` passes.
- `python -m compileall -q bilim_neo` passes.
- `git diff --check` passes; staged files contain no `.env`, database, or raw records.
- Existing login, `/logout`, navigation, snapshots, quiet hours and retry behavior still work.
