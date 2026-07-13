<div align="center">

<img src="assets/logo.png" alt="Frameshift" width="520">

**A local, all-in-one Elite Dangerous companion for desktop and cockpit displays.**

![Latest release](https://img.shields.io/github/v/release/TannerMidd/frameshift?color=orange)
![License: MIT](https://img.shields.io/badge/license-MIT-orange)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Platform: Windows | Linux](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)
![AI: Fable 5 + GPT 5.6 SOL Ultra](https://img.shields.io/badge/AI-Fable%205%20%2B%20GPT%205.6%20SOL%20Ultra-8A2BE2)

> **Disclaimer:** this codebase was **AI-generated with Claude (Fable 5) and GPT 5.6 SOL Ultra**,
> directed and play-tested by me against my own live game. It's a personal
> project built for my own use — shared as-is, and anyone is welcome to use it.

[**Download**](https://github.com/TannerMidd/frameshift/releases/latest) ·
[**Website**](https://tannermidd.github.io/frameshift/) ·
[**Documentation**](../../wiki)

<img src="docs/screenshots/frameshift-panel-hero.png" alt="Frameshift flight panel on a tablet" width="850">

</div>

Frameshift reads Elite Dangerous journals in real time and combines them with
a local galaxy-market database. The same app serves its desktop interface and
any paired browser on your home network, including a mounted tablet or laptop.

No Frameshift account, third-party sign-in, or API key is required. Community
market and route features use public EDDN and Spansh data; commander history,
planning, settings, and extensions remain on your machine.

## Quick start

### Windows

1. [Download the latest release](https://github.com/TannerMidd/frameshift/releases/latest)
   and run `Frameshift.exe`.
2. Play Elite Dangerous. Frameshift detects the journal folder and active
   commander automatically, including relocated Windows Saved Games folders.
3. Open **Settings → Build Database** once to enable galaxy-wide market and
   station searches. Build time varies; progress is shown in the app and EDDN
   keeps the finished database fresh.

The packaged Windows app stores its data beside the executable and can update
itself after showing the new release notes.

### From source

Python 3.12 is the tested development version.

```powershell
git clone https://github.com/TannerMidd/frameshift.git
cd frameshift
.\run.bat
```

On Linux or Steam Deck, run `./run.sh --headless` and open the printed URL in a
browser. Proton journal locations are detected automatically. See the
[Getting Started guide](../../wiki/Getting-Started) for platform details.

### Pair a tablet or another computer

On the gaming PC, open **Settings → Paired Devices** and scan the one-time QR
code or copy its LAN link. The device reconnects automatically after pairing
and opens in the touch-friendly Panel view by default. No password or account
is involved.

After an upgrade, Frameshift may reconstruct local commander history. The app
shows the current phase and journal count, then publishes the completed cockpit
state once the reconstruction is coherent.

## Highlights

- **Live cockpit** — current system, ship, fuel, cargo, missions, rebuy cover,
  exploration data, fleet and carrier status, with focused voice callouts.
- **Trading and markets** — loops and multi-hop routes ranked by profit per
  hour, market confidence and age, cargo recovery, commodity search, mining
  buyers, price history, and persistent market watches.
- **Exploration and exobiology** — Road to Riches, neutron and bio routing,
  body-local surface navigation, colony-range guidance, sample tracking,
  first-log estimates, values, and GeoJSON export.
- **Combat and engineering** — massacre-stack progress, combat and AX sessions,
  mission delivery tracking, rebuy warnings, a complete offline engineering
  catalog, shared wishlists, material deficits, and trader guidance.
- **Galaxy and mission control** — Powerplay, BGS factions and conflicts,
  community goals, commander objectives, learned timings, session planning,
  portable operations boards, and dedicated specialist workspaces.
- **Panel mode and autoplot** — a touch-first layout with per-device pages,
  themes and card arrangement. On Windows, Frameshift can plot routes in Elite
  using your own keybinds and verify them against `NavRoute.json`.
- **Local analytics** — session rates, earnings by source, daily profit,
  balance history, jumps, distance, and a compact commander-scoped event ledger.

Detailed guides live in the wiki:
[Trading](../../wiki/Trade-Routes-and-Market-Tools) ·
[Exploration](../../wiki/Exploration-and-Exobiology) ·
[Panel mode](../../wiki/Flight-Panel-Mode) ·
[Autoplot](../../wiki/Autoplot).

## Local-first data and security

- Commander history, objectives, watches, and specialist records stay local
  and are kept separate from the rebuildable market cache.
- Live and Legacy profiles are isolated. Community market and route advice
  fails closed in Legacy rather than presenting Live-galaxy data as valid.
- Localhost access on the gaming PC is automatic. LAN devices use short-lived,
  single-use pairing links and revocable **read**, **control**, or **admin**
  permissions. Keep Frameshift on a trusted LAN and do not port-forward it.
- Anonymous market contribution to EDDN is enabled by default. Broader
  outfitting, navigation, exploration, Codex, and biological reporting is a
  separate opt-in in Settings.
- Windows updates require a matching same-release SHA-256 checksum. This
  detects damaged or truncated downloads but is not publisher code-signing.

Frameshift works alongside EDMC, so you can keep EDMC running if you use it to
sync services such as Inara or EDSM.

## Extensions

Extensions are optional local add-on folders that react to journal events.
Declarative packs can add alerts or suggest objectives without executing code.
Advanced process adapters require explicit approval, and any content change
returns them to pending review. Extensions need no hosted service, login, or
API key. See [Extension API v1](docs/EXTENSIONS.md).

## Community ecosystem

Frameshift depends on the Elite Dangerous community's open-data work:

- [EDDN](https://github.com/EDCD/EDDN) supplies live community observations.
- [Spansh](https://spansh.co.uk) provides galaxy data, routing, station search,
  and community-mapped biological signals.
- [Inara](https://inara.cz) and [EDSM](https://www.edsm.net) provide reference
  and mapping destinations linked throughout the app.
- [EDCD](https://edcd.github.io/) maintains schemas, journal documentation, and
  conventions used across the companion-app ecosystem.

If these services help you, please consider supporting them.

Frameshift also bundles open-source work from:

- [EDEngineer](https://github.com/msarilar/EDEngineer) by msarilar — reference
  data behind the offline engineering catalog (MIT).
- [QR Code generator library](https://www.nayuki.io/page/qr-code-generator-library)
  by Project Nayuki — renders device-pairing QR codes locally (MIT).
- [Piper TTS](https://github.com/rhasspy/piper) and its community voices power
  the optional neural voice callouts (downloaded on demand, checksum-pinned).

Full license texts: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Support

- [Getting Started](../../wiki/Getting-Started)
- [Settings and configuration](../../wiki/Settings-and-Configuration)
- [Troubleshooting and FAQ](../../wiki/Troubleshooting-and-FAQ)
- [Report an issue](../../issues)

Settings → Diagnostics can create a bounded support bundle that excludes
journals, commander names, pairing secrets, and databases.

Frameshift is available under the [MIT License](LICENSE). It is not affiliated
with Frontier Developments. Elite Dangerous is a trademark of Frontier
Developments plc.
