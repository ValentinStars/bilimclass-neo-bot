# НЭО / Табло

The visual mode uses a school timetable board: charcoal `#151916`, warm white `#f2f0e5`, lime `#d4f66a`, muted text `#9ca69b`. Every section has a clear title and a small index, with a restrained grid at the right edge. Avoid gradients, ornamental glass panels and marketing paragraphs.

`visuals.py` draws a 1200×680 PNG with DejaVu Sans, a large heading and at most five summary lines. Pillow binary-searches line truncation so unusually long homework cannot cause quadratic font measurement. The image is a summary, not the sole source of information. `send_screen()` retains complete HTML as caption where it fits and sends a separate text message when it exceeds 1,000 characters. The keyboard stays with that text. Photo-to-photo navigation edits media in place; switching to compact or coming from a GIF sends a new text panel.

Animation is a 1.8-second moving underline in the opening screen, cached once in memory without personal data. Normal navigation uses static cards. Users can disable motion before login or from settings. The compact mode avoids media entirely after onboarding. Existing users are not switched automatically.

Fonts must be installed at `/usr/share/fonts/truetype/dejavu/`; the Dockerfile installs `fonts-dejavu-core`. Static demonstration artwork can be committed, but personalized cards, credentials, real screenshots and feedback never belong in assets. Admin charts use the same palette. Test long content, Cyrillic, no-data states and back navigation from photos/GIFs before changing delivery.
