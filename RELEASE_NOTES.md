## Elite Trader v1.10.1 — security hardening

A small, focused release: GitHub's CodeQL security scan was run against the
whole codebase and **every finding (19) is fixed**. No features changed —
everything from v1.10.0 works exactly as before.

### 🔒 What was hardened
Elite Trader serves its UI to your whole LAN (that's how the tablet panel
works), so the API deserves the same care as a public web app:

- **Error messages can never leak internals.** Only messages written for the
  player are ever sent back by the API; anything unexpected is logged on the
  machine running the app and the client just sees a generic error.
- **No more path probing via the journal-folder check.** The live "is this
  folder right?" validation in Settings now only inspects places a journal
  folder can plausibly live (your user profile / Saved Games / the
  auto-detected folder). Anything else shows as "can't check from here" —
  SAVE still works for exotic setups.
- **A search-query regex could be made slow on purpose** (a classic
  denial-of-service trick); it now runs in linear time no matter what's
  typed into module search.

### 🐛 Also
- Update-check errors now say what went wrong by name (e.g. `ConnectTimeout`)
  instead of dumping a wall of connection internals into the Settings panel.

---

Run from source (`run.bat` / `run.sh`) or grab the attached `EliteTrader.exe`
(no Python needed). On the packaged app, updates install in place — click
**Update & restart** when it appears.
