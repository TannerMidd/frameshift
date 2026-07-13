"""Reads the Elite Dangerous journal directory: bootstraps state from recent
session logs, then tails the newest journal + Status/Cargo/Market json files."""

import json
import hashlib
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

from . import biovalues, exploration, flight, launcher, marketdb

log = logging.getLogger(__name__)

BIO_SIGNAL_TYPE = "$SAA_SignalType_Biological;"
GAME_PROBE_SECONDS = 15  # process-probe cadence; catches exits with no Shutdown event

DEFAULT_JOURNAL_DIR = (
    Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"
)
BOOTSTRAP_MAX_FILES = 25
BOOTSTRAP_MIN_FILES = 12  # context like colonization depots spans sessions

# Events the game writes without a pilot (launcher/menu chrome). A completed
# journal containing nothing else is a login-less stub.
_SESSION_CHROME_EVENTS = frozenset({"Fileheader", "Shutdown", "Music", "Continued"})
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


_MK_SUFFIX = re.compile(r"\bMk(i{1,3}|iv|v)\b", re.IGNORECASE)


def _clean_name(raw):
    """Turn an internal name like '$gold_name;' into 'Gold'. Ship type stems
    ('krait_mkii') title-case into 'Krait Mkii', so mark-suffixes are mended
    to the in-game style ('Krait Mk II')."""
    if not raw:
        return ""
    name = raw.strip("$;")
    if name.endswith("_name"):
        name = name[: -len("_name")]
    name = name.replace("_", " ").title()
    return _MK_SUFFIX.sub(lambda m: "Mk " + m.group(1).upper(), name)


def _pretty_panel_name(raw):
    """Prettify station/destination tokens the game never localises, like
    "$EXT_PANEL_ColonisationShip; Nansen Claim" → "Colonisation Ship Nansen
    Claim". Plain names pass through untouched."""
    if not raw or not raw.startswith("$"):
        return raw
    token, _, rest = raw.partition(";")
    token = token.strip("$").removeprefix("EXT_PANEL_")
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", token).replace("_", " ").title()
    return f"{words} {rest.strip()}".strip()


