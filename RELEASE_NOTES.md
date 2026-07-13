## Frameshift v2.1.2 — commander profile repair

A focused patch: your local history now has a visible owner, a repair tool,
and error messages that never leak internals. As always there is no account,
API key, hosted service or new setup step.

### New: COMMANDER PROFILES card (Settings)

Frameshift stores analytics, watches and workflow history per commander,
matched from your journal. Test sessions, borrowed accounts or an upgrade from
2.0 could leave some of that history under the wrong owner — previously with
no way to see or fix it. The new card shows:

- every commander profile this machine has seen, with its local data
  footprint and when it was last active;
- an **UNASSIGNED HISTORY** banner when records exist from before Frameshift
  knew your commander name (pre-2.1 history, or journal files whose owner
  could not be determined). One tap assigns it all to your commander — it
  merges safely and duplicates are skipped;
- **ACTIVATE** to switch the visible commander (the journal switches it back
  automatically at your next login), and **DELETE** to remove a stale or test
  profile together with every local record it owns.

Guardrails: the active profile and the unassigned bucket cannot be deleted, a
commander-data backup is written before any delete, and nothing in-game is
ever touched.

### Hardening and polish

- API error messages now come only from text written for the player;
  unexpected internal errors return a generic line instead of raw exception
  detail. The local health endpoint reports error categories rather than full
  messages (which could include file paths).
- Journal-folder validation keeps its symlink-escape protection and adds a
  second containment check, with every filesystem probe inside the verified
  boundary.
- The README now credits the bundled open-source components (EDEngineer
  reference data, Project Nayuki's QR generator, Piper TTS), with full license
  texts in THIRD_PARTY_NOTICES.md.

### Upgrade notes

- Update normally from any 2.x release; no settings reset or manual database
  work is required.
- Existing journals, commander data, paired devices and local extensions are
  preserved.
- If you upgraded from 2.0 and your pre-2.1 analytics seemed to vanish, open
  **Settings → COMMANDER PROFILES** — they are waiting under UNASSIGNED
  HISTORY.
