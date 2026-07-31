## Frameshift v2.7.0 — a tripwire for the oldest question in the galaxy

Raxxla is Elite's longest-running unsolved legend: a world from the 1984
original that has never been proven to exist. This release arms Frameshift to
notice if that ever changes — from your own cockpit, and from the rest of the
galaxy at once.

### The Raxxla Scanner

**Settings → Extension Builder** ships a new starter template that fills in
five rules and saves as an ordinary local extension pack you can edit or delete
like any other:

- **Arrival tripwire.** Jumping into — or logging in inside — a system named
  Raxxla raises a critical cockpit alert with a spoken callout.
- **Undiscovered black hole.** A black hole nobody has scanned before, which is
  where the legend has always pointed.
- **New codex anomaly.** A geology-and-anomalies codex entry that is new to you,
  so it is worth checking whether it is new to anyone.
- **First-discovery Earth-like.** Raxxla is said to be one.

The last three fire rarely by design. They were tuned against real recorded
journal history rather than guesswork, so they stay quiet on an ordinary
exploration run instead of crying wolf.

### Galaxy watch

An extension only ever sees your own journal. The galaxy watch covers the other
case: it reads system names off the live EDDN feed Frameshift already listens
to, so a watched system announces itself even when a different commander is the
one who reaches it.

- Watch any system, not just the default. Set `galaxy_watch_systems` in your
  settings file to a list of names.
- Each name alerts once per run, and the Local Health line shows what is being
  watched so you can tell it is armed.
- Only game-assigned system names are read — never station or fleet-carrier
  names, which commanders choose themselves and could otherwise be used to
  trigger the alert for everyone.

### More to build with

- The Extension Builder learned four new things to watch: **Location**,
  **Codex entry recorded**, **Signal source detected**, and star type on
  **Body scanned** — with the usual `{Field}` insert chips.
- Templates can now carry more than one rule, which is what lets a single
  button set up a whole scanner.

### Fixes

- The **＋ CONDITION** button in the Extension Builder rendered as garbled
  characters. The repository's own mojibake guard had a gap that let this
  through; the guard now covers that range and the case is pinned by a test.

### Upgrade notes

- Update normally from any 2.x release. No database schema changes and no
  manual migration steps.
- The galaxy watch is on by default and costs nothing measurable: it reads
  messages the EDDN listener already decodes and discards.
- The Raxxla Scanner is opt-in — nothing is installed until you open the
  Extension Builder and save it.