def _galaxy_mode(game_version):
    """Classify Frontier's journal version without guessing unknown builds."""
    value = str(game_version or "").strip().casefold()
    if "legacy" in value or value.startswith("3."):
        return "legacy"
    if "live" in value or value.startswith("4."):
        return "live"
    return "unknown"


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
        self._public_state = state
        self.state = state
        self._fixed_dir = journal_dir is not None  # explicit dir: never re-detect
        self.journal_dir = Path(journal_dir) if journal_dir else find_journal_dir()
        self._current_file = None
        self._offset = 0
        self._partial = ""
        self._line_number = 0
        self._status_mtimes = {}
        self._body_scans = {}  # body name -> details, current system only
        self._body_ids = {}    # BodyID -> body name, current system only
        self._live = False  # False during bootstrap replay, True while tailing
        self._last_logged_balance = None
        self._bio_fetched = set()  # id64s we've queried Spansh for this session
        self._hull_bucket = None   # lowest hull-damage tier already called out
        self._first_disc_system = None  # system a first-discovery alert fired for
        self._risk_level = 0       # unsold-data risk tier already called out
        self._rebuy_level = 0      # 0 = covered, 1 = below 2x rebuy, 2 = below 1x
        self._sample_clear_said = True  # per-sample-point "clear to sample" callout
        self._commander_id = None
        self._event_ledger = None
        self._timing_model = None
        self._extension_sink = None
        self._extension_unsubscribe = None
        self._specialists = None
        self._pending_local_events = []
        # EDDN augmentation must use one trusted tuple sourced only from an
        # event that carries the complete location. UI state may legitimately
        # learn a newer system name from Docked without learning coordinates;
        # keeping this separate prevents pairing that name with stale StarPos.
        self._eddn_location = {
            "system": None, "system_address": None, "star_pos": None,
        }
        self._eddn_status_body_name = None
        self._eddn_journal_body_name = None
        self._eddn_journal_body_id = None
        self._last_background_error = None
        self._last_background_error_at = 0.0
        self._track_rebuild = False
        self._rebuild_attempt = 0
        self._staged_bootstrap_state = None
        self._reconstruction_pending = False
        self._profile_handoff_pending = False
        self._handoff_commander_id = None
        self._handoff_state = None
        self._stop_event = threading.Event()
        self._thread = None
        self._thread_lock = threading.Lock()

    def _begin_rebuild(self):
        self._track_rebuild = True
        self._rebuild_attempt = 0
        self._public_state.set_journal_rebuild(
            active=True, phase="preparing", completed=0, total=0,
            current=None, attempt=0, retrying=False,
        )

    def _set_rebuild_progress(self, phase, completed=0, total=0, current=None):
        if not self._track_rebuild:
            return
        self._public_state.set_journal_rebuild(
            active=True, phase=phase, completed=completed, total=total,
            current=Path(current).name if current else None,
            attempt=self._rebuild_attempt, retrying=False,
        )

    def _mark_rebuild_retry(self):
        if not self._track_rebuild:
            return
        self._rebuild_attempt += 1
        self._public_state.set_journal_rebuild(
            active=True, attempt=self._rebuild_attempt, retrying=True,
        )

    def _finish_rebuild(self):
        if self._track_rebuild:
            self._public_state.set_journal_rebuild(
                active=False, phase="complete",
                current=None, retrying=False,
            )
        self._track_rebuild = False

    def _publish_staged_bootstrap(self):
        staged = self._staged_bootstrap_state
        if staged is None:
            return False
        self._public_state.replace_from(
            staged,
            preserve=("game_running", "journal_dir_found", "journal_rebuild"),
        )
        self._staged_bootstrap_state = None
        return True

    # ---------- event handling ----------

    def handle_event(self, event, *, source_file=None, source_line=None):
        etype = event.get("event")
        try:
            if self._live and etype in {"Location", "FSDJump", "CarrierJump"}:
                self._preflush_public_signals()
            handler = getattr(self, f"_on_{etype.lower()}", None) if etype else None
            if handler:
                handler(event)
            if event.get("timestamp"):
                self.state.update(last_journal_event=event["timestamp"])
            # A live journal line can only come from a running game; Shutdown is
            # the game announcing the opposite (its handler just set it False).
            if self._live and etype and etype != "Shutdown":
                self.state.update(game_running=True)
            if self._live and etype:
                self._publish_public_event(event)
        finally:
            # The durable ledger is deliberately independent of individual
            # feature handlers: even an unexpected handler failure must not
            # punch a permanent hole in lifetime history.
            self._record_local_event(event, source_file=source_file, source_line=source_line)

    def _activate_commander(self, name):
        """Switch every durable local reducer to the journal's commander."""
        if not name:
            return
        mode = self.state.galaxy_mode if self.state.galaxy_mode in {"live", "legacy"} else "live"
        commander_id = marketdb.commander_profile_id(name, mode)
        if commander_id == self._commander_id and self._event_ledger is not None:
            if self._profile_handoff_pending and self._handoff_state is not None:
                self.state.restore_commander_context(self._handoff_state)
            self._finish_profile_handoff()
            marketdb.ensure_commander_profile(
                name, commander_id=commander_id, galaxy_mode=mode)
            self.state.update(commander=name, commander_id=commander_id)
            self._flush_pending_local_events()
            return
        if self._extension_unsubscribe:
            self._extension_unsubscribe()
            self._extension_unsubscribe = None
        # Clear the previous pilot before changing the process-wide active DB
        # profile.  A brief empty snapshot is safe; serving Alpha's missions,
        # cargo or surface coordinates under Beta's identity is not.
        self.state.reset_commander_context()
        self._reset_profile_context()
        marketdb.ensure_commander_profile(
            name, commander_id=commander_id, galaxy_mode=mode)
        self.state.update(commander=name, commander_id=commander_id)
        from .eventledger import EventLedger
        from .extensions import EXTENSIONS
        from .objectives import ExtensionActionSink
        from .specialists import SpecialistWorkflows
        from .timings import TimingModel

        self._commander_id = commander_id
        self._event_ledger = EventLedger(commander_id)
        self._timing_model = TimingModel(commander_id)
        self._extension_sink = ExtensionActionSink(
            commander_id,
            alert_callback=lambda alert: self.state.push_alert(
                alert.get("level") or "info",
                alert.get("code") or "extension",
                alert.get("say") or alert.get("text"),
                alert.get("text"),
            ),
        )
        self._specialists = SpecialistWorkflows(commander_id)
        self._extension_unsubscribe = EXTENSIONS.subscribe(self._extension_sink.accept)
        self.state.update(
            commander=name, commander_id=commander_id,
            specialists=self._specialists.snapshot(),
        )
        self._finish_profile_handoff()
        self._flush_pending_local_events()

    def _record_local_event(self, event, *, source_file=None, source_line=None):
        if not isinstance(event, dict) or not event.get("event"):
            return
        if self._profile_handoff_pending or self._event_ledger is None:
            # Fileheader normally precedes Commander/LoadGame. Hold that small
            # prefix so it lands in the correct profile instead of "default".
            if len(self._pending_local_events) < 64:
                self._pending_local_events.append((dict(event), source_file, source_line))
            return
        try:
            source = source_file or (self._current_file.name if self._current_file else None)
            dedupe_key = f"{source}:{source_line}" if source and source_line is not None else None
            saved = self._event_ledger.record(
                event, source_file=source, source_line=source_line, dedupe_key=dedupe_key)
            from .specialists import EXPECTED_JOURNAL_EVENTS

            if self._specialists and any(
                    event.get("event") in expected for expected in EXPECTED_JOURNAL_EVENTS.values()):
                carrier = self.state.carrier or {}
                own_names = {
                    str(value).casefold() for value in (
                        carrier.get("callsign"), carrier.get("name"), carrier.get("Callsign"),
                    ) if value
                }
                at_own_carrier = bool(
                    self.state.docked and self.state.station
                    and str(self.state.station).casefold() in own_names
                )
                workflow = self._specialists.observe_event(
                    event, saved["event_uid"], context={"at_own_carrier": at_own_carrier})
                self.state.update(specialists=workflow["snapshot"])
            if saved.get("inserted"):
                self._timing_model.observe_event(event)
                if self._live:
                    from .extensions import EXTENSIONS

                    EXTENSIONS.publish(event, self.state.snapshot())
        except Exception as exc:
            log.warning("local event intelligence failed: %s", type(exc).__name__, exc_info=True)

    def _reset_eddn_context(self):
        self._eddn_location = {
            "system": None, "system_address": None, "star_pos": None,
        }
        self._eddn_status_body_name = None
        self._eddn_journal_body_name = None
        self._eddn_journal_body_id = None

    def _begin_profile_handoff(self):
        """Hide the old cockpit and buffer a new file until identity arrives."""
        if self._profile_handoff_pending:
            return
        self._profile_handoff_pending = True
        self._handoff_commander_id = self._commander_id
        if self._commander_id is not None:
            self._handoff_state = self.state.capture_commander_context()
            self.state.reset_commander_context()

    def _finish_profile_handoff(self):
        self._profile_handoff_pending = False
        self._handoff_commander_id = None
        self._handoff_state = None

    def _flush_pending_local_events(self):
        pending, self._pending_local_events = self._pending_local_events, []
        for queued, source_file, source_line in pending:
            self._record_local_event(
                queued, source_file=source_file, source_line=source_line)

    def _reset_profile_context(self):
        """Reset watcher caches that are meaningful only for one commander."""
        self._commander_id = None
        self._event_ledger = None
        self._timing_model = None
        self._extension_sink = None
        self._specialists = None
        self._status_mtimes = {}
        self._body_scans = {}
        self._body_ids = {}
        self._last_logged_balance = None
        self._bio_fetched = set()
        self._hull_bucket = None
        self._first_disc_system = None
        self._risk_level = 0
        self._rebuy_level = 0
        self._sample_clear_said = True
        self._reset_eddn_context()

    def _log_background_failure(self, context, exc):
        """Persist recurring watcher failures without flooding bounded logs."""
        now = time.monotonic()
        key = (str(context), type(exc).__name__)
        if key != self._last_background_error or now - self._last_background_error_at >= 60:
            self._last_background_error = key
            self._last_background_error_at = now
            log.warning("%s failed: %s", context, type(exc).__name__, exc_info=True)

    def _remember_eddn_location(self, event):
        """Atomically replace, never partially merge, the augmentation tuple."""
        old = self._eddn_location
        new_system = event.get("StarSystem")
        new_address = event.get("SystemAddress")
        if old.get("system_address") is not None and (
            old.get("system_address") != new_address
            or str(old.get("system") or "").casefold() != str(new_system or "").casefold()
        ):
            # Status.json can lag a jump. Never carry its old surface body into
            # a Codex observation in the newly trusted system.
            self._eddn_status_body_name = None
            self._eddn_journal_body_name = None
            self._eddn_journal_body_id = None
        self._eddn_location = {
            "system": new_system,
            "system_address": new_address,
            "star_pos": event.get("StarPos"),
        }

    def _remember_journal_body(self, event):
        name = event.get("BodyName") or event.get("Body")
        body_id = event.get("BodyID")
        if name:
            self._eddn_journal_body_name = name
            self._eddn_journal_body_id = (
                body_id if isinstance(body_id, int) and not isinstance(body_id, bool) else None
            )
        else:
            self._eddn_journal_body_name = None
            self._eddn_journal_body_id = None

    def _preflush_public_signals(self):
        """Finish Horizons-order FSS batches before replacing their location."""
        try:
            from .eddn_upload import UPLOADER

            UPLOADER.flush_fss_signals(
                dict(self._eddn_location), self.state.commander,
                preserve_unmatched=True,
            )
        except Exception as exc:
            log.debug("EDDN pre-location signal flush skipped: %s", exc, exc_info=True)

    def _publish_public_event(self, event):
        """Contribute supported live observations to EDDN, never replay data."""
        try:
            from .eddn_upload import UPLOADER

            location = dict(self._eddn_location)
            status_body = self._eddn_status_body_name
            if (
                status_body and self._eddn_journal_body_name
                and str(status_body).casefold()
                == str(self._eddn_journal_body_name).casefold()
            ):
                # Codex body data is supplied only when independent Status and
                # journal names agree. A known ID remains optional.
                location["body_name"] = status_body
                if self._eddn_journal_body_id is not None:
                    location["body_id"] = self._eddn_journal_body_id
            UPLOADER.maybe_publish_journal(
                event,
                self.state.commander,
                location=location,
                game_version=self.state.game_version,
                game_build=self.state.game_build,
                horizons=self.state.horizons,
                odyssey=self.state.odyssey,
            )
        except Exception as exc:
            log.debug("EDDN journal publication skipped: %s", exc, exc_info=True)

    def _on_shutdown(self, e):
        # A replayed Shutdown only says that a *past* session ended. Treating
        # it as a current process observation flashes a false offline banner
        # during startup (and can persist through a long history import if the
        # OS probe is unavailable). Live Shutdown remains authoritative.
        if self._live:
            self.state.update(game_running=False)
        # Freeze the session clock: duration/cr-per-hour shouldn't keep
        # counting wall time after you stop playing.
        self.state.end_session(marketdb.parse_update_time(e.get("timestamp")))

    def _on_commander(self, e):
        self._reset_eddn_context()
        self._activate_commander(e.get("Name"))
        self.state.update(commander=e.get("Name"))

    def _on_fileheader(self, e):
        # EDDN uploads must carry the game version so consumers can tell
        # Live (4.x) from Legacy (3.x) data.
        self._begin_profile_handoff()
        self._reset_eddn_context()
        self.state.update(
            game_version=e.get("gameversion"),
            # Frontier build strings can contain significant whitespace; EDDN
            # explicitly requires senders to pass the value through unchanged.
            game_build=e.get("build") if e.get("build") is not None else "",
            galaxy_mode=_galaxy_mode(e.get("gameversion")),
            # Fileheader flags identify the client, not the commander's active
            # entitlements. Only LoadGame is a valid source for EDDN flags.
            horizons=None,
            odyssey=None,
        )

    def _on_loadgame(self, e):
        if self.state.commander and e.get("Commander") != self.state.commander:
            self._reset_eddn_context()
        self._activate_commander(e.get("Commander"))
        updates = {"commander": e.get("Commander")}
        self.state.update(
            horizons=bool(e["Horizons"]) if "Horizons" in e else None,
            odyssey=bool(e["Odyssey"]) if "Odyssey" in e else None,
        )
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
            # Keep the whole event: it's the ship's full module list, which is
            # exactly what EDSY/Coriolis/Inara import (see shipexport.py).
            loadout_raw=dict(e),
        )
        self._check_rebuy()

    def _on_location(self, e):
        if e.get("StarSystem") != self.state.system:
            self._body_scans = {}
            self._body_ids = {}
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
        self._remember_eddn_location(e)
        self._remember_journal_body(e)
        self._capture_system_politics(e)
        self._fetch_community_bio(e.get("SystemAddress"), e.get("StarSystem"))

    def _on_fsdjump(self, e):
        self._body_scans = {}
        self._body_ids = {}
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
        self._remember_eddn_location(e)
        self._eddn_journal_body_name = None
        self._eddn_journal_body_id = None
        self._capture_system_politics(e)
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
            self._body_ids = {}
            self.state.update(bio_signals={})
        self.state.update(
            system=e.get("StarSystem"),
            system_address=e.get("SystemAddress"),
            star_pos=e.get("StarPos"),
            body=e.get("Body"),
        )
        self._remember_eddn_location(e)
        self._remember_journal_body(e)
        # If this was OUR carrier completing its scheduled jump, the pending
        # entry is done. (The event only fires while aboard; destination match
        # keeps someone else's carrier from clearing ours.)
        carrier = self.state.carrier
        if carrier and carrier.get("jump") and carrier["jump"].get("system") == e.get("StarSystem"):
            self._update_carrier(jump=None)
        self._capture_system_politics(e)
        self._fetch_community_bio(e.get("SystemAddress"), e.get("StarSystem"))

    def _on_approachbody(self, e):
        self._remember_journal_body(e)

    def _on_leavebody(self, e):
        self._eddn_journal_body_name = None
        self._eddn_journal_body_id = None

    def _capture_system_politics(self, e):
        """BGS factions/conflicts and Powerplay status carried on every
        Location/FSDJump/CarrierJump. Unpopulated systems carry none of it, so
        empty lists correctly clear the previous system's data."""
        factions = []
        for f in e.get("Factions") or []:
            factions.append({
                "name": f.get("Name"),
                "state": f.get("FactionState"),
                "government": f.get("Government"),
                "influence": f.get("Influence"),
                "allegiance": f.get("Allegiance"),
                "my_reputation": f.get("MyReputation"),
                "active_states": [s.get("State") for s in f.get("ActiveStates") or []],
                "pending_states": [s.get("State") for s in f.get("PendingStates") or []],
                "recovering_states": [s.get("State") for s in f.get("RecoveringStates") or []],
            })
        factions.sort(key=lambda f: -(f["influence"] or 0))

        def _side(side):
            side = side or {}
            return {"name": side.get("Name"), "stake": side.get("Stake"),
                    "won_days": side.get("WonDays")}

        conflicts = [{
            "war_type": c.get("WarType"),
            "status": c.get("Status"),
            "faction1": _side(c.get("Faction1")),
            "faction2": _side(c.get("Faction2")),
        } for c in e.get("Conflicts") or []]

        pp = None
        if e.get("ControllingPower") or e.get("Powers"):
            pp = {
                "controlling": e.get("ControllingPower"),
                "powers": e.get("Powers") or [],
                "state": e.get("PowerplayState"),
                "control_progress": e.get("PowerplayStateControlProgress"),
                "reinforcement": e.get("PowerplayStateReinforcement"),
                "undermining": e.get("PowerplayStateUndermining"),
                "conflict_progress": [
                    {"power": p.get("Power"), "progress": p.get("ConflictProgress")}
                    for p in e.get("PowerplayConflictProgress") or []
                ],
            }

        self.state.update(
            factions=factions,
            conflicts=conflicts,
            pp_system=pp,
            controlling_faction=(e.get("SystemFaction") or {}).get("Name"),
        )

    def _on_docked(self, e):
        self._hull_bucket = None  # repairs are available; let damage re-announce
        self.state.update(
            system=e.get("StarSystem"),
            system_address=e.get("SystemAddress"),
            docked=True,
            station=_pretty_panel_name(e.get("StationName_Localised") or e.get("StationName")),
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
        if self.state.galaxy_mode != "live":
            # Spansh is the Live galaxy.  Never merge its body observations
            # into a Legacy commander's otherwise local surface workflow.
            if self.state.bio_community:
                self.state.update(bio_community={})
            return
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
            if self.state.galaxy_mode == "live" and self.state.system_address == id64:
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
        if e.get("BodyID") is not None:
            self._body_ids[e["BodyID"]] = body
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
            first = not e.get("WasDiscovered", True)
            scans[body] = {
                "body": body,
                "base": base,
                "first": first,
                "mapped": prev.get("mapped", False) if prev else False,
                "class": e.get("PlanetClass") or e.get("StarType"),
            }
            self.state.update(explo_scans=scans)
            if prev is None:  # count each body once, however often it's re-scanned
                self.state.add_collected(round(base * (2.6 if first else 1)))
            self._check_data_risk()

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
            # Nobody had discovered this body before you scanned it — any bio
            # you log here is almost certainly a first (5x at Vista Genomics).
            "was_discovered": bool(e.get("WasDiscovered", True)),
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
        self._check_data_risk()  # pile shrank: re-arm the at-risk ladder

    def _on_multisellexplorationdata(self, e):
        self.state.update(explo_scans={})
        self._log_income(e, "exploration", e.get("TotalEarnings") or e.get("BaseValue"))
        self._check_data_risk()

    def _likely_first_log(self, genus, body):
        """Best-effort guess at the Vista Genomics 'first logged' 5x bonus
        (the journal never says at scan time — only SellOrganicData's Bonus
        field confirms it). Two signals: if another commander already reported
        this genus on this body via EDDN it is NOT yours; if nobody had even
        discovered the body when you scanned it, it almost certainly is."""
        if not body:
            return False
        community = self.state.bio_community or {}
        if community.get("id64") == self.state.system_address:
            entry = (community.get("bodies") or {}).get(body) or {}
            if any(g.get("name") == genus for g in entry.get("genuses") or []):
                return False
        scan = self._body_scans.get(body) or {}
        return scan.get("was_discovered") is False

    def _on_scanorganic(self, e):
        species = e.get("Species_Localised") or _clean_name(e.get("Species"))
        genus = e.get("Genus_Localised") or _clean_name(e.get("Genus"))
        variant = e.get("Variant_Localised")
        scan_type = e.get("ScanType")
        # ScanOrganic carries the body as an ID; our own Scan records name it.
        # state.body is only a fallback — it can be stale mid-session.
        body = self._body_ids.get(e.get("Body")) or self.state.body
        if scan_type in ("Log", "Sample"):
            prev = self.state.bio_sampling or {}
            same = prev.get("species") == species
            progress = 1 if scan_type == "Log" else (min(3, (prev.get("progress") or 1) + 1) if same else 2)
            # Remember where this sample was taken (live Status.json position):
            # the next sample must be >= the genus's colony distance from every
            # previous one, and the distance readout measures against these.
            # A Log or a species switch starts a fresh set.
            points = list(self.state.bio_sample_points) if (scan_type == "Sample" and same) else []
            pos = self.state.pos
            if pos and pos.get("lat") is not None:
                points.append({"lat": pos["lat"], "lon": pos["lon"], "body": pos.get("body")})
            self._sample_clear_said = not points  # re-arm the callout for this point
            self.state.update(bio_sample_points=points, bio_sampling={
                "genus": genus, "species": species, "variant": variant,
                "progress": progress,
                "colony_m": biovalues.GENUS_COLONY_M.get(genus),
                "value": biovalues.species_value(species),
                "first": self._likely_first_log(genus, body),
            })
        elif scan_type == "Analyse":
            value = biovalues.species_value(species) or biovalues.genus_info(genus).get("min_value") or 0
            first = self._likely_first_log(genus, body)
            vault = list(self.state.bio_vault)
            vault.append({
                "species": species, "genus": genus, "variant": variant,
                "value": value, "body": body,
                # Snapshot the estimate now: body flags and community data are
                # both session-scoped, so deciding later would forget.
                "first": first,
            })
            self.state.update(bio_vault=vault, bio_sampling=None, bio_sample_points=[])
            self._sample_clear_said = True
            self.state.add_collected(value * (5 if first else 1))
            self._check_data_risk()

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
        except Exception as exc:
            self._log_background_failure("market-buy analytics", exc)

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
        except Exception as exc:
            self._log_background_failure("market-sell analytics", exc)

    def _log_balance_point(self, ts, balance):
        try:
            marketdb.log_balance(ts, balance)
        except Exception as exc:
            self._log_background_failure("balance analytics", exc)

    def _log_income(self, e, category, amount, detail=None):
        try:
            marketdb.log_income(
                marketdb.parse_update_time(e.get("timestamp")), category, amount, detail
            )
        except Exception as exc:
            self._log_background_failure("income analytics", exc)

    def _on_sellorganicdata(self, e):
        total = sum((b.get("Value") or 0) + (b.get("Bonus") or 0) for b in e.get("BioData") or [])
        self._log_income(e, "exobiology", total)
        self.state.update(bio_vault=[], bio_sampling=None, bio_sample_points=[])
        self._check_data_risk()  # pile shrank: re-arm the at-risk ladder

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

    def _update_mission(self, mission_id, **fields):
        """Merge fields into a tracked mission; unknown ids are ignored (wing
        depot events can reference missions accepted before we watched)."""
        mission = self.state.missions.get(mission_id)
        if not mission:
            return
        missions = dict(self.state.missions)
        missions[mission_id] = {**mission, **fields}
        self.state.update(missions=missions)

    def _on_cargodepot(self, e):
        """Progress on haulage missions (delivery/collection, wing shared):
        'X of Y delivered' for the mission board. Fires on your own deliveries
        (UpdateType Collect/Deliver) and on wingmates' (WingUpdate)."""
        self._update_mission(
            e.get("MissionID"),
            collected=e.get("ItemsCollected"),
            delivered=e.get("ItemsDelivered"),
            to_deliver=e.get("TotalItemsToDeliver"),
        )

    def _on_missionredirected(self, e):
        """The game retargets a mission (e.g. all cargo delivered -> report to
        the reward station). Keep the board's destination current; absent
        fields must not clobber the ones we have."""
        fields = {}
        if e.get("NewDestinationSystem"):
            fields["dest_system"] = e["NewDestinationSystem"]
        if e.get("NewDestinationStation"):
            fields["dest_station"] = e["NewDestinationStation"]
        if fields:
            self._update_mission(e.get("MissionID"), **fields)

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

    # ---------- engineers, fleet & carrier ----------

    def _on_engineerprogress(self, e):
        """Engineer access. One batch event at startup (Engineers array), then
        single events as progress changes (invite, unlock, rank-up)."""
        def entry(rec):
            return {
                "progress": rec.get("Progress"),
                "rank": rec.get("Rank"),
                "rank_progress": rec.get("RankProgress"),
            }

        if e.get("Engineers") is not None:
            engineers = {
                rec["Engineer"]: entry(rec)
                for rec in e["Engineers"] if rec.get("Engineer")
            }
            self.state.update(engineers=engineers)
        elif e.get("Engineer"):
            engineers = dict(self.state.engineers)
            engineers[e["Engineer"]] = entry(e)
            self.state.update(engineers=engineers)

    @staticmethod
    def _stored_ship(rec):
        return {
            "type": rec.get("ShipType_Localised") or _clean_name(rec.get("ShipType")),
            "name": rec.get("Name") or None,
            "value": rec.get("Value"),
            "hot": bool(rec.get("Hot")),
            "system": rec.get("StarSystem") or None,       # remote ships only
            "transfer_cr": rec.get("TransferPrice"),
            "transfer_s": rec.get("TransferTime"),
            "in_transit": bool(rec.get("InTransit")),
        }

    def _on_storedships(self, e):
        """Fleet overview, sent whenever a shipyard is opened."""
        self.state.update(stored_ships={
            "station": e.get("StationName"),
            "system": e.get("StarSystem"),
            "here": [self._stored_ship(r) for r in e.get("ShipsHere") or []],
            "remote": [self._stored_ship(r) for r in e.get("ShipsRemote") or []],
            "updated": e.get("timestamp"),
        })

    def _update_carrier(self, **fields):
        carrier = dict(self.state.carrier or {})
        carrier.update(fields)
        self.state.update(carrier=carrier)

    def _on_carrierstats(self, e):
        """The owner's fleet carrier, sent when carrier management is opened."""
        space = e.get("SpaceUsage") or {}
        finance = e.get("Finance") or {}
        self._update_carrier(
            callsign=e.get("Callsign"),
            name=e.get("Name"),
            fuel_t=e.get("FuelLevel"),
            balance=finance.get("CarrierBalance"),
            reserve=finance.get("ReserveBalance"),
            capacity=space.get("TotalCapacity"),
            free_space=space.get("FreeSpace"),
            updated=e.get("timestamp"),
        )

    def _on_carrierjumprequest(self, e):
        self._update_carrier(jump={
            "system": e.get("SystemName"),
            "body": e.get("Body") or None,
            "departure_ts": marketdb.parse_update_time(e.get("DepartureTime")),
        })

    def _on_carrierjumpcancelled(self, e):
        self._update_carrier(jump=None)

    def _on_carrierdepositfuel(self, e):
        if e.get("Total") is not None:
            self._update_carrier(fuel_t=e.get("Total"))

    # ---------- Powerplay / community goals / squadron ----------

    def _update_powerplay(self, **fields):
        pp = dict(self.state.powerplay or {"session_merits": 0})
        pp.update(fields)
        self.state.update(powerplay=pp)

    def _on_powerplay(self, e):
        # Startup snapshot: authoritative for everything but session merits.
        self._update_powerplay(
            power=e.get("Power"),
            rank=e.get("Rank"),
            merits=e.get("Merits"),
            time_pledged_s=e.get("TimePledged"),
        )

    def _on_powerplayjoin(self, e):
        self.state.update(powerplay={
            "power": e.get("Power"), "rank": 0, "merits": 0,
            "time_pledged_s": 0, "session_merits": 0,
        })

    def _on_powerplaydefect(self, e):
        self.state.update(powerplay={
            "power": e.get("ToPower"), "rank": 0, "merits": 0,
            "time_pledged_s": 0, "session_merits": 0,
        })

    def _on_powerplayleave(self, e):
        self.state.update(powerplay=None)

    def _on_powerplayrank(self, e):
        self._update_powerplay(power=e.get("Power"), rank=e.get("Rank"))

    def _on_powerplaymerits(self, e):
        pp = self.state.powerplay or {}
        self._update_powerplay(
            power=e.get("Power"),
            merits=e.get("TotalMerits"),
            session_merits=(pp.get("session_merits") or 0) + (e.get("MeritsGained") or 0),
        )

    def _on_communitygoal(self, e):
        # Each event is a full snapshot of every goal you've signed up for.
        goals = {}
        for g in e.get("CurrentGoals") or []:
            cgid = g.get("CGID")
            if cgid is None:
                continue
            goals[cgid] = {
                "cgid": cgid,
                "title": g.get("Title"),
                "system": g.get("SystemName"),
                "market": g.get("MarketName"),
                "expiry": g.get("Expiry"),
                "complete": bool(g.get("IsComplete")),
                "current_total": g.get("CurrentTotal"),
                "contribution": g.get("PlayerContribution"),
                "contributors": g.get("NumContributors"),
                "percentile": g.get("PlayerPercentileBand"),
                "tier": (g.get("TierReached") or "").replace("Tier ", "") or None,
                "top_rank": bool(g.get("PlayerInTopRank")),
                "bonus": g.get("Bonus"),
            }
        self.state.update(community_goals=goals)

    def _on_squadronstartup(self, e):
        self.state.update(squadron={
            "name": e.get("SquadronName"), "rank": e.get("CurrentRank"),
        })

    def _on_leftsquadron(self, e):
        self.state.update(squadron=None)

    def _on_disbandedsquadron(self, e):
        self.state.update(squadron=None)

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

    def _engineering_wishlist(self):
        """Return the established commander's normalized engineering pins."""
        if not self._commander_id:
            return []
        from . import blueprints, wishlist

        try:
            saved, _adopted = wishlist.load(self._commander_id)
        except ValueError:
            return []
        normalized, _changed = blueprints.normalize_wishlist(saved)
        return normalized

    def _pinned_craftable(self, pins=None):
        """Names of pinned blueprints whose full climb is covered right now."""
        from . import blueprints

        inventory = {}
        for cat in self.state.materials.values():
            for sym, m in cat.items():
                inventory[sym] = m.get("count", 0)
        out = set()
        for p in pins if pins is not None else self._engineering_wishlist():
            try:
                if blueprints.plan(p["name"], p.get("grade", 5), inventory)["craftable"]:
                    out.add(p["name"])
            except KeyError:
                continue
        return out

    def _on_materialcollected(self, e):
        pins = self._engineering_wishlist() if self._live else []
        before = self._pinned_craftable(pins) if pins else set()
        self._adjust_material(e.get("Category"), e, +1)
        if not self._live:
            return
        # A pickup that completes a pinned blueprint's shopping list is worth
        # announcing — that's the moment you can stop farming.
        newly_ready = self._pinned_craftable(pins) - before
        if newly_ready:
            grades = {p["name"]: p.get("grade", 5) for p in pins}
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
        self.state.update(bio_vault=[], bio_sampling=None, bio_sample_points=[],
                          explo_scans={})

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
            ("Outfitting.json", self._apply_outfitting),
            ("Shipyard.json", self._apply_shipyard),
            ("FCMaterials.json", self._apply_fcmaterials),
            ("NavRoute.json", self._apply_navroute),
            ("ShipLocker.json", self._apply_shiplocker),
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
        # This is the independent body-name side of EDDN's Codex cross-check.
        # Clear it whenever Status omits BodyName; never infer from coordinates.
        self._eddn_status_body_name = data.get("BodyName") or None
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
        updates["destination"] = _pretty_panel_name(dest.get("Name_Localised") or dest.get("Name")) or None
        # Surface position (present only near/on a body): drives the exobio
        # sample-distance readout. The game omits the keys when not applicable.
        if data.get("Latitude") is not None and data.get("Longitude") is not None:
            updates["pos"] = {
                "lat": data["Latitude"],
                "lon": data["Longitude"],
                "body": data.get("BodyName"),
                "radius_m": data.get("PlanetRadius"),
                "heading": data.get("Heading"),
                "alt_m": data.get("Altitude"),
            }
        else:
            updates["pos"] = None
        self.state.update(**updates)
        if self._specialists:
            try:
                self.state.update(specialists=self._specialists.update_status(self.state.pos))
            except Exception as exc:
                log.debug("specialist position update skipped: %s", type(exc).__name__)
        self._check_sample_clear()
        if balance is not None:
            self._check_rebuy()

    def _check_sample_clear(self):
        """One-shot spoken callout when the commander has walked/driven far
        enough from every previous sample of the species in progress. Re-armed
        each time a new sample is taken (see _on_scanorganic)."""
        if self._sample_clear_said or not self._live:
            return
        samp = self.state.bio_sampling
        if not samp or not samp.get("colony_m"):
            return
        clearance = flight.sample_clearance(
            self.state.bio_sample_points, self.state.pos, samp["colony_m"]
        )
        if clearance and clearance["clear"]:
            self._sample_clear_said = True
            self.state.push_alert(
                "info", "sample_clear",
                f"Clear to sample. You are far enough from your previous {samp.get('genus') or 'sample'}.",
                f"⬡ CLEAR TO SAMPLE · {samp.get('species') or ''} · ≥{samp['colony_m']} m",
            )

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
        try:
            from .eddn_upload import UPLOADER

            UPLOADER.maybe_publish_snapshot(
                "navroute", data, self.state.commander,
                self.state.game_version, self.state.game_build,
                horizons=self.state.horizons, odyssey=self.state.odyssey,
            )
        except Exception as exc:
            log.debug("EDDN nav route publication skipped: %s", exc, exc_info=True)

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

    # Unsold data below this is never worth a callout, whatever the rebuy —
    # keeps early-game ships with tiny insurance costs quiet.
    RISK_FLOOR_CR = 20_000_000

    def _unsold_data_cr(self):
        """Everything a rebuy screen would erase: completed bio samples at
        Vista value (first logs at 5x) plus unsold cartographic estimates."""
        bio = sum(
            (i.get("value") or 0) * (5 if i.get("first") else 1)
            for i in self.state.bio_vault
        )
        explo = sum(exploration.effective_value(e) for e in self.state.explo_scans.values())
        return bio + explo

    def _check_data_risk(self):
        """The exobiology heartbreak: dying with hundreds of millions in
        unsold data aboard. One-shot callouts as the pile crosses 10x / 25x /
        50x the ship's rebuy; the ladder re-arms after selling."""
        rebuy = self.state.rebuy
        if not rebuy or rebuy <= 0:
            return
        at_risk = self._unsold_data_cr()
        ratio = at_risk / rebuy if at_risk >= self.RISK_FLOOR_CR else 0
        level = 3 if ratio >= 50 else 2 if ratio >= 25 else 1 if ratio >= 10 else 0
        if level > self._risk_level and self._live:
            millions = round(at_risk / 1_000_000)
            self.state.push_alert(
                "critical" if level == 3 else "warn", "data_risk",
                f"You are carrying roughly {millions} million credits of unsold "
                f"exploration and biology data — about {int(ratio)} times your rebuy. "
                "Consider banking it soon.",
                f"◈ DATA AT RISK · ≈{millions}M CR UNSOLD ({int(ratio)}× REBUY)",
            )
        self._risk_level = level

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

    def _apply_shiplocker(self, data):
        """ShipLocker.json — the Odyssey on-foot inventory (goods, assets,
        data, consumables), consumed by bartenders and on-foot engineers.
        Entries repeat per owner/mission, so aggregate counts by item."""
        groups = {"items": "Items", "components": "Components",
                  "data": "Data", "consumables": "Consumables"}
        locker = {}
        for out_key, src_key in groups.items():
            rows = data.get(src_key)
            if rows is None:
                return  # not an Odyssey locker file; keep the last good state
            counts = {}
            for item in rows:
                raw = item.get("Name") or ""
                symbol = raw.strip("$;").removesuffix("_name").lower()
                name = item.get("Name_Localised") or _clean_name(raw)
                if not name or not symbol:
                    continue
                entry = counts.setdefault(symbol, {"symbol": symbol, "name": name, "count": 0})
                entry["count"] += item.get("Count") or 0
            locker[out_key] = sorted(
                counts.values(),
                key=lambda i: (-i["count"], i["name"]),
            )
        locker["total"] = sum(i["count"] for rows in locker.values() for i in rows)
        self.state.update(ship_locker=locker)

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

            UPLOADER.maybe_publish(
                data, self.state.commander,
                self.state.game_version, self.state.game_build,
                self.state.horizons, self.state.odyssey,
            )
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

    def _apply_outfitting(self, data):
        try:
            from .eddn_upload import UPLOADER

            UPLOADER.maybe_publish_snapshot(
                "outfitting", data, self.state.commander,
                self.state.game_version, self.state.game_build,
                horizons=self.state.horizons, odyssey=self.state.odyssey,
            )
        except Exception as exc:
            log.debug("EDDN outfitting publication skipped: %s", exc, exc_info=True)

    def _apply_shipyard(self, data):
        try:
            from .eddn_upload import UPLOADER

            UPLOADER.maybe_publish_snapshot(
                "shipyard", data, self.state.commander,
                self.state.game_version, self.state.game_build,
                horizons=self.state.horizons, odyssey=self.state.odyssey,
            )
        except Exception as exc:
            log.debug("EDDN shipyard publication skipped: %s", exc, exc_info=True)

    def _apply_fcmaterials(self, data):
        """FCMaterials journal events only signal this authoritative sidecar."""
        try:
            from .eddn_upload import UPLOADER

            UPLOADER.maybe_publish_snapshot(
                "fcmaterials", data, self.state.commander,
                self.state.game_version, self.state.game_build,
                horizons=self.state.horizons, odyssey=self.state.odyssey,
            )
        except Exception as exc:
            log.debug("EDDN carrier materials publication skipped: %s", exc, exc_info=True)

    # ---------- bootstrap & tail ----------

    def _process_lines(self, text, *, source_file=None, start_line=0):
        last_line = int(start_line)
        for line_number, line in enumerate(text.splitlines(), start_line + 1):
            last_line = line_number
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                self.handle_event(event, source_file=source_file, source_line=line_number)
            except Exception as exc:
                log.warning(
                    "journal event handler failed file=%s line=%s type=%s error=%s",
                    source_file or "?", line_number, event.get("event"), type(exc).__name__,
                    exc_info=True,
                )
        return last_line

    def bootstrap(self):
        if not self.journal_dir.is_dir():
            self._public_state.update(journal_dir_found=False)
            return False
        self._public_state.update(journal_dir_found=True)
        files = journal_files(self.journal_dir)

        # Replaying recent files directly into the shared AppState makes API
        # polls briefly expose each old system, balance and cargo snapshot in
        # turn. Build privately, then publish one coherent final cockpit.
        staged_state = type(self._public_state)()
        staged_state.update(
            game_running=self._public_state.game_running,
            journal_dir_found=True,
        )
        previous_state = self.state
        self.state = staged_state
        try:
            if files:
                self._bootstrap_files(files)
            else:
                # An empty but valid journal directory is a coherent fresh
                # state, not a reconstruction failure. Clear any prior-folder
                # watcher context and wait for the game's first journal.
                if self._extension_unsubscribe:
                    self._extension_unsubscribe()
                    self._extension_unsubscribe = None
                self._reset_profile_context()
                self._pending_local_events = []
                self._finish_profile_handoff()
                self._current_file = None
                self._offset = 0
                self._partial = ""
                self._line_number = 0
        finally:
            self.state = previous_state
        if self._track_rebuild:
            # Derived preservation/finalization is part of the same logical
            # reconstruction. Keep the cockpit private until that DB commit
            # succeeds as well.
            self._staged_bootstrap_state = staged_state
        else:
            self._staged_bootstrap_state = staged_state
            self._publish_staged_bootstrap()
        return True

    @staticmethod
    def _read_complete_journal_snapshot(path):
        """Read only newline-complete records and retain the exact tail cookie.

        The game may append while bootstrap handlers are still running. The
        returned text-mode cookie points immediately after the last complete
        line in this snapshot, so the live tail cannot skip those new bytes or
        discard a JSON record that was partial at snapshot time.
        """
        lines = []
        offset = 0
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            while True:
                line_start = handle.tell()
                line = handle.readline()
                if not line:
                    offset = handle.tell()
                    break
                if not line.endswith("\n"):
                    # Closed journals and synthetic imports do not always end
                    # in a newline. A parseable JSON object is complete and its
                    # EOF is a safe tail boundary; malformed JSON must remain
                    # unread so the next append can finish the record.
                    try:
                        json.loads(line.strip())
                    except (json.JSONDecodeError, ValueError):
                        offset = line_start
                    else:
                        lines.append(line)
                        offset = handle.tell()
                    break
                lines.append(line)
                offset = handle.tell()
        return "".join(lines), offset

    def _bootstrap_files(self, files):
        if self._extension_unsubscribe:
            self._extension_unsubscribe()
            self._extension_unsubscribe = None
        self._reset_profile_context()
        self._pending_local_events = []
        self._finish_profile_handoff()

        def profile_in(text):
            name = None
            mode = "unknown"
            for raw in text.splitlines():
                if not any(token in raw for token in ("Commander", "LoadGame", "Fileheader")):
                    continue
                try:
                    item = json.loads(raw.strip().rstrip(","))
                except json.JSONDecodeError:
                    continue
                if item.get("event") == "Fileheader":
                    mode = _galaxy_mode(item.get("gameversion"))
                candidate = item.get("Name") if item.get("event") == "Commander" else item.get("Commander")
                if candidate:
                    name = str(candidate)
                if name and mode != "unknown":
                    break
            return name, mode

        try:
            newest_text = files[-1].read_text(encoding="utf-8", errors="replace")
        except OSError:
            # The newest journal establishes the active profile. Publishing a
            # fallback built without it would expose an older commander's
            # cockpit, so let the outer reconstruction loop retry.
            raise
        current_profile = profile_in(newest_text)

        # Walk backwards through this commander's files until the essentials
        # have been seen, then replay them chronologically. Multi-account users
        # never inherit missions, samples, or balances from the other profile.
        # selected files in chronological order through the normal handlers.
        # The bio vault holds everything since your last Vista Genomics sale
        # (or death), which on a long expedition can be many sessions back —
        # while a file with samples has no newer sale/death, keep walking so
        # unsold samples survive restarts (BOOTSTRAP_MAX_FILES still caps it).
        needed = {"location": False, "loadout": False, "commander": False, "bio_boundary": False}
        selected = []
        # A newly-created journal can contain only Fileheader for a moment.
        # Until its Commander/LoadGame arrives there is no safe identity with
        # which to replay older files, so keep the snapshot empty instead of
        # guessing from another account's previous session.
        candidates = [files[-1]] if not current_profile[0] else files[-BOOTSTRAP_MAX_FILES:]
        for path in reversed(candidates):
            selected.insert(0, path)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                raise
            file_profile = profile_in(text)
            if (current_profile[0] and file_profile[0]
                    and file_profile != current_profile):
                selected.pop(0)
                continue
            if '"event":"Location"' in text or '"event":"FSDJump"' in text:
                needed["location"] = True
            if '"event":"Loadout"' in text:
                needed["loadout"] = True
            if '"event":"Commander"' in text or '"event":"LoadGame"' in text:
                needed["commander"] = True
            if '"event":"SellOrganicData"' in text or '"event":"Died"' in text:
                needed["bio_boundary"] = True  # vault emptied here; older samples are sold
            if all(needed.values()) and len(selected) >= BOOTSTRAP_MIN_FILES:
                break

        selected_total = len(selected)
        newest_offset = 0
        self._set_rebuild_progress("bootstrap", 0, selected_total)
        # One commander.db connection for the whole replay: the per-event
        # reducers borrow it via connect_user() instead of opening (and
        # re-validating) thousands of short-lived connections.
        with marketdb.commander_session():
            for file_index, path in enumerate(selected, 1):
                if self._stop_event.is_set():
                    # Never stage or publish a cockpit assembled from only part
                    # of the selected chronology during application shutdown.
                    raise InterruptedError("journal bootstrap was cancelled")
                self._set_rebuild_progress(
                    "bootstrap", file_index - 1, selected_total, path.name)
                try:
                    if path == files[-1]:
                        text, newest_offset = self._read_complete_journal_snapshot(path)
                    else:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    last_line = self._process_lines(text, source_file=path.name)
                    if path == files[-1]:
                        self._line_number = last_line
                except OSError:
                    raise
                self._set_rebuild_progress("bootstrap", file_index, selected_total)

        # Tail from the end of the newest file.
        self._current_file = files[-1]
        self._offset = newest_offset
        self._partial = ""

        self._refresh_status_files(force=True)
        self._set_rebuild_progress("bootstrap", selected_total, selected_total)

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
            self._line_number = 0
        self._read_new_bytes()

    def _read_new_bytes(self):
        try:
            size = self._current_file.stat().st_size
            if size < self._offset:  # truncated/replaced
                self._offset = 0
                self._partial = ""
                self._line_number = 0
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
            self._line_number = self._process_lines(
                text, source_file=self._current_file.name, start_line=self._line_number)

    # Bump when the set of events swept below changes, to force a one-time
    # re-import of already-processed journals (all logging is INSERT OR IGNORE).
    HISTORY_VERSION = "5"
    DERIVED_HISTORY_VERSION = "2"
    _DERIVED_REPLAY_PRESERVING = DERIVED_HISTORY_VERSION + ":preserving"
    _DERIVED_REPLAY_HISTORY_READY = DERIVED_HISTORY_VERSION + ":history-ready"

    # etype -> handler, for both the history sweep and the marker prefilter.
    _HISTORY_EVENTS = (
        "MarketBuy", "MarketSell", "LoadGame", "MissionCompleted",
        "SellExplorationData", "MultiSellExplorationData", "SellOrganicData",
        "RedeemVoucher",
    )

    def _import_event(self, event, commander_id):
        """Import analytics without mutating the currently displayed state."""
        etype = event.get("event")
        if etype == "MarketBuy":
            marketdb.log_trade(
                marketdb.parse_update_time(event.get("timestamp")), "buy",
                (event.get("Type") or "").lower(),
                event.get("Type_Localised") or (event.get("Type") or "").title(),
                event.get("Count"), event.get("BuyPrice"), event.get("TotalCost"),
                commander_id=commander_id,
            )
        elif etype == "MarketSell":
            profit = None
            if event.get("SellPrice") is not None and event.get("AvgPricePaid") is not None:
                profit = (event["SellPrice"] - event["AvgPricePaid"]) * (event.get("Count") or 0)
            marketdb.log_trade(
                marketdb.parse_update_time(event.get("timestamp")), "sell",
                (event.get("Type") or "").lower(),
                event.get("Type_Localised") or (event.get("Type") or "").title(),
                event.get("Count"), event.get("SellPrice"), event.get("TotalSale"), profit,
                commander_id=commander_id,
            )
        elif etype == "LoadGame" and event.get("Credits") is not None:
            marketdb.log_balance(
                marketdb.parse_update_time(event.get("timestamp")), event["Credits"],
                commander_id=commander_id,
            )
        elif etype == "MissionCompleted":
            marketdb.log_income(
                marketdb.parse_update_time(event.get("timestamp")), "mission",
                event.get("Reward") or 0, event.get("Name"), commander_id=commander_id,
            )
        elif etype in ("SellExplorationData", "MultiSellExplorationData"):
            marketdb.log_income(
                marketdb.parse_update_time(event.get("timestamp")), "exploration",
                event.get("TotalEarnings") or event.get("BaseValue"),
                commander_id=commander_id,
            )
        elif etype == "SellOrganicData":
            total = sum((b.get("Value") or 0) + (b.get("Bonus") or 0)
                        for b in event.get("BioData") or [])
            marketdb.log_income(
                marketdb.parse_update_time(event.get("timestamp")), "exobiology", total,
                commander_id=commander_id,
            )
        elif etype == "RedeemVoucher":
            voucher = (event.get("Type") or "").lower()
            category = "bounty" if voucher in ("bounty", "combatbond", "settlement") else "other"
            marketdb.log_income(
                marketdb.parse_update_time(event.get("timestamp")), category,
                event.get("Amount"), voucher or None, commander_id=commander_id,
            )

    def import_trade_history(self):
        """Sweep completed journals into commander-scoped analytics and ledger.

        Each file is assigned from its own Commander/LoadGame identity. This is
        essential for players with multiple accounts sharing one journal folder.
        """
        if not self.journal_dir.is_dir():
            # If a repair was already staged, a temporarily unavailable folder
            # cannot be treated as a successful empty sweep: doing so would
            # expose cleared state and discard the bootstrap ordering barrier.
            conn = marketdb.connect_user()
            try:
                marker = conn.execute(
                    "SELECT value FROM user_meta WHERE key='journal_derived_version'"
                ).fetchone()
            finally:
                conn.close()
            if marker and marker[0] in {
                self._DERIVED_REPLAY_PRESERVING,
                self._DERIVED_REPLAY_HISTORY_READY,
            }:
                return False
            return True
        replay_phase = self._prepare_derived_history_replay()
        conn = marketdb.connect()
        try:
            if marketdb.get_meta(conn, "history_version") != self.HISTORY_VERSION:
                conn.execute("DELETE FROM imported_journals")
                marketdb.set_meta(conn, "history_version", self.HISTORY_VERSION)
                conn.commit()
            done = {
                (row[0], row[1])
                for row in conn.execute("SELECT commander_id, filename FROM imported_journals")
            }
        finally:
            conn.close()
        files = journal_files(self.journal_dir)
        completed_files = files[:-1]
        total_files = len(completed_files)
        self._set_rebuild_progress("history", 0, total_files)
        sweep_complete = True
        for file_index, path in enumerate(completed_files, 1):
            # The newest file is still being written; the live tail covers it.
            if self._stop_event.is_set():
                sweep_complete = False
                break
            self._set_rebuild_progress(
                "history", file_index - 1, total_files, path.name)
            try:
                parsed = []
                commander_name = None
                galaxy_mode = "unknown"
                for line_number, line in enumerate(
                        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    try:
                        event = json.loads(line.strip().rstrip(","))
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict) or not event.get("event"):
                        continue
                    parsed.append((line_number, event))
                    if event["event"] == "Fileheader":
                        galaxy_mode = _galaxy_mode(event.get("gameversion"))
                    if event["event"] == "Commander" and event.get("Name"):
                        commander_name = event["Name"]
                    elif event["event"] == "LoadGame" and event.get("Commander"):
                        commander_name = event["Commander"]
            except OSError:
                # An unreadable old file is a hard ordering barrier for every
                # state-machine replay, not just the one-time repair. Retrying
                # is safe because reducers and per-file markers are durable.
                sweep_complete = False
                break
            if not parsed:
                self._set_rebuild_progress("history", file_index, total_files)
                continue
            # A damaged/header-only file has no trustworthy owner.  Never infer
            # it from whichever unrelated account happens to be on screen.
            known_commander = commander_name
            commander_id = (
                marketdb.ensure_commander_profile(
                    known_commander, make_active=False,
                    galaxy_mode=galaxy_mode if galaxy_mode in {"live", "legacy"} else "live",
                )
                if known_commander else "default"
            )

            # Launcher stubs (the game was opened but no pilot logged in)
            # contain only session chrome, so there is nothing worth keeping
            # and no owner to keep it for. Skip ledger ingestion; the
            # imported_journals marker below still records the file as
            # processed so it is never rescanned.
            stub_only = known_commander is None and all(
                event.get("event") in _SESSION_CHROME_EVENTS for _, event in parsed
            )

            # Full compressed history powers lifetime queries and future local
            # reducers. Source coordinates preserve legitimate identical events.
            if not stub_only:
                try:
                    from .eventledger import EventLedger

                    stat = path.stat()
                    ledger = EventLedger(commander_id)
                    claim = ledger.prepare_journal(
                        path.name, size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
                    if claim["needs_import"]:
                        resume = claim["resume_after_line"]
                        remaining = [(line, event) for line, event in parsed if line > resume]
                        report = ledger.import_journal(
                            path.name, remaining, size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
                except Exception as exc:
                    log.warning("journal ledger backfill failed file=%s error=%s",
                                path.name, type(exc).__name__, exc_info=True)

            if (commander_id, path.name) in done:
                self._set_rebuild_progress("history", file_index, total_files)
                continue
            derived_ok = True
            try:
                from .timings import TimingModel

                # Timing has its own idempotent observation keys, so replay the
                # whole file until the shared derived-reducer checkpoint lands.
                # This remains retryable even after the ledger itself is marked
                # complete on an earlier attempt.
                timing = TimingModel(commander_id)
                for _line, event in parsed:
                    timing.observe_event(event)
            except Exception as exc:
                derived_ok = False
                log.warning("timing history backfill failed file=%s error=%s",
                            path.name, type(exc).__name__, exc_info=True)
            try:
                from .specialists import EXPECTED_JOURNAL_EVENTS, SpecialistWorkflows

                workflows = SpecialistWorkflows(commander_id)
                expected = set().union(*EXPECTED_JOURNAL_EVENTS.values())
                for line_number, event in parsed:
                    if event.get("event") not in expected:
                        continue
                    uid = hashlib.sha256(f"{path.name}:{line_number}".encode("utf-8")).hexdigest()
                    workflows.observe_event(event, uid, context={"at_own_carrier": False})
            except Exception as exc:
                derived_ok = False
                log.warning("specialist history backfill failed file=%s error=%s",
                            path.name, type(exc).__name__, exc_info=True)
            for _line, event in parsed:
                if event.get("event") in self._HISTORY_EVENTS:
                    try:
                        self._import_event(event, commander_id)
                    except Exception as exc:
                        derived_ok = False
                        log.warning(
                            "analytics history backfill failed file=%s type=%s error=%s",
                            path.name, event.get("event"), type(exc).__name__, exc_info=True,
                        )
            if not derived_ok:
                # Every reducer is idempotent.  Withhold the shared completion
                # marker so a transient disk lock or a newly-fixed reducer is
                # retried on the next sweep rather than creating a permanent
                # hole in the commander's history.
                # Derived reducers are state machines, so a failed older file
                # is a chronological barrier. Processing newer files and then
                # retrying this one would corrupt the present state again.
                sweep_complete = False
                break
            conn = marketdb.connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO imported_journals(commander_id, filename) VALUES(?, ?)",
                    (commander_id, path.name),
                )
                conn.commit()
            finally:
                conn.close()
            self._set_rebuild_progress("history", file_index, total_files)

        if not sweep_complete:
            return False
        self._set_rebuild_progress("history", total_files, total_files)
        if replay_phase:
            conn = marketdb.connect_user()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO user_meta(key,value) VALUES(?,?)",
                    ("journal_derived_version", self._DERIVED_REPLAY_HISTORY_READY),
                )
                conn.commit()
            finally:
                conn.close()
        return True

    @staticmethod
    def _preservable_specialist_fragment(conn, commander_id, workflow, raw_state):
        """Extract only inputs the journal cannot authoritatively reconstruct."""
        try:
            state = json.loads(raw_state)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(state, dict):
            return None
        fragment = {"fallback_state": state}

        if workflow == "carrier_ops":
            plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
            upkeep_source = str(plan.get("upkeep_source") or "").casefold()
            explicit_plan = bool(
                plan.get("weekly_upkeep_cr") is not None
                or plan.get("route")
                or plan.get("tritium_per_jump_t") is not None
                or plan.get("tritium_reserve_t") not in (None, 0, 0.0)
                or plan.get("reserve_target_weeks") not in (None, 8)
                or (upkeep_source and "not configured" not in upkeep_source)
            )
            if explicit_plan:
                keys = (
                    "weekly_upkeep_cr", "upkeep_source", "reserve_target_weeks",
                    "tritium_per_jump_t", "tritium_reserve_t", "route",
                )
                fragment["plan"] = {key: plan.get(key) for key in keys}
            inventory_source = str(state.get("inventory_source") or "").casefold()
            if inventory_source and "journal" not in inventory_source:
                fragment["inventory"] = state.get("inventory")
                fragment["inventory_source"] = state.get("inventory_source")
            return fragment

        if workflow == "exobiology_map":
            preserved_surveys = []
            surveys = state.get("surveys") if isinstance(state.get("surveys"), dict) else {}
            for key, survey in surveys.items():
                if not isinstance(survey, dict):
                    continue
                preserved_surveys.append({**survey, "key": survey.get("key") or key})
            fragment["surveys"] = preserved_surveys
            return fragment

        if workflow in {"mining", "combat_ops"}:
            session = state.get("session")
            if not isinstance(session, dict) or not session.get("active"):
                return fragment
            started_ts = session.get("started_ts")
            # Automatic sessions start on a journal event and therefore have
            # an event marker at exactly the same timestamp. API/button starts
            # deliberately do not create specialist_events rows.
            journal_start = conn.execute(
                "SELECT 1 FROM specialist_events"
                " WHERE commander_id=? AND workflow=? AND event_ts=? LIMIT 1",
                (commander_id, workflow, started_ts),
            ).fetchone()
            if not journal_start:
                fragment["active_session"] = session
            return fragment
        return fragment

    @staticmethod
    def _specialist_default_state(workflow):
        if workflow == "carrier_ops":
            from .carrierops import _default_state
        elif workflow == "exobiology_map":
            from .exobiology import _default_state
        elif workflow == "mining":
            from .mining import _default_state
        elif workflow == "combat_ops":
            from .combatops import _default_state
        else:
            return {}
        return _default_state()

    @staticmethod
    def _merge_preserved_specialist_state(workflow, rebuilt, fragment):
        state = rebuilt if isinstance(rebuilt, dict) else {}
        if workflow == "carrier_ops":
            if isinstance(fragment.get("plan"), dict):
                state.setdefault("plan", {}).update(fragment["plan"])
            if "inventory" in fragment:
                state["inventory"] = fragment["inventory"]
                state["inventory_source"] = fragment.get("inventory_source")
        elif workflow == "exobiology_map":
            surveys = state.setdefault("surveys", {})
            if not isinstance(surveys, dict):
                surveys = state["surveys"] = {}
            for saved in fragment.get("surveys") or []:
                key = saved.get("key")
                if not key:
                    continue
                survey = surveys.get(key)
                if not isinstance(survey, dict):
                    # No surviving journal recreated this body, so the old
                    # complete survey is the only local record of it.
                    survey = dict(saved)
                    surveys[key] = survey
                else:
                    # Matching rebuilt surveys stay authoritative. Fill only
                    # facts that the surviving journal subset did not supply.
                    for field in (
                        "system", "system_address", "body", "body_id", "radius_m",
                        "signal_count", "genuses", "updated_ts",
                    ):
                        if survey.get(field) in (None, [], {}) and saved.get(field) not in (
                            None, [], {},
                        ):
                            survey[field] = saved[field]
                    completed = survey.setdefault("completed", {})
                    if not isinstance(completed, dict):
                        completed = survey["completed"] = {}
                    for organism, details in (saved.get("completed") or {}).items():
                        completed.setdefault(organism, details)
                pins = survey.setdefault("pins", [])
                if not isinstance(pins, list):
                    pins = survey["pins"] = []
                def signature(pin):
                    if not isinstance(pin, dict):
                        return None
                    return (
                        pin.get("source"), pin.get("timestamp"), pin.get("lat"),
                        pin.get("lon"), pin.get("label"),
                    )

                known = {signature(pin) for pin in pins if signature(pin) is not None}
                for pin in saved.get("pins") or []:
                    marker = signature(pin)
                    if marker is not None and marker in known:
                        continue
                    pins.append(pin)
                    if marker is not None:
                        known.add(marker)
                if state.get("system") is None:
                    state["system"] = saved.get("system")
                if state.get("system_address") is None:
                    state["system_address"] = saved.get("system_address")
        elif workflow in {"mining", "combat_ops"}:
            manual = fragment.get("active_session")
            if isinstance(manual, dict):
                rebuilt_session = state.get("session")

                def latest(item):
                    if not isinstance(item, dict):
                        return -1
                    values = (
                        item.get("ended_ts"), item.get("last_event_ts"),
                        item.get("started_ts"),
                    )
                    numeric = []
                    for value in values:
                        try:
                            numeric.append(int(value))
                        except (TypeError, ValueError):
                            pass
                    return max(numeric, default=-1)

                # The rebuilt projection wins at equal/newer timestamps. A
                # later manual start is a real current session after the old
                # journal timeline and must remain active.
                if latest(manual) > latest(rebuilt_session):
                    state["session"] = manual
        return state

    def _prepare_derived_history_replay(self):
        """Stage user-authored data, then clear the flawed v4 projections.

        The staging tables and phase marker share the clearing transaction.
        A crash therefore either leaves the original data untouched or leaves
        a durable preservation set that a later launch can restore after the
        chronological sweep and live bootstrap have both completed.
        """
        conn = marketdb.connect_user()
        try:
            current = conn.execute(
                "SELECT value FROM user_meta WHERE key='journal_derived_version'"
            ).fetchone()
            phase = current[0] if current else None
            if phase == self.DERIVED_HISTORY_VERSION:
                return None
            if phase in {
                self._DERIVED_REPLAY_PRESERVING,
                self._DERIVED_REPLAY_HISTORY_READY,
            }:
                return phase
            previous_history = conn.execute(
                "SELECT value FROM user_meta WHERE key='history_version'"
            ).fetchone()
            if not previous_history or previous_history[0] != "4":
                # v2.0 did not have these projections, while a fresh v2.1 user
                # may have intentionally created a pre-game specialist session.
                # Only history v4 identifies a database exposed to the flawed
                # recent-first replay; never clear data merely for lacking a
                # marker introduced by this release.
                conn.execute(
                    "INSERT OR REPLACE INTO user_meta(key,value) VALUES(?,?)",
                    ("journal_derived_version", self.DERIVED_HISTORY_VERSION),
                )
                conn.commit()
                return None
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            populated = False
            for table in (
                "specialist_state", "specialist_events", "specialist_history",
                "timing_pending",
            ):
                if table in tables and conn.execute(
                    f"SELECT 1 FROM {marketdb.commanderdb.quote_identifier(table)} LIMIT 1"
                ).fetchone():
                    populated = True
                    break
            if not populated and "timing_observations" in tables:
                populated = bool(conn.execute(
                    "SELECT 1 FROM timing_observations WHERE source='journal' LIMIT 1"
                ).fetchone())
        finally:
            conn.close()

        if populated:
            marketdb.backup_commander_data(reason="chronological-derived-replay")

        conn = marketdb.connect_user()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS journal_replay_preserved_state("
                " commander_id TEXT NOT NULL, workflow TEXT NOT NULL, fragment_json TEXT NOT NULL,"
                " PRIMARY KEY(commander_id,workflow)) WITHOUT ROWID"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS journal_replay_preserved_history("
                " commander_id TEXT NOT NULL, workflow TEXT NOT NULL, session_key TEXT NOT NULL,"
                " started_ts INTEGER, ended_ts INTEGER, summary_json TEXT NOT NULL,"
                " created_at TEXT NOT NULL, PRIMARY KEY(commander_id,workflow,session_key))"
                " WITHOUT ROWID"
            )
            conn.execute("DELETE FROM journal_replay_preserved_state")
            conn.execute("DELETE FROM journal_replay_preserved_history")
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "specialist_state" in tables:
                rows = conn.execute(
                    "SELECT commander_id,workflow,state_json FROM specialist_state"
                ).fetchall()
                for commander_id, workflow, raw_state in rows:
                    fragment = self._preservable_specialist_fragment(
                        conn, commander_id, workflow, raw_state)
                    if fragment:
                        conn.execute(
                            "INSERT INTO journal_replay_preserved_state"
                            "(commander_id,workflow,fragment_json) VALUES(?,?,?)",
                            (
                                commander_id, workflow,
                                json.dumps(fragment, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")),
                            ),
                        )
            if "specialist_history" in tables:
                conn.execute(
                    "INSERT INTO journal_replay_preserved_history("
                    "commander_id,workflow,session_key,started_ts,ended_ts,summary_json,created_at)"
                    " SELECT commander_id,workflow,session_key,started_ts,ended_ts,summary_json,created_at"
                    " FROM specialist_history"
                )
            for table in ("specialist_state", "specialist_events", "specialist_history"):
                if table in tables:
                    conn.execute(
                        f"DELETE FROM {marketdb.commanderdb.quote_identifier(table)}"
                    )
            if "timing_pending" in tables:
                conn.execute("DELETE FROM timing_pending")
            # Completed timing observations are immutable facts with a unique
            # transition key. Retaining them is safe under replay de-duplication
            # and preserves samples whose source journal has since been pruned.
            conn.execute(
                "INSERT OR REPLACE INTO user_meta(key,value) VALUES(?,?)",
                ("journal_derived_version", self._DERIVED_REPLAY_PRESERVING),
            )
            conn.commit()
            return self._DERIVED_REPLAY_PRESERVING
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _derived_history_replay_pending(self):
        conn = marketdb.connect_user()
        try:
            marker = conn.execute(
                "SELECT value FROM user_meta WHERE key='journal_derived_version'"
            ).fetchone()
            return bool(marker and marker[0] in {
                self._DERIVED_REPLAY_PRESERVING,
                self._DERIVED_REPLAY_HISTORY_READY,
            })
        finally:
            conn.close()

    def _finalize_derived_history_replay(self):
        """Merge staged non-journal data after historical and live replay."""
        conn = marketdb.connect_user()
        try:
            phase = conn.execute(
                "SELECT value FROM user_meta WHERE key='journal_derived_version'"
            ).fetchone()
            if not phase or phase[0] == self.DERIVED_HISTORY_VERSION:
                return True
            if phase[0] != self._DERIVED_REPLAY_HISTORY_READY:
                return False
            if not self.journal_dir.is_dir():
                return False
            conn.execute("BEGIN IMMEDIATE")
            adopted = conn.execute(
                "SELECT value FROM user_meta WHERE key='default_profile_adopted_by'"
            ).fetchone()
            adopted_id = adopted[0] if adopted and adopted[0] != "default" else None

            def restored_commander(source_id):
                return adopted_id if source_id == "default" and adopted_id else source_id

            rows = conn.execute(
                "SELECT commander_id,workflow,fragment_json"
                " FROM journal_replay_preserved_state"
                " ORDER BY CASE WHEN commander_id='default' THEN 0 ELSE 1 END"
            ).fetchall()
            for source_commander_id, workflow, raw_fragment in rows:
                commander_id = restored_commander(source_commander_id)
                try:
                    fragment = json.loads(raw_fragment)
                except (TypeError, json.JSONDecodeError):
                    continue
                current = conn.execute(
                    "SELECT state_json FROM specialist_state"
                    " WHERE commander_id=? AND workflow=?",
                    (commander_id, workflow),
                ).fetchone()
                try:
                    rebuilt = json.loads(current[0]) if current else None
                except (TypeError, json.JSONDecodeError):
                    rebuilt = None
                if not isinstance(rebuilt, dict):
                    fallback = fragment.get("fallback_state")
                    rebuilt = (
                        fallback if not current and isinstance(fallback, dict)
                        else self._specialist_default_state(workflow)
                    )
                merged = self._merge_preserved_specialist_state(
                    workflow, rebuilt, fragment)
                conn.execute(
                    "INSERT INTO specialist_state(commander_id,workflow,state_json,updated_at)"
                    " VALUES(?,?,?,?) ON CONFLICT(commander_id,workflow) DO UPDATE SET"
                    " state_json=excluded.state_json,updated_at=excluded.updated_at",
                    (
                        commander_id, workflow,
                        json.dumps(merged, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")),
                        marketdb.utc_now_iso(),
                    ),
                )

            history = conn.execute(
                "SELECT commander_id,workflow,session_key,started_ts,ended_ts,"
                "summary_json,created_at FROM journal_replay_preserved_history"
                " ORDER BY CASE WHEN commander_id='default' THEN 1 ELSE 0 END"
            ).fetchall()
            for source_commander_id, workflow, key, started, ended, raw_summary, created in history:
                commander_id = restored_commander(source_commander_id)
                duplicate = conn.execute(
                    "SELECT 1 FROM specialist_history WHERE commander_id=? AND workflow=?"
                    " AND started_ts IS ? AND ended_ts IS ? LIMIT 1",
                    (commander_id, workflow, started, ended),
                ).fetchone()
                if duplicate:
                    continue
                collision = conn.execute(
                    "SELECT 1 FROM specialist_history"
                    " WHERE commander_id=? AND workflow=? AND session_key=?",
                    (commander_id, workflow, key),
                ).fetchone()
                if collision:
                    # Session keys are logical identities. If replay rebuilt
                    # that session, its chronological version is authoritative
                    # even when the flawed v4 projection recorded bad times.
                    continue
                conn.execute(
                    "INSERT INTO specialist_history("
                    "commander_id,workflow,session_key,started_ts,ended_ts,summary_json,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (commander_id, workflow, key, started, ended, raw_summary, created),
                )
            conn.execute("DELETE FROM journal_replay_preserved_state")
            conn.execute("DELETE FROM journal_replay_preserved_history")
            conn.execute(
                "INSERT OR REPLACE INTO user_meta(key,value) VALUES(?,?)",
                ("journal_derived_version", self.DERIVED_HISTORY_VERSION),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_journal_dir(self):
        """Recover from a missing or changed journal folder without a restart:
        re-resolve (the in-app setting may have changed, or the game's first
        launch may have just created the folder) and re-bootstrap on a switch."""
        # Explicit constructor paths never switch elsewhere, but still share
        # the same disappearance/reappearance recovery state machine.
        desired = self.journal_dir if self._fixed_dir else find_journal_dir()
        changed = desired != self.journal_dir
        appeared = not self.state.journal_dir_found and self.journal_dir.is_dir()
        if not desired.is_dir():
            self._public_state.update(journal_dir_found=False)
            self.journal_dir = desired
            self._live = False
            self._reconstruction_pending = True
            return
        if not changed and not appeared and not self._reconstruction_pending:
            return
        if changed:
            self.journal_dir = desired
        # Directory presence is safe to publish before commander data. This
        # lets the rebuild banner replace the stale folder warning while the
        # potentially lengthy chronological import is running.
        self._public_state.update(journal_dir_found=True)
        self._live = False
        self._reconstruction_pending = True
        if not self._track_rebuild:
            self._begin_rebuild()
        self._status_mtimes = {}
        reconstruction_ready = False
        # One commander.db connection for the entire reconstruction: the
        # sweep, the bootstrap replay and finalization all borrow it through
        # connect_user() instead of opening one per reduced event.
        replay_session = marketdb.commander_session()
        replay_session.__enter__()
        try:
            history_ready = False
            try:
                history_ready = self.import_trade_history()
            except Exception as exc:
                self._log_background_failure(
                    "journal history import after directory change", exc
                )
            if not history_ready:
                self._mark_rebuild_retry()
                raise RuntimeError("chronological journal replay is not complete")
            if history_ready and not self._stop_event.is_set():
                try:
                    self.bootstrap()
                    self._set_rebuild_progress("finalizing")
                    if not self._finalize_derived_history_replay():
                        raise RuntimeError("journal replay is awaiting its historical sweep")
                    if not self._publish_staged_bootstrap():
                        raise RuntimeError("journal replay has no coherent cockpit snapshot")
                    reconstruction_ready = True
                except Exception as exc:
                    self._mark_rebuild_retry()
                    self._log_background_failure(
                        "journal bootstrap after directory change", exc
                    )
                    raise
        finally:
            replay_session.__exit__(None, None, None)
            # Do not tail/publish a new folder on top of a partial repair. The
            # outer poll loop retries _ensure_journal_dir on its next pass.
            self._live = reconstruction_ready
            self._reconstruction_pending = not reconstruction_ready
        if reconstruction_ready:
            self._finish_rebuild()
        self._fetch_community_bio(self.state.system_address, self.state.system)

    def _probe_game(self):
        """Reconcile game_running with the process table. Journal events flip
        it True instantly; this probe is what notices the game going away."""
        alive = launcher.is_running()
        if alive is not None and alive != self.state.game_running:
            self.state.update(game_running=alive)
            if not alive:
                # Shutdown normally flushes the final FSSSignalDiscovered
                # batch. A crash has no such journal event, so preserve that
                # last singleton/batch before freezing the session.
                try:
                    from .eddn_upload import UPLOADER

                    UPLOADER.flush_fss_signals(
                        dict(self._eddn_location), self.state.commander)
                except Exception as exc:
                    self._log_background_failure("final EDDN signal flush", exc)
                # Crash or exit without a Shutdown event: freeze the session
                # at the last thing the game wrote, not at detection time.
                self.state.end_session(
                    marketdb.parse_update_time(self.state.last_journal_event)
                    or marketdb.now_epoch()
                )

    def run_forever(self):
        # Establish present-day process truth before any potentially lengthy
        # journal/database bootstrap. Historical events must never be the UI's
        # only game-presence signal while that work is in progress.
        try:
            self._probe_game()
        except Exception as exc:
            self._log_background_failure("initial game process probe", exc)
        self._begin_rebuild()
        # Replay every completed file before rebuilding the live snapshot.
        # Otherwise a session start older than BOOTSTRAP_MAX_FILES can arrive
        # after its recent closing event and leave the durable reducer active.
        while not self._stop_event.is_set():
            try:
                with marketdb.commander_session():
                    if self.import_trade_history():
                        break
            except Exception as exc:
                self._log_background_failure("journal history import", exc)
            if not self._stop_event.is_set():
                self._mark_rebuild_retry()
            # A transient reducer/database error is an ordering barrier. Retry
            # it before bootstrap instead of mixing recent events into a
            # partially reconstructed older state.
            if self._stop_event.wait(POLL_SECONDS):
                return
        if self._stop_event.is_set():
            return
        bootstrap_ready = False
        waiting_for_journal = False
        while not self._stop_event.is_set():
            try:
                if not self.bootstrap() and not self.journal_dir.is_dir():
                    # Missing is a normal recoverable state: keep the watcher
                    # alive so a Settings change or newly-created directory is
                    # observed without restarting Frameshift.
                    waiting_for_journal = True
                    break
                self._set_rebuild_progress("finalizing")
                if not self._finalize_derived_history_replay():
                    raise RuntimeError("journal replay is awaiting its historical sweep")
                if not self._publish_staged_bootstrap():
                    raise RuntimeError("journal replay has no coherent cockpit snapshot")
                bootstrap_ready = True
                break
            except Exception as exc:
                self._mark_rebuild_retry()
                self._log_background_failure("journal bootstrap", exc)
                retryable = isinstance(exc, (OSError, sqlite3.Error))
                if not retryable and not self._derived_history_replay_pending():
                    break
            # Preservation stays staged until a complete bootstrap and final
            # merge commit. Retrying is safe because journal event IDs dedupe.
            if self._stop_event.wait(POLL_SECONDS):
                return
        if self._stop_event.is_set():
            return
        if bootstrap_ready:
            self._finish_rebuild()
        elif waiting_for_journal:
            self._staged_bootstrap_state = None
            self._finish_rebuild()
        else:
            self._staged_bootstrap_state = None
            self._public_state.set_journal_rebuild(
                active=False, phase="error", current=None, retrying=False,
            )
            self._track_rebuild = False
            # A failed bootstrap has no safe tail boundary. Do not let normal
            # polling replay historical files directly into public AppState.
            return
        # Probe again after reconstruction in case the game started while the
        # local history was being rebuilt.
        try:
            self._probe_game()
        except Exception as exc:
            self._log_background_failure("game process probe", exc)
        if self._stop_event.is_set():
            return
        self._live = True
        # Bootstrap set the current system without a live event, so fetch its
        # community bio data now.
        self._fetch_community_bio(self.state.system_address, self.state.system)
        last_probe = 0.0
        while not self._stop_event.is_set():
            try:
                self._ensure_journal_dir()
                # A folder can appear immediately after the recovery check.
                # Keep public tailing gated until the next pass reconstructs
                # that folder privately and publishes it atomically.
                if self._live and self._public_state.journal_dir_found:
                    self._poll_journal()
                    self._refresh_status_files()
                if time.monotonic() - last_probe >= GAME_PROBE_SECONDS:
                    last_probe = time.monotonic()
                    self._probe_game()
            except Exception as exc:
                self._log_background_failure("journal watcher poll", exc)
            self._stop_event.wait(POLL_SECONDS)

    def start(self):
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return self._thread
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self.run_forever, name="journal-watcher", daemon=True,
            )
            self._thread.start()
            return self._thread

    def stop(self, timeout=5):
        """Request shutdown, join the worker, and detach extension callbacks."""
        self._stop_event.set()
        with self._thread_lock:
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(max(0, float(timeout)))
        stopped = not thread or not thread.is_alive()
        if stopped and self._extension_unsubscribe:
            self._extension_unsubscribe()
            self._extension_unsubscribe = None
        return stopped
