## Frameshift v2.3.0 — the tactical status page

The flight panel's home page and rail got a full redesign — more readout,
less furniture, same touch targets.

### Command header

The system readout is now a proper cockpit plate: your current system large
and glowing, a dock-state chip, and — when you're following a plotted
route — a segmented progress bar right in the header showing the next
waypoint. On the right, ship telemetry: credits, rebuy, and legal state,
with a new **coverage line** under the rebuy — `COVERS 21×` in green, amber
when you're under two rebuys, red when you can't afford to lose the ship.

### Readouts that advise, not just report

- **Fuel** shows ≈how many jumps your tank holds at your recent burn rate,
  and whether the star here (or the next one on your route) is scoopable.
- **Cargo** shows free tonnage, or "hold empty — ready for loop cargo".
- **Unbanked data** gets its own card: exploration value with body count,
  bio samples with species count.
- **Data at risk** is now a full-width hazard banner when your unsold data
  is worth many rebuys, with a pointer to the nearest place to bank it.
- The status strip shows your in-game destination with **jumps left**, and
  a quiet footer reports link health and telemetry time.

### The rail

Sharper and more legible: an accent header bar, the active page marked with
hazard striping and a cut corner, and boxed utility buttons. Everything —
rail, strip, page — follows your chosen color theme, and the ambient CRT
effects remain opt-in in Settings.

### Upgrade notes

- Update normally from any 2.x release. No database changes.
