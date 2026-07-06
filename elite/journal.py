"""Reads the Elite Dangerous journal directory: bootstraps state from recent
session logs, then tails the newest journal + Status/Cargo/Market json files."""

import json
import os
import threading
import time
from pathlib import Path

from . import biovalues, marketdb

BIO_SIGNAL_TYPE = "$SAA_SignalType_Biological;"

DEFAULT_JOURNAL_DIR = (
    Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"
)
BOOTSTRAP_MAX_FILES = 25
POLL_SECONDS = 1.0

ED_STEAM_APP_ID = "359320"
_PROTON_SUFFIX = (
    Path("steamapps/compatdata") / ED_STEAM_APP_ID
    / "pfx/drive_c/users/steamuser/Saved Games/Frontier Developments/Elite Dangerous"
)


def _candidate_journal_dirs():
    yield DEFAULT_JOURNAL_DIR  # native Windows
    home = Path.home()
    for steam_root in (  # Linux: Steam Proton prefixes
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/debian-installation",
    ):
        yield steam_root / _PROTON_SUFFIX


def _clean_name(raw):
    """Turn an internal name like '$gold_name;' into 'Gold'."""
    if not raw:
        return ""
    name = raw.strip("$;")
    if name.endswith("_name"):
        name = name[: -len("_name")]
    return name.replace("_", " ").title()


def find_journal_dir():
    override = os.environ.get("ED_JOURNAL_DIR")
    if override:
        return Path(override)
    for candidate in _candidate_journal_dirs():
        if candidate.is_dir():
            return candidate
    return DEFAULT_JOURNAL_DIR


def journal_files(journal_dir):
    # Filenames are ISO-timestamped, so lexicographic order == chronological.
    return sorted(journal_dir.glob("Journal.*.log"))


