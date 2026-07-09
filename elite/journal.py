"""Reads the Elite Dangerous journal directory: bootstraps state from recent
session logs, then tails the newest journal + Status/Cargo/Market json files."""

import json
import os
import sys
import threading
import time
from pathlib import Path

from . import biovalues, exploration, marketdb

BIO_SIGNAL_TYPE = "$SAA_SignalType_Biological;"

DEFAULT_JOURNAL_DIR = (
    Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"
)
BOOTSTRAP_MAX_FILES = 25
BOOTSTRAP_MIN_FILES = 12  # context like colonization depots spans sessions
POLL_SECONDS = 1.0

ED_STEAM_APP_ID = "359320"
_PROTON_SUFFIX = (
    Path("steamapps/compatdata") / ED_STEAM_APP_ID
    / "pfx/drive_c/users/steamuser/Saved Games/Frontier Developments/Elite Dangerous"
)


def _windows_saved_games():
    """The real 'Saved Games' known folder via the shell API. Users can relocate
    it (small C: drives); Path.home()/'Saved Games' misses that."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
            ]

        # FOLDERID_SavedGames {4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4}
        folder_id = GUID(0x4C5C32FF, 0xBB9D, 0x43B0,
                         (ctypes.c_ubyte * 8)(0xB5, 0xB4, 0x2D, 0x72, 0xE5, 0x4E, 0xAA, 0xA4))
        path_ptr = ctypes.c_wchar_p()
        res = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(path_ptr))
        if res != 0:
            return None
        try:
            return Path(path_ptr.value) / "Frontier Developments" / "Elite Dangerous"
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
    except Exception:
        return None


def _candidate_journal_dirs():
    known = _windows_saved_games()  # honors a relocated Saved Games folder
    if known:
        yield known
    yield DEFAULT_JOURNAL_DIR  # native Windows, default profile layout
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
    """Precedence: the in-app setting, the ED_JOURNAL_DIR env var, then
    auto-detection (known-folder Saved Games, default path, Proton prefixes)."""
    from . import settings

    manual = (settings.get("journal_dir") or "").strip()
    if manual:
        return Path(manual)
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


def probe_roots():
    """Directories the LAN-reachable journal-folder validator may look inside:
    the user's profile, the (possibly relocated) Saved Games folder, and
    wherever auto-detection currently points. Confining the live check to
    these stops the open API being used to probe arbitrary paths, while still
    covering every place a journal folder can plausibly live."""
    roots = [Path.home()]
    known = _windows_saved_games()
    if known:
        roots.append(known)
    roots.append(find_journal_dir())
    return roots


class JournalWatcher:
    def __init__(self, state, journal_dir=None):
        self.state = state
        self._fixed_dir = journal_dir is not None  # explicit dir: never re-detect
        self.journal_dir = Path(journal_dir) if journal_dir else find_journal_dir()
        self._current_file = None
        self._offset = 0
        self._partial = ""
        self._status_mtimes = {}
        self._body_scans = {}  # body name -> details, current system only
        self._live = False  # False during bootstrap replay, True while tailing
        self._last_logged_balance = None
        self._bio_fetched = set()  # id64s we've queried Spansh for this session
        self._hull_bucket = None   # lowest hull-damage tier already called out
        self._first_disc_system = None  # system a first-discovery alert fired for
        self._rebuy_level = 0      # 0 = covered, 1 = below 2x rebuy, 2 = below 1x

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
        # LoadGame marks the start of a play session. Bootstrap replays these
        # chronologically, so the most recent one sets the current session and
        # the jumps logged after it reconstruct the session's distance/count.
        self.state.start_session(
            marketdb.parse_update_time(e.get("timestamp")) or marketdb.now_epoch(),
            e.get("Credits"),
        )

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
            rebuy=e.get("Rebuy"),
        )
        self._check_rebuy()

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
        self._fetch_community_bio(e.get("SystemAddress"), e.get("StarSystem"))

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
        # Actual fuel burned this jump → conservative fuel-per-jump for scoop
        # projections. FuelLevel is the fresh post-jump tank reading.
        self.state.add_fuel_used(e.get("FuelUsed"))
        if e.get("FuelLevel") is not None:
            self.state.update(fuel_main=e.get("FuelLevel"))
        self._fetch_community_bio(e.get("SystemAddress"), e.get("StarSystem"))

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
        self._fetch_community_bio(e.get("SystemAddress"), e.get("StarSystem"))

    def _on_docked(self, e):
        self._hull_bucket = None  # repairs are available; let damage re-announce
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

    def _fetch_community_bio(self, id64, system):
        """Pull community-mapped genuses for a system from Spansh in the
        background, so they show on arrival before you FSS/DSS anything. Live
        only (never during bootstrap replay), fetched at most once per session."""
        if not self._live or not id64 or id64 in self._bio_fetched:
            return
        self._bio_fetched.add(id64)

        def work():
            try:
                from . import spansh

                bodies = spansh.system_genuses(id64)
            except Exception:
                return
            # Apply only if the player is still in that system.
            if self.state.system_address == id64:
                self.state.update(
                    bio_community={"id64": id64, "system": system, "bodies": bodies}
                )

        threading.Thread(target=work, name="bio-community", daemon=True).start()

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
        if not entry.get("genuses") and entry.get("landable"):
            entry["predicted"] = biovalues.predict_genera(
                entry.get("planet_class"), entry.get("atmosphere"),
                entry.get("temp_k"), entry.get("gravity_g"), entry.get("volcanism"),
            )
        else:
            entry.pop("predicted", None)
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
        if not body:
            return
        # First-in: the primary star's auto-scan reveals whether anyone has been
        # here before. WasDiscovered false on the entry star = the whole system
        # is yours to discover. Announce once per system, live only.
        if (
            self._live
            and e.get("StarType")
            and e.get("BodyID") == 0
            and not e.get("WasDiscovered", True)
            and self.state.system
            and self._first_disc_system != self.state.system
        ):
            self._first_disc_system = self.state.system
            self.state.push_alert(
                "info", "first_discovery",
                f"First discovery. {self.state.system} is undiscovered.",
                f"✦ FIRST DISCOVERY · {self.state.system}",
            )

        # Cartographic value estimate for the exploration tracker
        base = exploration.scan_base_value(e)
        if base is not None:
            scans = dict(self.state.explo_scans)
            prev = scans.get(body)
            scans[body] = {
                "body": body,
                "base": base,
                "first": not e.get("WasDiscovered", True),
                "mapped": prev.get("mapped", False) if prev else False,
                "class": e.get("PlanetClass") or e.get("StarType"),
            }
            self.state.update(explo_scans=scans)

        if e.get("PlanetClass") is None:
            return
        gravity = e.get("SurfaceGravity")
        details = {
            "planet_class": e.get("PlanetClass"),
            "atmosphere": e.get("Atmosphere") or e.get("AtmosphereType") or "",
            "gravity_g": round(gravity / 9.80665, 2) if gravity is not None else None,
            "temp_k": round(e.get("SurfaceTemperature")) if e.get("SurfaceTemperature") else None,
            "landable": bool(e.get("Landable")),
            "volcanism": e.get("Volcanism") or "",
        }
        self._body_scans[body] = details
        if body in self.state.bio_signals:
            self._update_bio_body(body)

    def _on_saascancomplete(self, e):
        body = e.get("BodyName")
        scans = dict(self.state.explo_scans)
        if body in scans:
            entry = dict(scans[body])
            entry["mapped"] = True
            scans[body] = entry
            self.state.update(explo_scans=scans)

    def _on_sellexplorationdata(self, e):
        self.state.update(explo_scans={})
        self._log_income(e, "exploration", e.get("TotalEarnings") or e.get("BaseValue"))

    def _on_multisellexplorationdata(self, e):
        self.state.update(explo_scans={})
        self._log_income(e, "exploration", e.get("TotalEarnings") or e.get("BaseValue"))

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

    # ---------- colonization ----------

    def _on_colonisationconstructiondepot(self, e):
        market_id = e.get("MarketID")
        if not market_id:
            return
        resources = []
        for r in e.get("ResourcesRequired") or []:
            required = r.get("RequiredAmount") or 0
            provided = r.get("ProvidedAmount") or 0
            resources.append({
                "symbol": (r.get("Name") or "").strip("$;").removesuffix("_name").lower(),
                "name": r.get("Name_Localised") or _clean_name(r.get("Name")),
                "required": required,
                "provided": provided,
                "remaining": max(0, required - provided),
                "payment": r.get("Payment") or 0,
            })
        depots = dict(self.state.colonisation)
        depots[market_id] = {
            "market_id": market_id,
            "progress": e.get("ConstructionProgress"),
            "complete": bool(e.get("ConstructionComplete")),
            "failed": bool(e.get("ConstructionFailed")),
            "updated": e.get("timestamp"),
            # The event fires while docked at the depot, so current location names it.
            "station": self.state.station if self.state.docked else None,
            "system": self.state.system,
            "resources": sorted(resources, key=lambda r: -r["remaining"]),
        }
        # Keep the most recent handful of projects only.
        if len(depots) > 8:
            for key in sorted(depots, key=lambda k: depots[k].get("updated") or "")[: len(depots) - 8]:
                depots.pop(key, None)
        self.state.update(colonisation=depots)

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

    def _log_income(self, e, category, amount, detail=None):
        try:
            marketdb.log_income(
                marketdb.parse_update_time(e.get("timestamp")), category, amount, detail
            )
        except Exception:
            pass

    def _on_sellorganicdata(self, e):
        total = sum((b.get("Value") or 0) + (b.get("Bonus") or 0) for b in e.get("BioData") or [])
        self._log_income(e, "exobiology", total)
        self.state.update(bio_vault=[], bio_sampling=None)

    def _on_missioncompleted(self, e):
        self._log_income(e, "mission", e.get("Reward") or 0, e.get("Name"))
        self._remove_mission(e.get("MissionID"))

    def _on_missionabandoned(self, e):
        self._remove_mission(e.get("MissionID"))

    def _on_missionfailed(self, e):
        self._remove_mission(e.get("MissionID"))

    # ---------- combat: kills, bounties, massacre stacks ----------

    def _massacre_missions(self):
        for m in self.state.missions.values():
            if (m.get("kind") == "combat" and m.get("target_faction") and m.get("kill_count")
                    and "massacre" in (m.get("name") or "").lower()):
                yield m

    def _record_combat_kill(self, victim, bounty_cr=0, bond_cr=0):
        counts = any(m["target_faction"] == victim for m in self._massacre_missions())
        new_count = self.state.record_kill(victim, bounty_cr, bond_cr, counts_for_stack=counts)
        if new_count is None or not self._live:
            return
        # Fire exactly when the largest giver's requirement is crossed.
        needed = next((s["kills_needed"] for s in self.state._massacre_snapshot()
                       if s["faction"] == victim), None)
        if needed and new_count == needed:
            self.state.push_alert(
                "info", "massacre",
                f"Massacre stack complete. All missions against {victim} are done.",
                f"✦ STACK COMPLETE · {victim}",
            )

    def _on_bounty(self, e):
        total = e.get("TotalReward")
        if total is None:
            total = sum((r.get("Reward") or 0) for r in e.get("Rewards") or [])
        self._record_combat_kill(e.get("VictimFaction"), bounty_cr=total or 0)

    def _on_factionkillbond(self, e):
        self._record_combat_kill(e.get("VictimFaction"), bond_cr=e.get("Reward") or 0)

    def _sync_faction_kills(self):
        """Drop stack-kill counters for factions with no active massacre
        missions left, so a future stack starts counting from zero."""
        active = {m["target_faction"] for m in self._massacre_missions()}
        stale = [f for f in self.state.faction_kills if f not in active]
        if stale:
            fk = dict(self.state.faction_kills)
            for f in stale:
                fk.pop(f, None)
            self.state.update(faction_kills=fk)

    def _on_redeemvoucher(self, e):
        vtype = (e.get("Type") or "").lower()
        category = "bounty" if vtype in ("bounty", "combatbond", "settlement") else "other"
        # RedeemVoucher can split across factions; Amount is the total received.
        self._log_income(e, category, e.get("Amount"), vtype or None)

    # ---------- mission board ----------

    # Map the internal Mission_* name stem to a short kind for grouping/icons.
    _MISSION_KINDS = (
        ("delivery", "delivery"), ("collect", "collect"), ("salvage", "salvage"),
        ("mining", "mining"), ("courier", "courier"), ("passenger", "passenger"),
        ("massacre", "combat"), ("assassin", "combat"), ("hack", "combat"),
        ("piracy", "piracy"), ("rescue", "rescue"), ("donation", "donation"),
    )

    @classmethod
    def _mission_kind(cls, name):
        low = (name or "").lower()
        for needle, kind in cls._MISSION_KINDS:
            if needle in low:
                return kind
        return "other"

    def _on_missionaccepted(self, e):
        mission_id = e.get("MissionID")
        if mission_id is None:
            return
        missions = dict(self.state.missions)
        missions[mission_id] = {
            "id": mission_id,
            "name": e.get("LocalisedName") or _clean_name(e.get("Name")),
            "kind": self._mission_kind(e.get("Name")),
            "faction": e.get("Faction"),
            "commodity": e.get("Commodity_Localised") or _clean_name(e.get("Commodity")) or None,
            "commodity_symbol": (e.get("Commodity") or "").strip("$;").removesuffix("_Name").removesuffix("_name").lower() or None,
            "count": e.get("Count"),
            "dest_system": e.get("DestinationSystem") or None,
            "dest_station": e.get("DestinationStation") or None,
            "target_faction": e.get("TargetFaction") or None,
            "kill_count": e.get("KillCount"),
            "reward": e.get("Reward") or 0,
            "wing": bool(e.get("Wing")),
            "expiry": e.get("Expiry"),
            "expiry_ts": marketdb.parse_update_time(e.get("Expiry")),
            "accepted": e.get("timestamp"),
        }
        self.state.update(missions=missions)

    def _remove_mission(self, mission_id):
        if mission_id is None or mission_id not in self.state.missions:
            return
        missions = dict(self.state.missions)
        missions.pop(mission_id, None)
        self.state.update(missions=missions)
        self._sync_faction_kills()

    def _on_missions(self, e):
        """Session-start snapshot: reconcile our set to the game's active list so
        missions completed/expired while the app was closed drop off."""
        active_ids = {m.get("MissionID") for m in e.get("Active") or []}
        if not self.state.missions:
            return
        missions = {mid: m for mid, m in self.state.missions.items() if mid in active_ids}
        if len(missions) != len(self.state.missions):
            self.state.update(missions=missions)
            self._sync_faction_kills()

    # ---------- engineering materials ----------

    @staticmethod
    def _material_entry(item):
        name = item.get("Name") or ""
        return name.lower(), {
            "symbol": name.lower(),
            "name": item.get("Name_Localised") or _clean_name(name),
            "count": item.get("Count", 0),
        }

    def _on_materials(self, e):
        mats = {"Raw": {}, "Manufactured": {}, "Encoded": {}}
        for cat in mats:
            for item in e.get(cat) or []:
                sym, entry = self._material_entry(item)
                if sym:
                    mats[cat][sym] = entry
        self.state.update(materials=mats)

    def _adjust_material(self, category, item, delta_sign):
        cat = (category or "").title()
        if cat not in ("Raw", "Manufactured", "Encoded"):
            return
        mats = {k: dict(v) for k, v in self.state.materials.items()}
        sym, entry = self._material_entry(item)
        if not sym:
            return
        current = mats[cat].get(sym, {"symbol": sym, "name": entry["name"], "count": 0})
        current = dict(current)
        current["count"] = max(0, current.get("count", 0) + delta_sign * (item.get("Count", 0) or 0))
        if current["count"]:
            mats[cat][sym] = current
        else:
            mats[cat].pop(sym, None)
        self.state.update(materials=mats)

    def _pinned_craftable(self):
        """Names of pinned blueprints whose full climb is covered right now."""
        from . import blueprints, settings

        inventory = {}
        for cat in self.state.materials.values():
            for sym, m in cat.items():
                inventory[sym] = m.get("count", 0)
        out = set()
        for p in settings.get("pinned_blueprints", []):
            try:
                if blueprints.plan(p["name"], p.get("grade", 5), inventory)["craftable"]:
                    out.add(p["name"])
            except KeyError:
                continue
        return out

    def _on_materialcollected(self, e):
        before = self._pinned_craftable() if self._live else set()
        self._adjust_material(e.get("Category"), e, +1)
        if not self._live:
            return
        # A pickup that completes a pinned blueprint's shopping list is worth
        # announcing — that's the moment you can stop farming.
        newly_ready = self._pinned_craftable() - before
        if newly_ready:
            from . import settings

            grades = {p["name"]: p.get("grade", 5) for p in settings.get("pinned_blueprints", [])}
            for name in newly_ready:
                grade = grades.get(name, 5)
                self.state.push_alert(
                    "info", "blueprint",
                    f"Materials complete for {name}, grade {grade}.",
                    f"✦ READY TO ENGINEER · {name} G{grade}",
                )

    def _on_materialdiscarded(self, e):
        self._adjust_material(e.get("Category"), e, -1)

    def _on_died(self, e):
        # Exobio samples and unsold cartographic data are lost on death.
        self.state.update(bio_vault=[], bio_sampling=None, explo_scans={})

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
            ("NavRoute.json", self._apply_navroute),
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

    _FLAG_IN_MAIN_SHIP = 0x01000000  # Status.json Flags bit 24

    def _apply_status(self, data):
        updates = {
            "cargo_tons": data.get("Cargo"),
            "legal_state": data.get("LegalState"),
        }
        # In an SRV / Scarab / Nomad (or on foot), Status.json's Fuel block is
        # the *vehicle's* tiny tank, not the ship's — taking it would read as
        # ~0% against ship capacity and false-trigger low-fuel callouts. Keep
        # the last known ship reading until we're back in the main ship.
        flags = data.get("Flags")
        if flags is None or flags & self._FLAG_IN_MAIN_SHIP:
            fuel = data.get("Fuel") or {}
            updates["fuel_main"] = fuel.get("FuelMain")
            updates["fuel_reservoir"] = fuel.get("FuelReservoir")
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
        if balance is not None:
            self._check_rebuy()

    def _apply_navroute(self, data):
        """NavRoute.json holds the full plotted route (each system's StarClass),
        which the game keeps intact as you fly it. Kept for fuel-scoop callouts."""
        route = [
            {
                "system": r.get("StarSystem"),
                "address": r.get("SystemAddress"),
                "star_class": r.get("StarClass"),
            }
            for r in (data.get("Route") or [])
            if r.get("StarSystem")
        ]
        self.state.update(nav_route=route)

    def _on_navrouteclear(self, e):
        self.state.update(nav_route=[])

    def _on_interdicted(self, e):
        """You are being pulled out of supercruise (pirate / NPC / Thargoid)."""
        if not self._live:
            return
        who = e.get("Interdictor_Localised") or e.get("Interdictor")
        if e.get("IsThargoid"):
            who = "Thargoid"
        say = "Interdiction detected. Evade or submit."
        text = "⚠ BEING INTERDICTED" + (f" · {who}" if who else "")
        self.state.push_alert("critical", "interdiction", say, text)

    REBUY_COVER = 2  # warn when credits can't cover this many rebuys

    def _check_rebuy(self):
        """The most expensive lesson in the game: flying without rebuy money.
        One-shot callouts when the balance drops below 2x (warn) or 1x
        (critical) the ship's insurance cost; re-arms once covered again."""
        credits, rebuy = self.state.credits, self.state.rebuy
        if not rebuy or rebuy <= 0 or credits is None:
            return
        level = 2 if credits < rebuy else (1 if credits < rebuy * self.REBUY_COVER else 0)
        if level > self._rebuy_level and self._live:
            if level == 2:
                self.state.push_alert(
                    "critical", "rebuy",
                    "Warning. You cannot afford your rebuy. Fly safe.",
                    "⚠ REBUY NOT COVERED",
                )
            else:
                self.state.push_alert(
                    "warn", "rebuy",
                    f"Caution. Credits below {self.REBUY_COVER} rebuys.",
                    f"⚠ CREDITS BELOW {self.REBUY_COVER}× REBUY",
                )
        self._rebuy_level = level

    # Hull-damage tiers: (fraction ceiling, spoken/banner percent, level).
    _HULL_TIERS = ((0.25, 25, "critical"), (0.50, 50, "critical"), (0.75, 75, "warn"))

    def _on_hulldamage(self, e):
        """Significant hull loss on your own ship (not fighters/crew)."""
        if not self._live or not e.get("PlayerPilot", True) or e.get("Fighter"):
            return
        health = e.get("Health")
        if health is None:
            return
        for ceiling, pct, level in self._HULL_TIERS:
            if health <= ceiling and (self._hull_bucket is None or ceiling < self._hull_bucket):
                self._hull_bucket = ceiling
                self.state.push_alert(level, "hull", f"Warning. Hull at {pct} percent.", f"⚠ HULL {pct}%")
                return

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
        # Last-known DB prices for this station, to show a live-vs-recorded trend.
        try:
            prev_prices = marketdb.station_prices(data.get("MarketID"))
        except Exception:
            prev_prices = {}
        items = []
        for item in data.get("Items", []):
            stock = item.get("Stock", 0)
            demand = item.get("Demand", 0)
            buy = item.get("BuyPrice", 0)
            sell = item.get("SellPrice", 0)
            if not (stock or demand or buy or sell):
                continue
            symbol = (item.get("Name") or "").strip("$;").removesuffix("_name").lower()
            prev = prev_prices.get(symbol)
            items.append(
                {
                    "name": item.get("Name_Localised") or _clean_name(item.get("Name")),
                    "category": item.get("Category_Localised") or "",
                    "symbol": symbol,
                    "buy": buy,
                    "sell": sell,
                    "stock": stock,
                    "demand": demand,
                    "prev_sell": prev[0] if prev else None,
                    "prev_buy": prev[1] if prev else None,
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
        # Docking here makes this market history-worthy: start keeping price
        # points for it (this snapshot now, EDDN updates from anyone later).
        # Runs at bootstrap too — the last-visited market starts accumulating
        # immediately, with the snapshot's own (possibly old) timestamp.
        if data.get("MarketID") and items:
            try:
                marketdb.track_market(data["MarketID"])
                conn = marketdb.connect()
                try:
                    marketdb.record_price_history(
                        conn, data["MarketID"],
                        [(i["symbol"], i["buy"], i["sell"], i["stock"], i["demand"]) for i in items],
                        marketdb.parse_update_time(data.get("timestamp")),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass  # history is a nicety; never break market parsing

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
        self.state.update(journal_dir_found=True)
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
            if all(needed.values()) and len(selected) >= BOOTSTRAP_MIN_FILES:
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

    # Bump when the set of events swept below changes, to force a one-time
    # re-import of already-processed journals (all logging is INSERT OR IGNORE).
    HISTORY_VERSION = "2"

    # etype -> handler, for both the history sweep and the marker prefilter.
    _HISTORY_EVENTS = (
        "MarketBuy", "MarketSell", "LoadGame", "MissionCompleted",
        "SellExplorationData", "MultiSellExplorationData", "SellOrganicData",
        "RedeemVoucher",
    )

    def _import_event(self, event):
        etype = event.get("event")
        if etype == "MarketBuy":
            self._on_marketbuy(event)
        elif etype == "MarketSell":
            self._on_marketsell(event)
        elif etype == "LoadGame" and event.get("Credits") is not None:
            self._log_balance_point(
                marketdb.parse_update_time(event.get("timestamp")), event["Credits"]
            )
        elif etype == "MissionCompleted":
            self._log_income(event, "mission", event.get("Reward") or 0, event.get("Name"))
        elif etype in ("SellExplorationData", "MultiSellExplorationData"):
            self._log_income(event, "exploration",
                             event.get("TotalEarnings") or event.get("BaseValue"))
        elif etype == "SellOrganicData":
            total = sum((b.get("Value") or 0) + (b.get("Bonus") or 0)
                        for b in event.get("BioData") or [])
            self._log_income(event, "exobiology", total)
        elif etype == "RedeemVoucher":
            self._on_redeemvoucher(event)

    def import_trade_history(self):
        """One-time sweep of ALL journal files for trade/income/balance events, so
        analytics start with full history instead of just recent sessions."""
        if not self.journal_dir.is_dir():
            return
        conn = marketdb.connect()
        try:
            if marketdb.get_meta(conn, "history_version") != self.HISTORY_VERSION:
                conn.execute("DELETE FROM imported_journals")
                marketdb.set_meta(conn, "history_version", self.HISTORY_VERSION)
                conn.commit()
            done = {r[0] for r in conn.execute("SELECT filename FROM imported_journals")}
        finally:
            conn.close()
        files = journal_files(self.journal_dir)
        markers = tuple(f'"event":"{name}"' for name in self._HISTORY_EVENTS)
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
                    self._import_event(event)
            except OSError:
                continue
            conn = marketdb.connect()
            try:
                conn.execute("INSERT OR IGNORE INTO imported_journals(filename) VALUES(?)", (path.name,))
                conn.commit()
            finally:
                conn.close()

    def _ensure_journal_dir(self):
        """Recover from a missing or changed journal folder without a restart:
        re-resolve (the in-app setting may have changed, or the game's first
        launch may have just created the folder) and re-bootstrap on a switch."""
        if self._fixed_dir:
            return
        desired = find_journal_dir()
        changed = desired != self.journal_dir
        appeared = not self.state.journal_dir_found and self.journal_dir.is_dir()
        if not changed and not appeared:
            return
        if changed:
            if not desired.is_dir():
                self.state.update(journal_dir_found=False)
                self.journal_dir = desired  # keep watching; recovers if created
                return
            self.journal_dir = desired
        self._live = False
        self._status_mtimes = {}
        self.bootstrap()
        try:
            self.import_trade_history()
        except Exception:
            pass
        self._live = True
        self._fetch_community_bio(self.state.system_address, self.state.system)

    def run_forever(self):
        self.bootstrap()
        try:
            self.import_trade_history()
        except Exception:
            pass
        self._live = True
        # Bootstrap set the current system without a live event, so fetch its
        # community bio data now.
        self._fetch_community_bio(self.state.system_address, self.state.system)
        while True:
            try:
                self._ensure_journal_dir()
                self._poll_journal()
                self._refresh_status_files()
            except Exception:
                pass
            time.sleep(POLL_SECONDS)

    def start(self):
        thread = threading.Thread(target=self.run_forever, name="journal-watcher", daemon=True)
        thread.start()
        return thread
