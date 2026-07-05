# Elite Trader

A lightweight Elite Dangerous companion app (a much simpler EDCopilot). It reads the
game's journal files live and shows your current system, station, credits, fuel and
cargo — plus one-click trade routes via Spansh and pre-filled links to Inara/EDSM.

## Run it

Double-click **`run.bat`** (first run creates a `.venv` and installs dependencies —
needs Python 3.10+ installed).

- A desktop window opens with the app.
- The same UI is also served on your LAN: open `http://<this-pc-ip>:8666` from any
  phone/tablet/other PC on your network (the exact URL is printed at startup).
- `run.bat --headless` runs the web server only, with no desktop window.

The first time Windows will show a firewall prompt — allow access on **Private
networks** so other machines can connect.

## Features

- **Current location** — system and station, updated ~2s after in-game events, with
  copy-to-clipboard buttons.
- **Status** — credits, fuel, cargo tonnage and legal state from `Status.json`.
- **Trade routes** — "Find routes" computes multi-hop profit loops pre-filled with
  your ship's cargo capacity, jump range and balance. Two engines:
  - **Local (recommended)** — click *Build Database* on the Market Database panel
    once: the app downloads the Spansh galaxy dump (~3.9 GB, deleted after import;
    ~1-2 GB kept as SQLite in `data/`) and then keeps prices fresh in real time via
    the community **EDDN** feed — the same open data network Inara/Spansh/EDSM
    themselves are built on. Routes are then computed on your machine, no third
    party needed. Fleet carriers are excluded.
  - **Spansh API** — used automatically until the local DB is built, or on demand
    via the Source selector.
- **Quick links** — Inara trade routes/commodities/system and EDSM, pre-filled with
  your current system; opens in your default browser.
- **Station market** — sortable/filterable commodity table (populated when you open a
  station's commodities screen in game).
- **Jump history** — last 20 jumps with distances.
- **Autoplot (◎ buttons)** — click any system to plot a route to it in-game, like
  EDCopilot: the app focuses the game window, opens the galaxy map with your own
  keybind, types the system into search and plots to it. Needs keyboard keys bound
  for Galaxy Map / UI Up / UI Right / UI Select (checked automatically; controller
  binds alone are not enough). Leave the game window alone for ~10 seconds while it
  runs. Timings are tunable at the top of `elite/autoplot.py` if the sequence
  outruns the game on your PC.
- **Open in app** — tick the checkbox on the Quick Links card (desktop window only)
  to open Inara/EDSM results in a window inside the app instead of your browser.
  (Inara's terms don't allow scraping their pages, so results are shown in a real
  embedded browser rather than parsed into the page.)

## Configuration

| Env var          | Meaning                                        | Default |
|------------------|------------------------------------------------|---------|
| `ET_PORT`        | HTTP port                                      | `8666`  |
| `ED_JOURNAL_DIR` | Journal folder override                        | `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous` |

## Security note

The web server has **no authentication** — it exposes your in-game location/credits
to anyone who can reach the port. That's fine on a home LAN; do **not** port-forward
it to the internet.

## Credits

Route planning by [spansh.co.uk](https://spansh.co.uk). Links to
[inara.cz](https://inara.cz) and [edsm.net](https://www.edsm.net). Not affiliated
with Frontier Developments.