class JournalWatcher:
    def __init__(self, state, journal_dir=None):
        self.state = state
        self.journal_dir = Path(journal_dir) if journal_dir else find_journal_dir()
        self._current_file = None
        self._offset = 0
        self._partial = ""
        self._status_mtimes = {}
        self._body_scans = {}  # body name -> details, current system only
        self._live = False  # False during bootstrap replay, True while tailing
        self._last_logged_balance = None

    # ---------- event handling ----------

    def handle_event(self, event):
        etype = event.get("event")
        handler = getattr(self, f"_on_{etype.lower()}", None) if etype else None
        if handler:
            handler(event)
        if event.get("timestamp"):
            self.state.update(last_journal_event=event["timestamp"])

    def _on_commander(self, e):
        self.state.update(commander=e.get("Name"))

    def _on_loadgame(self, e):
        updates = {"commander": e.get("Commander")}
        if e.get("Ship_Localised") or e.get("Ship"):
            updates["ship_type"] = e.get("Ship_Localised") or e.get("Ship")
        if e.get("ShipName"):
            updates["ship_name"] = e.get("ShipName")
        if e.get("ShipIdent"):
            updates["ship_ident"] = e.get("ShipIdent")
        if e.get("FuelCapacity") is not None:
            updates["fuel_capacity"] = e.get("FuelCapacity")
        if e.get("Credits") is not None:
            updates["credits"] = e.get("Credits")
            self._log_balance_point(marketdb.parse_update_time(e.get("timestamp")), e.get("Credits"))
        self.state.update(**{k: v for k, v in updates.items() if v is not None})

    def _on_loadout(self, e):
        fuel_cap = e.get("FuelCapacity")
        if isinstance(fuel_cap, dict):
            fuel_cap = fuel_cap.get("Main")
        self.state.update(
            ship_type=_clean_name(e.get("Ship")) or None,
            ship_name=e.get("ShipName") or None,
            ship_ident=e.get("ShipIdent") or None,
            cargo_capacity=e.get("CargoCapacity"),
            max_jump_range=e.get("MaxJumpRange"),
            fuel_capacity=fuel_cap,
        )

    def _on_location(self, e):
        if e.get("StarSystem") != self.state.system:
            self._body_scans = {}
            self.state.update(bio_signals={})
        self.state.update(
            system=e.get("StarSystem"),
            system_address=e.get("SystemAddress"),
            star_pos=e.get("StarPos"),
            body=e.get("Body"),
            docked=bool(e.get("Docked")),
            station=e.get("StationName") if e.get("Docked") else None,
            station_type=e.get("StationType") if e.get("Docked") else None,
            station_market_id=e.get("MarketID") if e.get("Docked") else None,
        )

    def _on_fsdjump(self, e):
        self._body_scans = {}
        self.state.update(
            system=e.get("StarSystem"),
            system_address=e.get("SystemAddress"),
            star_pos=e.get("StarPos"),
            body=e.get("Body"),
            docked=False,
            station=None,
            station_type=None,
            station_market_id=None,
            dist_from_star_ls=None,
            bio_signals={},
        )
        self.state.add_jump(e.get("StarSystem"), e.get("JumpDist"), e.get("timestamp"))

    def _on_carrierjump(self, e):
        if e.get("StarSystem") != self.state.system:
            self._body_scans = {}
            self.state.update(bio_signals={})
        self.state.update(
            system=e.get("StarSystem"),
            system_address=e.get("SystemAddress"),
            star_pos=e.get("StarPos"),
            body=e.get("Body"),
        )

    def _on_docked(self, e):
        self.state.update(
            system=e.get("StarSystem"),
            system_address=e.get("SystemAddress"),
            docked=True,
            station=e.get("StationName"),
            station_type=e.get("StationType"),
            station_market_id=e.get("MarketID"),
            dist_from_star_ls=e.get("DistFromStarLS"),
        )

    def _on_undocked(self, e):
        self.state.update(
            docked=False,
            station=None,
            station_type=None,
            station_market_id=None,
            dist_from_star_ls=None,
        )

    # ---------- exobiology ----------

    @staticmethod
    def _bio_count(e):
        for sig in e.get("Signals") or []:
            if sig.get("Type") == BIO_SIGNAL_TYPE:
                return sig.get("Count") or 0
        return 0

    def _update_bio_body(self, body_name, count=None, genuses=None):
        if not body_name:
            return
        signals = dict(self.state.bio_signals)
        entry = dict(signals.get(body_name) or {"body": body_name, "count": 0, "genuses": []})
        if count:
            entry["count"] = count
        if genuses is not None:
            entry["genuses"] = genuses
        entry.update(self._body_scans.get(body_name) or {})
        signals[body_name] = entry
        self.state.update(bio_signals=signals)

    def _on_fssbodysignals(self, e):
        count = self._bio_count(e)
        if count:
            self._update_bio_body(e.get("BodyName"), count=count)

    def _on_saasignalsfound(self, e):
        count = self._bio_count(e)
        genuses = [
            biovalues.genus_info(g.get("Genus_Localised") or _clean_name(g.get("Genus")))
            for g in e.get("Genuses") or []
        ]
        if count or genuses:
            self._update_bio_body(e.get("BodyName"), count=count or None, genuses=genuses or None)

    def _on_scan(self, e):
        body = e.get("BodyName")
        if not body or e.get("PlanetClass") is None:
            return
        gravity = e.get("SurfaceGravity")
        details = {
            "planet_class": e.get("PlanetClass"),
            "atmosphere": e.get("Atmosphere") or e.get("AtmosphereType") or "",
            "gravity_g": round(gravity / 9.80665, 2) if gravity is not None else None,
            "temp_k": round(e.get("SurfaceTemperature")) if e.get("SurfaceTemperature") else None,
            "landable": bool(e.get("Landable")),
        }
        self._body_scans[body] = details
        if body in self.state.bio_signals:
            self._update_bio_body(body)

    def _on_scanorganic(self, e):
        species = e.get("Species_Localised") or _clean_name(e.get("Species"))
        genus = e.get("Genus_Localised") or _clean_name(e.get("Genus"))
        variant = e.get("Variant_Localised")
        scan_type = e.get("ScanType")
        if scan_type in ("Log", "Sample"):
            prev = self.state.bio_sampling or {}
            same = prev.get("species") == species
            progress = 1 if scan_type == "Log" else (min(3, (prev.get("progress") or 1) + 1) if same else 2)
            self.state.update(bio_sampling={
                "genus": genus, "species": species, "variant": variant,
                "progress": progress,
                "colony_m": biovalues.GENUS_COLONY_M.get(genus),
                "value": biovalues.species_value(species),
            })
        elif scan_type == "Analyse":
            value = biovalues.species_value(species) or biovalues.genus_info(genus).get("min_value") or 0
            vault = list(self.state.bio_vault)
            vault.append({
                "species": species, "genus": genus, "variant": variant,
                "value": value, "body": self.state.body,
            })
            self.state.update(bio_vault=vault, bio_sampling=None)

    # ---------- trade & balance logging (analytics) ----------

    def _on_marketbuy(self, e):
        try:
            marketdb.log_trade(
                marketdb.parse_update_time(e.get("timestamp")), "buy",
                (e.get("Type") or "").lower(), e.get("Type_Localised") or (e.get("Type") or "").title(),
                e.get("Count"), e.get("BuyPrice"), e.get("TotalCost"),
            )
        except Exception:
            pass

    def _on_marketsell(self, e):
        try:
            profit = None
            if e.get("SellPrice") is not None and e.get("AvgPricePaid") is not None:
                profit = (e["SellPrice"] - e["AvgPricePaid"]) * (e.get("Count") or 0)
            marketdb.log_trade(
                marketdb.parse_update_time(e.get("timestamp")), "sell",
                (e.get("Type") or "").lower(), e.get("Type_Localised") or (e.get("Type") or "").title(),
                e.get("Count"), e.get("SellPrice"), e.get("TotalSale"), profit,
            )
        except Exception:
            pass

    def _log_balance_point(self, ts, balance):
        try:
            marketdb.log_balance(ts, balance)
        except Exception:
            pass

    def _on_sellorganicdata(self, e):
        self.state.update(bio_vault=[], bio_sampling=None)

    def _on_died(self, e):
        self.state.update(bio_vault=[], bio_sampling=None)  # exobio data is lost on death

    # ---------- status json files ----------

    def _read_json_file(self, path):
        try:
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                return None
            return json.loads(text)
        except (OSError, json.JSONDecodeError):
            # The game rewrites these files constantly; transient failures are normal.
            return None

    def _refresh_status_files(self, force=False):
        for name, parser in (
            ("Status.json", self._apply_status),
            ("Cargo.json", self._apply_cargo),
            ("Market.json", self._apply_market),
        ):
            path = self.journal_dir / name
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if not force and self._status_mtimes.get(name) == mtime:
                continue
            data = self._read_json_file(path)
            if data is not None:
                self._status_mtimes[name] = mtime
                parser(data)

    def _apply_status(self, data):
        fuel = data.get("Fuel") or {}
        updates = {
            "fuel_main": fuel.get("FuelMain"),
            "fuel_reservoir": fuel.get("FuelReservoir"),
            "cargo_tons": data.get("Cargo"),
            "legal_state": data.get("LegalState"),
        }
        balance = data.get("Balance")
        if balance is not None:
            updates["credits"] = balance
            # Sample the live balance curve on meaningful changes only.
            if self._live and (
                self._last_logged_balance is None
                or abs(balance - self._last_logged_balance) >= 50000
            ):
                self._last_logged_balance = balance
                self._log_balance_point(marketdb.now_epoch(), balance)
        dest = data.get("Destination") or {}
        updates["destination"] = dest.get("Name") or None
        self.state.update(**updates)

    def _apply_cargo(self, data):
        inventory = [
            {
                "name": item.get("Name_Localised") or (item.get("Name") or "").title(),
                "symbol": (item.get("Name") or "").lower(),
                "count": item.get("Count", 0),
                "stolen": item.get("Stolen", 0),
            }
            for item in data.get("Inventory", [])
        ]
        self.state.update(cargo_inventory=inventory)

    def _apply_market(self, data):
        try:
            from .eddn_upload import UPLOADER

            UPLOADER.maybe_publish(data, self.state.commander)
        except Exception:
            pass  # uploading is best-effort; never break market parsing
        items = []
        for item in data.get("Items", []):
            stock = item.get("Stock", 0)
            demand = item.get("Demand", 0)
            buy = item.get("BuyPrice", 0)
            sell = item.get("SellPrice", 0)
            if not (stock or demand or buy or sell):
                continue
            items.append(
                {
                    "name": item.get("Name_Localised") or _clean_name(item.get("Name")),
                    "category": item.get("Category_Localised") or "",
                    "buy": buy,
                    "sell": sell,
                    "stock": stock,
                    "demand": demand,
                }
            )
        self.state.update(
            market={
                "market_id": data.get("MarketID"),
                "station": data.get("StationName"),
                "system": data.get("StarSystem"),
                "timestamp": data.get("timestamp"),
                "items": items,
            }
        )

    # ---------- bootstrap & tail ----------

    def _process_lines(self, text):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                self.handle_event(event)
            except Exception:
                continue  # one bad event must never kill the watcher

    def bootstrap(self):
        if not self.journal_dir.is_dir():
            self.state.update(journal_dir_found=False)
            return
        files = journal_files(self.journal_dir)
        if not files:
            return

        # Walk backwards until the essentials have been seen, then replay the
        # selected files in chronological order through the normal handlers.
        needed = {"location": False, "loadout": False, "commander": False}
        selected = []
        for path in reversed(files[-BOOTSTRAP_MAX_FILES:]):
            selected.insert(0, path)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if '"event":"Location"' in text or '"event":"FSDJump"' in text:
                needed["location"] = True
            if '"event":"Loadout"' in text:
                needed["loadout"] = True
            if '"event":"Commander"' in text or '"event":"LoadGame"' in text:
                needed["commander"] = True
            if all(needed.values()):
                break

        for path in selected:
            try:
                self._process_lines(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue

        # Tail from the end of the newest file.
        self._current_file = files[-1]
        try:
            self._offset = self._current_file.stat().st_size
        except OSError:
            self._offset = 0

        self._refresh_status_files(force=True)

    def _poll_journal(self):
        files = journal_files(self.journal_dir)
        if not files:
            return
        newest = files[-1]
        if self._current_file != newest:
            # Finish the old file, then switch to the new session's log.
            if self._current_file is not None:
                self._read_new_bytes()
            self._current_file = newest
            self._offset = 0
            self._partial = ""
        self._read_new_bytes()

    def _read_new_bytes(self):
        try:
            size = self._current_file.stat().st_size
            if size < self._offset:  # truncated/replaced
                self._offset = 0
                self._partial = ""
            if size == self._offset:
                return
            with open(self._current_file, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
        except OSError:
            return
        text = self._partial + chunk
        if text and not text.endswith("\n"):
            # Keep the trailing partial line for the next poll.
            text, _, self._partial = text.rpartition("\n")
        else:
            self._partial = ""
        if text:
            self._process_lines(text)

    def import_trade_history(self):
        """One-time sweep of ALL journal files for trade/balance events, so
        analytics start with full history instead of just recent sessions."""
        if not self.journal_dir.is_dir():
            return
        conn = marketdb.connect()
        try:
            done = {r[0] for r in conn.execute("SELECT filename FROM imported_journals")}
        finally:
            conn.close()
        files = journal_files(self.journal_dir)
        markers = ('"event":"MarketBuy"', '"event":"MarketSell"', '"event":"LoadGame"')
        for path in files[:-1]:  # the newest file is still being written; tail covers it
            if path.name in done:
                continue
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not any(m in line for m in markers):
                        continue
                    try:
                        event = json.loads(line.strip().rstrip(","))
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("event")
                    if etype == "MarketBuy":
                        self._on_marketbuy(event)
                    elif etype == "MarketSell":
                        self._on_marketsell(event)
                    elif etype == "LoadGame" and event.get("Credits") is not None:
                        self._log_balance_point(
                            marketdb.parse_update_time(event.get("timestamp")), event["Credits"]
                        )
            except OSError:
                continue
            conn = marketdb.connect()
            try:
                conn.execute("INSERT OR IGNORE INTO imported_journals(filename) VALUES(?)", (path.name,))
                conn.commit()
            finally:
                conn.close()

    def run_forever(self):
        self.bootstrap()
        try:
            self.import_trade_history()
        except Exception:
            pass
        self._live = True
        while True:
            try:
                self._poll_journal()
                self._refresh_status_files()
            except Exception:
                pass
            time.sleep(POLL_SECONDS)

    def start(self):
        thread = threading.Thread(target=self.run_forever, name="journal-watcher", daemon=True)
        thread.start()
        return thread
