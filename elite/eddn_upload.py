"""Publish account-free, privacy-safe observations to EDDN.

Every message is built for a concrete schema rather than forwarding raw game
JSON.  That keeps commander-specific fields out, prevents future journal fields
from invalidating strict schemas, and makes location augmentation fail closed.

Commodity contribution uses ET_EDDN_UPLOAD (default on). Broader journal and
snapshot contribution requires ET_EDDN_EXTENDED_UPLOAD=1 or the Settings opt-in.
"""

import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from copy import deepcopy

import requests

from . import marketdb

try:
    from ._version import VERSION as SOFTWARE_VERSION
except Exception:
    SOFTWARE_VERSION = "0.0.0"


UPLOAD_URL = "https://eddn.edcd.io:4430/upload/"
SCHEMA = "https://eddn.edcd.io/schemas/commodity/3"  # backwards-compatible name
SCHEMAS = {
    "commodity": SCHEMA,
    "journal": "https://eddn.edcd.io/schemas/journal/1",
    "fssallbodiesfound": "https://eddn.edcd.io/schemas/fssallbodiesfound/1",
    "fssbodysignals": "https://eddn.edcd.io/schemas/fssbodysignals/1",
    "fssdiscoveryscan": "https://eddn.edcd.io/schemas/fssdiscoveryscan/1",
    "fsssignaldiscovered": "https://eddn.edcd.io/schemas/fsssignaldiscovered/1",
    "navbeaconscan": "https://eddn.edcd.io/schemas/navbeaconscan/1",
    "scanbarycentre": "https://eddn.edcd.io/schemas/scanbarycentre/1",
    "codexentry": "https://eddn.edcd.io/schemas/codexentry/1",
    "approachsettlement": "https://eddn.edcd.io/schemas/approachsettlement/1",
    "dockinggranted": "https://eddn.edcd.io/schemas/dockinggranted/1",
    "dockingdenied": "https://eddn.edcd.io/schemas/dockingdenied/1",
    "fcmaterials": "https://eddn.edcd.io/schemas/fcmaterials_journal/1",
    "navroute": "https://eddn.edcd.io/schemas/navroute/1",
    "outfitting": "https://eddn.edcd.io/schemas/outfitting/2",
    "shipyard": "https://eddn.edcd.io/schemas/shipyard/2",
}
SOFTWARE_NAME = "Frameshift"
MAX_AGE_S = 120  # never upload stale snapshots (e.g. bootstrap replays)
IDENTITY_PATH = marketdb.DATA_DIR / "eddn_identity.bin"
_identity_lock = threading.Lock()
_identity_secret = None


