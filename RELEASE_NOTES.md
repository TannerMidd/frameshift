## Frameshift v2.1.0 — local mission control

This is the all-in release: safer tablet access, complete engineering data,
commander-aware storage, an OPS planner and four specialist workspaces, all in
the existing app. **There is no new account, login, API key or companion
service to configure.** Run Frameshift as before; existing data and settings
migrate automatically.

### 🔐 One-scan LAN pairing

- The gaming PC still opens Frameshift automatically on localhost. To add a
  tablet or second computer, scan the one-time QR code in **Settings → Paired
  Devices** or open the link printed at startup.
- Pairing links are short-lived and single-use. A paired device reconnects
  automatically afterwards; it does not need a username or password.
- Pairing address discovery prefers active physical Ethernet/Wi-Fi adapters
  over VPN tunnels and virtual switches. Copied links, QR codes, API responses
  and the startup console all use the same ordered addresses.
- Each device receives a revocable **read**, **control** or **admin**
  capability, so a status display does not need permission to launch Elite,
  plot routes, change settings or pair more devices.
- A fresh browser opens in the touch-friendly **Panel** view by default. An
  explicit Panel/Desktop choice remains saved on that device.
- Cross-site, origin, host, request-size, path and rate-limit protections now
  cover the LAN API as well as the browser UI. Local speech control is POST-only.

### 🧭 OPS — a planner built from your own play

- A compressed, commander-scoped **event ledger** imports journal history
  idempotently and keeps it available for lifetime queries and replay. Journals
  remain local and are never included in support bundles.
- **Learned timings** measure activities from your own journal instead of
  pretending every commander, ship and route takes the same time.
- **Personal objectives** record priority, deadlines, locations, dependencies,
  risk and estimates. The session planner fits the most useful work into a time
  budget and explains its selections, alternatives and provenance.
- **Operations boards** track shared objectives, assignments, resource
  reservations and timestamped contributions. Commanders exchange one bounded
  JSON file; deterministic merges surface conflicts instead of silently
  overwriting work. There is no hosted board or sign-in.
- Local permissioned extension packs can turn matching journal events into
  alerts or objective suggestions. Declarative packs execute no code; advanced
  process adapters require an explicit approval stored by Frameshift outside
  the pack and bound to the reviewed pack's full content hash. Any changed
  pack file automatically returns the adapter to pending approval.

### ▦ Specialist consoles

- **Mining**: automatic or manual runs, refinery-confirmed yield, prospector
  quality, core cracks, limpet usage/cost, conservative commodity-sale
  attribution, yield rate and durable history.
- **Combat / AX**: session kills and AX types, bounty and bond claims, damage,
  synthesis usage and a journal-backed readiness checklist. Ammunition is
  labelled as the last `Loadout` observation because Elite does not journal
  every weapon discharge.
- **Fleet carrier**: authoritative owner snapshots, balance and buy-order
  exposure, explicit weekly-upkeep runway, cargo inventory and a leg-by-leg
  tritium plan. Values unavailable from the journal are requested rather than
  guessed.
- **Exobiology**: a north-up body-local surface map with position, heading,
  journal samples, landing/Codex observations, persistent manual pins,
  colony-range clearance and GeoJSON export.

### 🔧 Complete offline engineering workshop

- Frameshift now ships a validated catalog of **505 source groups, 1,172
  recipes and 369 materials** covering ship blueprints, experimentals,
  synthesis, engineer and technology unlocks, and Odyssey suit/weapon upgrades
  and modifications. It needs no runtime download, login or API key.
- One shared wishlist plans multiple items against material, cargo and Odyssey
  locker inventory. Current-to-target grade costs, quantities, application
  counts, material sources and engineer access are explicit.
- Deficits use material-trader grade and family rules, reserve inventory once
  across the whole wishlist, and never suggest invalid commodity or Odyssey
  trades. Existing pinned blueprints migrate to stable catalog entries
  automatically.

### 💹 More honest market advice

- Trade loops, routes, commodity results, mining buyers and cargo-sale results
  now expose **confidence and provenance** based on price age, available depth
  and the 25% bulk-sale threshold, with conservative payout/profit ranges.
- Loop results distinguish steady-state profit/hour from the first trip, which
  includes the time needed to position at the starting station.
- **Cargo recovery** excludes a failed or drained destination and finds a
  recommended nearby buyer plus alternatives for the hold already aboard.

### 💾 Commander data that is not disposable

