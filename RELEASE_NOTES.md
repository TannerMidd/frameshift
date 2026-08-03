## Frameshift v2.9.1 — the update your antivirus ate

If you clicked **UPDATE** and got this, the update never had a chance:

```
↑ UPDATE   Update failed: could not create a rollback copy — update cancelled
```

Frameshift takes a backup of itself before swapping in a new build, so a
replacement that won't start is always recoverable. Until now that backup was
written as `Frameshift.old.exe` — a byte-identical copy of the running program,
saved next to it, under an `.exe` name.

That is also a fair description of how self-replicating malware behaves, and
Windows Defender scored it accordingly: `Trojan:Win32/Wacatac.B!ml`, deleted
mid-write, `WinError 225`. The updater found its own backup missing, concluded
it could not guarantee a rollback, and cancelled — correctly, but for a reason
it had no way to explain.

### The backup is no longer an executable

It is now `Frameshift.rollback.bak`. Same bytes, same purpose, no `.exe`
extension for a scanner to take an interest in.

If a future update ever does fail to start, the recovery is unchanged in spirit
but new in detail: rename `Frameshift.rollback.bak` back to `Frameshift.exe`.

### Failures now say what went wrong

Every filesystem error during an update used to collapse into one sentence, so
antivirus interference, a full disk, and a locked file were indistinguishable —
and the log recorded only the exception's type name, throwing away the error
code that would have identified it in seconds.

Update failures now name the cause and, where there is one, the fix:

```
Update failed: could not create a rollback copy — your antivirus blocked it
(WinError 225) — allow Frameshift in your antivirus, or install the update
manually from the releases page
```

### Old backups get cleaned up

Two related leftovers are now swept on the first healthy launch after an update:

- **`Frameshift.old.exe` files from previous versions.** Roughly 18 MB each, and
  previously only removed if the filename matched exactly.
- **`Frameshift.old.old.exe`.** If a replacement ever failed to start and you
  double-clicked the backup rather than renaming it, that install kept running
  as `Frameshift.old.exe` — and every later update stacked another `.old` on the
  backup it made. Nothing recognised those, so they accumulated. Backups are now
  named from the install's *base* name, so the chain cannot grow, and the strays
  are cleaned up.

Cleanup never touches the executable you are currently running, and only ever
removes filenames Frameshift could have written itself — it will not go
rummaging through the folder it is installed in.

### Honest scope

This removes Frameshift's own contribution to the problem: it no longer writes a
second executable next to itself during an update. It cannot change how your
antivirus judges the downloaded exe, and Windows may still warn on first launch —
the build is unsigned, and unsigned installers with low download counts are
exactly what these heuristics are tuned to flag. Code signing is the real fix and
is not in this release.

If Defender does quarantine Frameshift, it is a false positive, and reporting it
through Microsoft's submission portal helps clear the verdict for everyone.

### Upgrade notes

- Update normally from any 2.x release. No database changes, no migration.
- If the in-app update is being blocked on your machine, download `Frameshift.exe`
  from this release and replace your existing copy — your `data` folder, settings
  and pairings are untouched by a manual swap.
- Installs currently running as `Frameshift.old.exe` will keep that filename; it
  works fine, but renaming it back to `Frameshift.exe` while Frameshift is closed
  will tidy up the naming for good.
