## Frameshift v2.1.4 — unassigned history stays assigned

Fixes the loop where the COMMANDER PROFILES card showed the same unassigned
records again after every restart, no matter how many times you assigned
them.

### What was happening

Elite writes a journal file every time the game launches — including launches
where no pilot ever logs in (opened to the menu, then closed). Those stub
files contain no commander name, so Frameshift correctly refuses to guess an
owner and files their handful of records as unassigned.

The bug: assigning that history to your commander also moved the internal
"this file was already processed" markers. On the next start, the journal
sweep no longer remembered the stub files, imported them again, and the same
records reappeared as unassigned — forever.

### What changed

- Assigning unassigned history now moves only the history itself. The
  per-file processing markers stay put, so assigned records stay assigned
  across restarts.
- Internal bookkeeping rows are no longer counted as "records" in the
  unassigned banner — the number you see is now only real history (the count
  you see may drop after updating; nothing was lost).
- Login-less stub journals (menu open/close, launcher crash) are no longer
  ingested at all. They contain nothing but session chrome, so future stubs
  will not feed the unassigned bucket in the first place.

### Upgrade notes

- Update normally from any 2.x release.
- If the banner still shows unassigned records after updating, assign them
  once — this time they will stay with your commander.