- History, watches, objectives, ledger entries and workflow records now live in
  a separate `data/commander.db`; `market.db` is only the replaceable galaxy
  cache. Existing user tables — including future or locally added tables — are
  migrated transparently through a validated candidate and a compact backup.
- Profiles switch automatically from the active journal commander. The same
  commander name in **Live and Legacy is deliberately isolated**, so credits,
  history and plans cannot bleed between galaxies.
- Live community-market and route endpoints now **fail closed in Legacy** with
  an explanation. Local journal history and specialist tools remain available,
  but Frameshift will not present Live prices as Legacy advice.
- Database reseeds build and validate a complete candidate before promotion,
  preserve commander data and backups, and replay fresher EDDN changes received
  during the long import. Empty, truncated or implausibly small builds never
  replace a healthy cache.

### 🌐 Anonymous community contribution

- Frameshift still requires no third-party account. Anonymous commodity-market
  contribution keeps its existing default; a separate, default-off informed
  opt-in can contribute outfitting, shipyard, navigation, exploration, Codex
  and biological-signal observations through EDDN's public schemas.
- Reports include game version/build and location context where the schema
  requires it. Event-specific root and nested allowlists keep unknown and
  commander-local fields out, Legacy reports remain distinguishable, and
  either contribution class can be disabled in Settings.

### 🩺 Diagnostics, releases and recovery

- Rotating local logs and a one-click **Support Bundle** make background issues
  diagnosable. The bounded ZIP includes health, sanitized settings and logs —
  never journals, commander names, pairing secrets or either database. Pairing
  query values, authorization headers and cookies are scrubbed from copied logs.
- Settings writes are atomic and recover a corrupt file to a backup instead of
  silently discarding it.
- Game presence uses native Windows process enumeration, is checked before
  journal/database bootstrap, and never treats a historical `Shutdown` replay
  as proof that the currently running game is offline.
- Packaged updates now require a matching same-release SHA-256 sidecar over
  validated HTTPS GitHub redirect targets, enforce download bounds and create
  a rollback executable before replacement. The digest detects corruption and
  truncation but is not an independent publisher signature. Rollback is kept
  through a sustained healthy replacement launch and a later successful
  startup; a missing or malformed checksum stops the update.
- Dependencies and third-party workflow actions are pinned. Continuous
  integration compiles and tests the app
  on Windows and Linux, checks browser JavaScript, and the Windows release job
  smoke-tests the packaged executable before publishing both product names and
  their checksums.

## Frameshift v2.0.0 — new name, whole galaxy

**Elite Trader is now Frameshift.** The app long ago outgrew its trading
roots — it navigates, explores, engineers and fights too — so the name now
matches what it is: the companion computer for everything your Frame Shift
Drive points at. **Nothing changes for you**: your data, settings, layouts and
themes carry over untouched, auto-update keeps working (your exe file keeps
its old name until you re-download — that's fine), and the GitHub project
redirects from the old address.

### ⚑ New GALAXY page — the background sim, finally on deck

- **Powerplay 2.0** — your pledge, rating and merits (with a session tally),
  plus the current system's power status on every jump: controlling power,
  control-progress bar, reinforcement vs undermining this cycle.
- **System factions (BGS)** — influence bars, active/pending/recovering
  states, controlling faction, and your reputation with each — refreshed the
  moment you jump in.
- **Conflicts** — wars and elections: who's fighting whom, what station or
  settlement is staked, and the days-won score.
- **Community goals** — goals you've joined, with your contribution, reward
  tier, percentile band and expiry countdown.
- **Squadron** — your squadron and rank, when the game reports one.
- Every card teaches: if a section is empty, it explains that corner of the
  galaxy's politics instead of showing a blank.

### 🛡 Trust & safety hardening

- **EDDN uploads now carry your game version**, so the network can tell Live
  data from Legacy — and the live price feed now **filters out Legacy-galaxy
  messages** instead of letting them poison Live prices.
- **The web server now rejects cross-site browser requests** (and DNS-rebinding
  Host tricks): a random web page can no longer poke your companion's API.
  Tablets and everything else on your LAN work exactly as before.
- **Database rebuilds are now crash-proof**: the new database is built to the
  side and swapped in only when it's complete — a mid-rebuild crash can no
  longer leave you with a gutted market database.
- **"Exclude fleet carriers" now tells the truth**: the setting controls what
  the database collects (live feed and rebuilds), not just what searches show.

### 🛠 Under the hood

- Releases now run the full test suite before building.
- The release publishes the exe under both names (`Frameshift.exe` and
  `EliteTrader.exe`) so every existing install keeps auto-updating.