def _pseudonymous_uploader(commander):
    """Return a stable per-install identity without publishing a commander name."""
    global _identity_secret
    with _identity_lock:
        if _identity_secret is None:
            try:
                value = IDENTITY_PATH.read_bytes()
                if len(value) != 32:
                    raise ValueError("invalid identity length")
            except (OSError, ValueError):
                value = secrets.token_bytes(32)
                temp = IDENTITY_PATH.with_suffix(".tmp")
                try:
                    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
                    with open(temp, "wb") as stream:
                        stream.write(value)
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        os.chmod(temp, 0o600)
                    except OSError:
                        pass
                    os.replace(temp, IDENTITY_PATH)
                except OSError:
                    try:
                        temp.unlink()
                    except OSError:
                        pass
                    # Read-only portable runs remain session-pseudonymous.
            _identity_secret = value
        normalized = " ".join(str(commander or "unknown").casefold().split())
        digest = hmac.new(
            _identity_secret, normalized.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return "frameshift-" + digest[:24]


SKIP_CATEGORIES = {"nonmarketable"}

JOURNAL_SCHEMAS = {
    "Location": "journal",
    "FSDJump": "journal",
    "CarrierJump": "journal",
    "Docked": "journal",
    "Scan": "journal",
    "SAASignalsFound": "journal",
    "FSSAllBodiesFound": "fssallbodiesfound",
    "FSSBodySignals": "fssbodysignals",
    "FSSDiscoveryScan": "fssdiscoveryscan",
    "NavBeaconScan": "navbeaconscan",
    "ScanBaryCentre": "scanbarycentre",
    "CodexEntry": "codexentry",
    "ApproachSettlement": "approachsettlement",
    "DockingGranted": "dockinggranted",
    "DockingDenied": "dockingdenied",
}

_FACTION_PRIVATE = {"HappiestSystem", "HomeSystem", "MyReputation", "SquadronFaction"}
_CODEX_PRIVATE = {"IsNewEntry", "NewTraitsDiscovered"}

_COMMON_FIELDS = {"timestamp", "event", "horizons", "odyssey"}
_STRICT_FIELDS = {
    "fssallbodiesfound": _COMMON_FIELDS | {
        "SystemName", "StarPos", "SystemAddress", "Count",
    },
    "fssbodysignals": _COMMON_FIELDS | {
        "StarSystem", "StarPos", "SystemAddress", "BodyID", "BodyName", "Signals",
    },
    "fssdiscoveryscan": _COMMON_FIELDS | {
        "SystemName", "StarPos", "SystemAddress", "BodyCount", "NonBodyCount",
    },
    "navbeaconscan": _COMMON_FIELDS | {
        "StarSystem", "StarPos", "SystemAddress", "NumBodies",
    },
    "scanbarycentre": _COMMON_FIELDS | {
        "StarSystem", "StarPos", "SystemAddress", "BodyID", "SemiMajorAxis",
        "Eccentricity", "OrbitalInclination", "Periapsis", "OrbitalPeriod",
        "AscendingNode", "MeanAnomaly",
    },
    "codexentry": _COMMON_FIELDS | {
        "System", "StarPos", "SystemAddress", "Name", "Region", "EntryID",
        "Category", "Latitude", "Longitude", "SubCategory", "NearestDestination",
        "VoucherAmount", "Traits", "BodyID", "BodyName",
    },
    "approachsettlement": _COMMON_FIELDS | {
        "StarSystem", "StarPos", "SystemAddress", "StationGovernment",
        "StationAllegiance", "StationEconomies", "StationFaction", "StationServices",
        "StationEconomy", "Name", "MarketID", "BodyID", "BodyName", "Latitude",
        "Longitude",
    },
    "dockinggranted": _COMMON_FIELDS | {
        "MarketID", "StationName", "StationType", "LandingPad",
    },
    "dockingdenied": _COMMON_FIELDS | {
        "MarketID", "StationName", "StationType", "Reason",
    },
    "fcmaterials": _COMMON_FIELDS | {
        "MarketID", "CarrierName", "CarrierID", "Items",
    },
}

_REQUIRED_FIELDS = {
    "journal": {"timestamp", "event", "StarSystem", "StarPos", "SystemAddress"},
    "fssallbodiesfound": {
        "timestamp", "event", "SystemName", "StarPos", "SystemAddress", "Count",
    },
    "fssbodysignals": {
        "timestamp", "event", "StarSystem", "StarPos", "SystemAddress", "BodyID", "Signals",
    },
    "fssdiscoveryscan": {
        "timestamp", "event", "SystemName", "StarPos", "SystemAddress", "BodyCount",
        "NonBodyCount",
    },
    "navbeaconscan": {
        "timestamp", "event", "StarSystem", "StarPos", "SystemAddress", "NumBodies",
    },
    "scanbarycentre": {
        "timestamp", "event", "StarSystem", "StarPos", "SystemAddress", "BodyID",
    },
    "codexentry": {
        "timestamp", "event", "System", "StarPos", "SystemAddress", "EntryID",
    },
    "approachsettlement": {
        "timestamp", "event", "StarSystem", "StarPos", "SystemAddress", "Name",
        "BodyID", "BodyName", "Latitude", "Longitude",
    },
    "dockinggranted": {"timestamp", "event", "MarketID", "StationName"},
    "dockingdenied": {"timestamp", "event", "MarketID", "StationName", "Reason"},
    "fcmaterials": {
        "timestamp", "event", "MarketID", "CarrierName", "CarrierID", "Items",
    },
}

_EXPECTED_EVENTS = {
    "fssallbodiesfound": "FSSAllBodiesFound",
    "fssbodysignals": "FSSBodySignals",
    "fssdiscoveryscan": "FSSDiscoveryScan",
    "navbeaconscan": "NavBeaconScan",
    "scanbarycentre": "ScanBaryCentre",
    "codexentry": "CodexEntry",
    "approachsettlement": "ApproachSettlement",
    "dockinggranted": "DockingGranted",
    "dockingdenied": "DockingDenied",
    "fcmaterials": "FCMaterials",
}

_CONTEXT_NAME_KEYS = {
    "journal": "StarSystem",
    "fssallbodiesfound": "SystemName",
    "fssbodysignals": "StarSystem",
    "fssdiscoveryscan": "SystemName",
    "navbeaconscan": "StarSystem",
    "scanbarycentre": "StarSystem",
    "codexentry": "System",
    "approachsettlement": "StarSystem",
}

_FSS_SIGNAL_FIELDS = {
    "timestamp", "SignalName", "SignalType", "IsStation", "USSType",
    "SpawningState", "SpawningFaction", "SpawningPower", "OpposingPower", "ThreatLevel",
}
_MODULE_PATTERN = re.compile(r"(^hpt_|^int_|_armour_)", re.IGNORECASE)
_SKU_PLANETARY = "ELITE_HORIZONS_V_PLANETARY_LANDINGS"
_STRICT_STRING_FIELDS = {
    "timestamp", "event", "SystemName", "StarSystem", "BodyName", "System",
    "Name", "Region", "Category", "SubCategory", "NearestDestination",
    "StationGovernment", "StationAllegiance", "StationEconomy", "StationName",
    "StationType", "Reason", "CarrierName", "CarrierID",
}
_STRICT_INT_FIELDS = {
    "SystemAddress", "BodyID", "MarketID", "Count", "BodyCount", "NonBodyCount",
    "NumBodies", "EntryID", "LandingPad", "VoucherAmount",
}
_STRICT_NUMBER_FIELDS = {
    "Latitude", "Longitude", "Progress", "SemiMajorAxis", "Eccentricity",
    "OrbitalInclination", "Periapsis", "OrbitalPeriod", "AscendingNode", "MeanAnomaly",
}

# The journal/1 schema deliberately permits additional properties, so schema
# validation cannot be the privacy boundary.  Project each supported event onto
# an explicit field contract instead of forwarding every non-denylisted journal
# key.  New Frontier fields therefore stay local until deliberately reviewed.
_GENERAL_FIELDS = {
    "Location": _COMMON_FIELDS | {
        "Docked", "StarSystem", "SystemAddress", "StarPos", "Body", "BodyID",
        "BodyType", "DistFromStarLS", "SystemAllegiance", "SystemEconomy",
        "SystemSecondEconomy", "SystemGovernment", "SystemSecurity", "Population",
        "SystemFaction", "Factions", "Conflicts", "Powers", "PowerplayState",
    },
    "FSDJump": _COMMON_FIELDS | {
        "StarSystem", "SystemAddress", "StarPos", "Body", "BodyID", "BodyType",
        "DistFromStarLS", "SystemAllegiance", "SystemEconomy", "SystemSecondEconomy",
        "SystemGovernment", "SystemSecurity", "Population", "SystemFaction",
        "Factions", "Conflicts", "Powers", "PowerplayState",
    },
    "CarrierJump": _COMMON_FIELDS | {
        "Docked", "StarSystem", "SystemAddress", "StarPos", "Body", "BodyID",
        "BodyType", "DistFromStarLS", "SystemAllegiance", "SystemEconomy",
        "SystemSecondEconomy", "SystemGovernment", "SystemSecurity", "Population",
        "SystemFaction", "Factions", "Conflicts", "Powers", "PowerplayState",
        "StationName", "StationType", "MarketID",
    },
    "Docked": _COMMON_FIELDS | {
        "StationName", "StationType", "StarSystem", "SystemAddress", "StarPos",
        "MarketID", "StationFaction", "StationGovernment", "StationAllegiance",
        "StationServices", "StationEconomy", "StationEconomies", "DistFromStarLS",
        "LandingPads",
    },
    "Scan": _COMMON_FIELDS | {
        "ScanType", "BodyName", "BodyID", "StarSystem", "SystemAddress", "StarPos",
        "DistanceFromArrivalLS", "TidalLock", "TerraformState", "PlanetClass",
        "Atmosphere", "AtmosphereType", "Volcanism", "MassEM", "Radius",
        "SurfaceGravity", "SurfaceTemperature", "SurfacePressure", "Landable",
        "Materials", "Composition", "SemiMajorAxis", "Eccentricity",
        "OrbitalInclination", "Periapsis", "OrbitalPeriod", "RotationPeriod",
        "AxialTilt", "Rings", "ReserveLevel", "Parents", "WasDiscovered",
        "WasMapped", "StarType", "Subclass", "StellarMass", "AbsoluteMagnitude",
        "Age_MY", "Luminosity", "AscendingNode", "MeanAnomaly",
    },
    "SAASignalsFound": _COMMON_FIELDS | {
        "BodyName", "BodyID", "StarSystem", "SystemAddress", "StarPos", "Signals",
    },
}
_GENERAL_NESTED_FIELDS = {
    "SystemFaction": {"Name", "FactionState"},
    "StationFaction": {"Name", "FactionState"},
    "StationEconomies": {"Name", "Proportion"},
    "LandingPads": {"Small", "Medium", "Large"},
    "Materials": {"Name", "Percent"},
    "Composition": {"Ice", "Rock", "Metal"},
    "Rings": {"Name", "RingClass", "MassMT", "InnerRad", "OuterRad"},
    "Signals": {"Type", "Count"},
}
_FACTION_FIELDS = {
    "Name", "FactionState", "Government", "Influence", "Allegiance",
    "PendingStates", "RecoveringStates", "ActiveStates",
}
_FACTION_STATE_FIELDS = {"State", "Trend"}
_CONFLICT_FIELDS = {"WarType", "Status", "Faction1", "Faction2"}
_CONFLICT_FACTION_FIELDS = {"Name", "Stake", "WonDays"}
_PARENT_KEYS = {"Star", "Planet", "Null", "Ring", "Barycentre"}


def enabled():
    from . import settings

    return bool(settings.get("eddn_upload", True))


def extended_enabled():
    """Whether the commander explicitly opted into non-market observations.

    This consent is independent of the established anonymous market toggle;
    neither class silently opts the commander into the other.
    """
    from . import settings

    return bool(settings.get("eddn_extended_upload", False))


def _symbol(raw):
    value = str(raw or "").strip("$;")
    if value.casefold().endswith("_name"):
        value = value[:-5]
    return value.casefold()


def _strip_localised(value, *, general=False, in_faction=False):
    if isinstance(value, list):
        return [
            _strip_localised(item, general=general, in_faction=in_faction)
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    clean = {}
    for key, item in value.items():
        if str(key).endswith("_Localised"):
            continue
        if in_faction and key in _FACTION_PRIVATE:
            continue
        clean[key] = _strip_localised(
            item,
            general=general,
            in_faction=(key == "Factions" or in_faction),
        )
    return clean


def _project_dict(value, fields):
    if not isinstance(value, dict):
        return None
    return {key: value[key] for key in fields if key in value}


def _project_general_nested(message):
    """Apply allowlists below the event root as well as at the root."""
    for field, fields in _GENERAL_NESTED_FIELDS.items():
        if field not in message:
            continue
        value = message[field]
        if field in {"SystemFaction", "StationFaction", "LandingPads", "Composition"}:
            clean = _project_dict(value, fields)
            if clean is None:
                return False
            message[field] = clean
            continue
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            return False
        message[field] = [_project_dict(item, fields) for item in value]

    if "Factions" in message:
        factions = message["Factions"]
        if not isinstance(factions, list) or not all(isinstance(item, dict) for item in factions):
            return False
        projected = []
        for faction in factions:
            clean = _project_dict(faction, _FACTION_FIELDS)
            for state_field in ("PendingStates", "RecoveringStates", "ActiveStates"):
                if state_field not in clean:
                    continue
                states = clean[state_field]
                if not isinstance(states, list) or not all(isinstance(item, dict) for item in states):
                    return False
                clean[state_field] = [_project_dict(item, _FACTION_STATE_FIELDS) for item in states]
            projected.append(clean)
        message["Factions"] = projected

    if "Conflicts" in message:
        conflicts = message["Conflicts"]
        if not isinstance(conflicts, list) or not all(isinstance(item, dict) for item in conflicts):
            return False
        projected = []
        for conflict in conflicts:
            clean = _project_dict(conflict, _CONFLICT_FIELDS)
            for side in ("Faction1", "Faction2"):
                if side in clean:
                    clean[side] = _project_dict(clean[side], _CONFLICT_FACTION_FIELDS)
                    if clean[side] is None:
                        return False
            projected.append(clean)
        message["Conflicts"] = projected

    if "Parents" in message:
        parents = message["Parents"]
        if not isinstance(parents, list) or not all(isinstance(item, dict) for item in parents):
            return False
        clean_parents = []
        for parent in parents:
            clean = {
                key: value for key, value in parent.items()
                if key in _PARENT_KEYS and _is_int(value)
            }
            if len(clean) != len(parent):
                return False
            clean_parents.append(clean)
        message["Parents"] = clean_parents

    if "Powers" in message and not (
        isinstance(message["Powers"], list)
        and all(isinstance(item, str) for item in message["Powers"])
    ):
        return False
    if "StationServices" in message and not (
        isinstance(message["StationServices"], list)
        and all(isinstance(item, str) for item in message["StationServices"])
    ):
        return False
    return True


def _fresh(timestamp, max_age=MAX_AGE_S):
    updated = marketdb.parse_update_time(timestamp)
    return bool(updated and 0 <= marketdb.now_epoch() - updated <= max_age)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_pos(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(_is_number(part) for part in value)
    )


def _add_flags(message, horizons, odyssey):
    if horizons is not None:
        message["horizons"] = bool(horizons)
    if odyssey is not None:
        message["odyssey"] = bool(odyssey)
    return message


def _trusted_context(location):
    location = location or {}
    system = location.get("system")
    address = location.get("system_address")
    star_pos = location.get("star_pos")
    if not isinstance(system, str) or not system or not _is_int(address) or not _valid_pos(star_pos):
        return None
    return {
        "system": system,
        "system_address": address,
        "star_pos": list(star_pos),
        "body_name": location.get("body_name"),
        "body_id": location.get("body_id"),
    }


def _context_matches(event, context, name_key):
    """Cross-check source identity before adding coordinates from prior events."""
    source_address = event.get("SystemAddress")
    if source_address is not None and source_address != context["system_address"]:
        return False
    source_name = event.get(name_key)
    if source_name is not None and (
        not isinstance(source_name, str)
        or source_name.casefold() != context["system"].casefold()
    ):
        return False
    source_pos = event.get("StarPos")
    if source_pos is not None and (
        not _valid_pos(source_pos) or list(source_pos) != context["star_pos"]
    ):
        return False
    return True


def _required_present(schema_name, message):
    for field in _REQUIRED_FIELDS[schema_name]:
        if field not in message or message[field] is None:
            return False
        if isinstance(message[field], str) and not message[field]:
            return False
    return True


def _project_approach_nested(message):
    if "StationEconomies" in message:
        economies = message["StationEconomies"]
        if not isinstance(economies, list):
            return False
        message["StationEconomies"] = [
            {key: value for key, value in item.items() if key in {"Name", "Proportion"}}
            for item in economies
            if isinstance(item, dict)
        ]
        if len(message["StationEconomies"]) != len(economies):
            return False
    if "StationFaction" in message:
        faction = message["StationFaction"]
        if not isinstance(faction, dict):
            return False
        message["StationFaction"] = {
            key: value for key, value in faction.items() if key in {"Name", "FactionState"}
        }
    if "StationServices" in message and not (
        isinstance(message["StationServices"], list)
        and all(isinstance(item, str) for item in message["StationServices"])
    ):
        return False
    return True


def _valid_strict_message(schema_name, message):
    if set(message) - _STRICT_FIELDS[schema_name]:
        return False
    if not _required_present(schema_name, message):
        return False
    if message.get("event") != _EXPECTED_EVENTS[schema_name]:
        return False
    if not isinstance(message.get("timestamp"), str):
        return False
    for flag in ("horizons", "odyssey"):
        if flag in message and not isinstance(message[flag], bool):
            return False
    for field in _STRICT_STRING_FIELDS:
        if field in message and not isinstance(message[field], str):
            return False
    for field in _STRICT_INT_FIELDS:
        if field in message and not _is_int(message[field]):
            return False
    for field in _STRICT_NUMBER_FIELDS:
        if field in message and not _is_number(message[field]):
            return False
    if "StarPos" in message and not _valid_pos(message["StarPos"]):
        return False
    if schema_name == "fssbodysignals":
        signals = message.get("Signals")
        if not isinstance(signals, list):
            return False
        if any(
            not isinstance(item, dict)
            or set(item) - {"Type", "Count"}
            or not isinstance(item.get("Type"), str)
            or not _is_int(item.get("Count"))
            for item in signals
        ):
            return False
    if schema_name == "fcmaterials":
        items = message.get("Items")
        if not isinstance(items, list):
            return False
        for item in items:
            if not isinstance(item, dict):
                return False
            if not {"id", "Name", "Price", "Stock", "Demand"}.issubset(item):
                return False
            if not isinstance(item["Name"], str) or not item["Name"]:
                return False
            if any(not _is_int(item[field]) for field in ("id", "Price", "Stock", "Demand")):
                return False
    if schema_name == "codexentry" and "Traits" in message and not (
        isinstance(message["Traits"], list)
        and all(isinstance(item, str) and item for item in message["Traits"])
    ):
        return False
    if schema_name == "approachsettlement":
        for item in message.get("StationEconomies", []):
            if "Name" in item and not isinstance(item["Name"], str):
                return False
            if "Proportion" in item and not _is_number(item["Proportion"]):
                return False
        faction = message.get("StationFaction", {})
        if any(not isinstance(value, str) for value in faction.values()):
            return False
    return True


def _build_journal_message(schema_name, event, location, horizons, odyssey):
    general = schema_name == "journal"
    clean = _strip_localised(deepcopy(event), general=general)
    if schema_name == "codexentry":
        for field in _CODEX_PRIVATE:
            clean.pop(field, None)

    name_key = _CONTEXT_NAME_KEYS.get(schema_name)
    if name_key:
        context = _trusted_context(location)
        if context is None or not _context_matches(clean, context, name_key):
            return None
        clean.setdefault(name_key, context["system"])
        clean.setdefault("SystemAddress", context["system_address"])
        clean.setdefault("StarPos", context["star_pos"])
    else:
        context = None

    if general:
        fields = _GENERAL_FIELDS.get(clean.get("event"))
        if fields is None:
            return None
        message = {key: value for key, value in clean.items() if key in fields}
        if not _project_general_nested(message):
            return None
        _add_flags(message, horizons, odyssey)
        if not _required_present("journal", message):
            return None
        if (
            not isinstance(message.get("timestamp"), str)
            or not isinstance(message.get("event"), str)
            or not isinstance(message.get("StarSystem"), str)
            or not _valid_pos(message.get("StarPos"))
            or not _is_int(message.get("SystemAddress"))
        ):
            return None
        return message

    fields = _STRICT_FIELDS[schema_name]
    message = {key: value for key, value in clean.items() if key in fields}

    if schema_name == "codexentry":
        # Body identity is only safe when Status.json's body agrees with the
        # separately tracked journal body. The watcher supplies only a matched
        # pair; raw event values are deliberately ignored.
        message.pop("BodyName", None)
        message.pop("BodyID", None)
        if context and context.get("body_name"):
            message["BodyName"] = context["body_name"]
            if _is_int(context.get("body_id")):
                message["BodyID"] = context["body_id"]

    if schema_name == "fssbodysignals" and isinstance(message.get("Signals"), list):
        message["Signals"] = [
            {key: value for key, value in item.items() if key in {"Type", "Count"}}
            if isinstance(item, dict) else item
            for item in message["Signals"]
        ]
    if schema_name == "approachsettlement" and not _project_approach_nested(message):
        return None

    _add_flags(message, horizons, odyssey)
    return message if _valid_strict_message(schema_name, message) else None


def _clean_module_name(raw):
    value = str(raw or "").strip("$;")
    if value.casefold().endswith("_name"):
        value = value[:-5]
    return value


def _build_snapshot_message(schema_name, data, horizons, odyssey):
    timestamp = data.get("timestamp")
    if schema_name == "outfitting":
        if data.get("event") != "Outfitting":
            return None
        modules = []
        seen = set()
        for item in data.get("Items") or []:
            if not isinstance(item, dict):
                return None
            name = _clean_module_name(item.get("Name"))
            sku = item.get("sku") if "sku" in item else item.get("SKU")
            if sku is not None and str(sku).casefold() != _SKU_PLANETARY.casefold():
                continue
            if name.casefold() == "int_planetapproachsuite" or not _MODULE_PATTERN.search(name):
                continue
            marker = name.casefold()
            if marker not in seen:
                seen.add(marker)
                modules.append(name)
        message = {
            "systemName": data.get("StarSystem"),
            "stationName": data.get("StationName"),
            "marketId": data.get("MarketID"),
            "timestamp": timestamp,
            "modules": modules,
        }
        _add_flags(message, horizons, odyssey)
        if not (
            isinstance(message["systemName"], str) and message["systemName"]
            and isinstance(message["stationName"], str) and message["stationName"]
            and _is_int(message["marketId"])
            and isinstance(timestamp, str)
            and modules
        ):
            return None
        return message

    if schema_name == "shipyard":
        if data.get("event") != "Shipyard":
            return None
        ships = []
        seen = set()
        for item in data.get("PriceList") or []:
            if not isinstance(item, dict):
                return None
            name = item.get("ShipType") or item.get("name") or item.get("Name")
            if not isinstance(name, str) or not name:
                return None
            marker = name.casefold()
            if marker not in seen:
                seen.add(marker)
                ships.append(name)
        message = {
            "systemName": data.get("StarSystem"),
            "stationName": data.get("StationName"),
            "marketId": data.get("MarketID"),
            "timestamp": timestamp,
            "ships": ships,
        }
        allow_cobra = data.get("AllowCobraMkIV")
        if allow_cobra is not None:
            if not isinstance(allow_cobra, bool):
                return None
            message["allowCobraMkIV"] = allow_cobra
        _add_flags(message, horizons, odyssey)
        if not (
            isinstance(message["systemName"], str) and message["systemName"]
            and isinstance(message["stationName"], str) and message["stationName"]
            and _is_int(message["marketId"])
            and isinstance(timestamp, str)
            and ships
        ):
            return None
        return message

    if schema_name == "navroute":
        if data.get("event") != "NavRoute":
            return None
        route = []
        for item in data.get("Route") or []:
            if not isinstance(item, dict):
                return None
            row = {key: item.get(key) for key in (
                "StarSystem", "SystemAddress", "StarPos", "StarClass",
            )}
            if not (
                isinstance(row["StarSystem"], str) and row["StarSystem"]
                and _is_int(row["SystemAddress"])
                and _valid_pos(row["StarPos"])
                and isinstance(row["StarClass"], str) and row["StarClass"]
            ):
                return None
            row["StarPos"] = list(row["StarPos"])
            route.append(row)
        if not route or not isinstance(timestamp, str):
            return None
        message = {"timestamp": timestamp, "event": "NavRoute", "Route": route}
        return _add_flags(message, horizons, odyssey)

    if schema_name == "fcmaterials":
        clean = _strip_localised(deepcopy(data))
        message = {
            key: value for key, value in clean.items() if key in _STRICT_FIELDS["fcmaterials"]
        }
        _add_flags(message, horizons, odyssey)
        return message if _valid_strict_message("fcmaterials", message) else None

    return None


class EddnUploader:
    def __init__(self):
        self._lock = threading.Lock()
        self._signal_lock = threading.Lock()
        self._last_key = None
        self._pending_signals = []
        self.uploads = 0
        self.last_upload_at = None
        self.last_error = None
        self.by_schema = {}

    def stats(self):
        with self._lock:
            return {
                "enabled": enabled(),
                "market_enabled": enabled(),
                "extended_enabled": extended_enabled(),
                "uploads": self.uploads,
                "last_upload_at": self.last_upload_at,
                "last_error": self.last_error,
                "by_schema": dict(self.by_schema),
            }

    @staticmethod
    def _header(commander, game_version=None, game_build=None):
        # EDDN requires best-effort source metadata and explicitly says to send
        # empty strings rather than omit these keys when the journal lacks it.
        return {
            "uploaderID": _pseudonymous_uploader(commander),
            "softwareName": SOFTWARE_NAME,
            "softwareVersion": SOFTWARE_VERSION,
            "gameversion": "" if game_version is None else str(game_version),
            "gamebuild": "" if game_build is None else str(game_build),
        }

    def _capture_fss_signal(
        self, event, commander, game_version, game_build, horizons, odyssey
    ):
        if not _fresh(event.get("timestamp")):
            return
        if event.get("USSType") == "$USS_Type_MissionTarget;":
            return
        if not _is_int(event.get("SystemAddress")):
            return
        clean = _strip_localised(deepcopy(event))
        signal = {key: value for key, value in clean.items() if key in _FSS_SIGNAL_FIELDS}
        if not isinstance(signal.get("timestamp"), str) or not isinstance(signal.get("SignalName"), str):
            return
        for field in (
            "SignalType", "USSType", "SpawningState", "SpawningFaction",
            "SpawningPower", "OpposingPower",
        ):
            if field in signal and not isinstance(signal[field], str):
                return
        if "IsStation" in signal and not isinstance(signal["IsStation"], bool):
            return
        if "ThreatLevel" in signal and not _is_int(signal["ThreatLevel"]):
            return
        with self._signal_lock:
            self._pending_signals.append({
                "signal": signal,
                "system_address": event["SystemAddress"],
                "commander": commander,
                "game_version": game_version,
                "game_build": game_build,
                "horizons": horizons,
                "odyssey": odyssey,
            })

    def flush_fss_signals(self, location, commander=None, *, preserve_unmatched=False):
        """Publish the part of a contiguous batch belonging to ``location``.

        Signals can appear either side of the Location/FSDJump event depending
        on the game client. A pre-handler flush retains address-mismatched rows
        so the post-handler flush can try them against the newly trusted tuple.
        """
        if not extended_enabled():
            with self._signal_lock:
                self._pending_signals = []
            return
        with self._signal_lock:
            pending, self._pending_signals = self._pending_signals, []
        if not pending:
            return
        context = _trusted_context(location)
        if context is None:
            if preserve_unmatched:
                with self._signal_lock:
                    self._pending_signals = pending + self._pending_signals
            return
        current = [
            item for item in pending
            if (commander is None or item["commander"] == commander)
            and _fresh(item["signal"].get("timestamp"))
        ]
        matching = [
            item for item in current
            if item["system_address"] == context["system_address"]
        ]
        if preserve_unmatched:
            unmatched = [
                item for item in current
                if item["system_address"] != context["system_address"]
            ]
            if unmatched:
                with self._signal_lock:
                    self._pending_signals = unmatched + self._pending_signals
        if not matching:
            return
        first = matching[0]
        signals = [item["signal"] for item in matching]
        message = {
            "event": "FSSSignalDiscovered",
            "timestamp": signals[0]["timestamp"],
            "SystemAddress": context["system_address"],
            "signals": signals,
            "StarSystem": context["system"],
            "StarPos": context["star_pos"],
        }
        _add_flags(message, first["horizons"], first["odyssey"])
        key = (
            "fsssignaldiscovered", context["system_address"], message["timestamp"],
            tuple(signal.get("SignalName") for signal in signals),
        )
        self._queue_envelope(
            "fsssignaldiscovered", message, first["commander"],
            first["game_version"], first["game_build"], key,
        )

    def maybe_publish_journal(
        self,
        event,
        commander,
        location=None,
        game_version=None,
        game_build=None,
        horizons=None,
        odyssey=None,
    ):
        """Publish a fresh journal observation supported by an official schema."""
        if not isinstance(event, dict):
            return
        if not extended_enabled():
            with self._signal_lock:
                self._pending_signals = []
            return

        event_name = event.get("event")
        if event_name == "FSSSignalDiscovered":
            self._capture_fss_signal(
                event, commander, game_version, game_build, horizons, odyssey
            )
            return

        # Any other event terminates the contiguous FSS signal run. This runs
        # before schema selection so an ordinary Music event can flush a batch.
        self.flush_fss_signals(location, commander)

        schema_name = JOURNAL_SCHEMAS.get(event_name)
        if not schema_name or not _fresh(event.get("timestamp")):
            return
        message = _build_journal_message(
            schema_name, event, location or {}, horizons, odyssey
        )
        if message is None:
            return
        key = (schema_name, event_name, message.get("timestamp"), message.get("BodyID"))
        self._queue_envelope(
            schema_name, message, commander, game_version, game_build, key
        )

    def maybe_publish_snapshot(
        self,
        kind,
        data,
        commander,
        game_version=None,
        game_build=None,
        max_age=MAX_AGE_S,
        *,
        horizons=None,
        odyssey=None,
    ):
        """Publish a fresh game-written JSON snapshot using a strict builder."""
        schema_name = str(kind or "").lower()
        if not extended_enabled() or schema_name not in {
            "outfitting", "shipyard", "navroute", "fcmaterials",
        }:
            return
        if not isinstance(data, dict) or not _fresh(data.get("timestamp"), max_age=max_age):
            return
        message = _build_snapshot_message(schema_name, data, horizons, odyssey)
        if message is None:
            return
        key = (
            schema_name,
            message.get("marketId", message.get("MarketID")),
            message.get("timestamp"),
        )
        self._queue_envelope(
            schema_name, message, commander, game_version, game_build, key
        )

    def _queue_envelope(self, schema_name, message, commander, game_version, game_build, key):
        with self._lock:
            if key == self._last_key:
                return
            self._last_key = key
        envelope = {
            "$schemaRef": SCHEMAS[schema_name],
            "header": self._header(commander, game_version, game_build),
            "message": message,
        }
        threading.Thread(
            target=self._publish_envelope,
            args=(schema_name, envelope),
            name=f"eddn-upload-{schema_name}",
            daemon=True,
        ).start()

    def maybe_publish(
        self, market, commander, game_version=None, game_build=None,
        horizons=None, odyssey=None,
    ):
        """Called by the journal watcher whenever Market.json changes."""
        if not enabled() or not isinstance(market, dict):
            return
        market_id = market.get("MarketID")
        timestamp = market.get("timestamp")
        if (
            market.get("event") != "Market"
            or not _is_int(market_id)
            or not isinstance(market.get("StarSystem"), str)
            or not market.get("StarSystem")
            or not isinstance(market.get("StationName"), str)
            or not market.get("StationName")
            or not isinstance(market.get("Items"), list)
            or not _fresh(timestamp)
        ):
            return
        key = (market_id, timestamp)
        with self._lock:
            if key == self._last_key:
                return
            self._last_key = key
        threading.Thread(
            target=self._publish,
            args=(market, commander, game_version, game_build, horizons, odyssey),
            name="eddn-upload",
            daemon=True,
        ).start()

    def _publish(
        self, market, commander, game_version=None, game_build=None,
        horizons=None, odyssey=None,
    ):
        commodities = []
        for item in market.get("Items") or []:
            if not isinstance(item, dict):
                continue
            category = _symbol(item.get("Category"))
            if category in SKIP_CATEGORIES or item.get("Legality"):
                continue
            name = _symbol(item.get("Name"))
            values = {
                "meanPrice": item.get("MeanPrice", 0),
                "buyPrice": item.get("BuyPrice", 0),
                "stock": item.get("Stock", 0),
                "stockBracket": item.get("StockBracket", 0),
                "sellPrice": item.get("SellPrice", 0),
                "demand": item.get("Demand", 0),
                "demandBracket": item.get("DemandBracket", 0),
            }
            if not name:
                continue
            if any(
                not _is_int(values[field])
                for field in ("meanPrice", "buyPrice", "stock", "sellPrice", "demand")
            ):
                continue
            if any(
                values[field] not in (0, 1, 2, 3, "")
                for field in ("stockBracket", "demandBracket")
            ):
                continue
            commodities.append({"name": name, **values})
        if not commodities:
            return
        message = {
            "systemName": market["StarSystem"],
            "stationName": market["StationName"],
            "marketId": market["MarketID"],
            "timestamp": market["timestamp"],
            "commodities": commodities,
        }
        _add_flags(message, horizons, odyssey)
        envelope = {
            "$schemaRef": SCHEMA,
            "header": self._header(commander, game_version, game_build),
            "message": message,
        }
        self._publish_envelope("commodity", envelope)

    def _publish_envelope(self, schema_name, envelope):
        try:
            body = gzip.compress(json.dumps(envelope).encode("utf-8"))
            resp = requests.post(
                UPLOAD_URL,
                data=body,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Content-Encoding": "gzip",
                },
                timeout=20,
            )
            with self._lock:
                if resp.status_code == 200:
                    self.uploads += 1
                    self.by_schema[schema_name] = self.by_schema.get(schema_name, 0) + 1
                    self.last_upload_at = marketdb.utc_now_iso()
                    self.last_error = None
                else:
                    self.last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
        except requests.RequestException as exc:
            with self._lock:
                self.last_error = str(exc)[:200]


UPLOADER = EddnUploader()
