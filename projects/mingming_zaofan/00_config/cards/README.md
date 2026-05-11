# Cards directory

- `characters/<slug>.json` — character cards (traits, goals, relationships, secret).
- `events/<slug>.json` — event cards (cause, process, status, future direction).

Cards are read by the writer before each chapter and updated automatically after each chapter.
See longbookwritter/cards/store.py for the schema. Hand-edit JSON freely; missing fields fall back to safe defaults.
