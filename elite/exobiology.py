"""Persistent, body-local exobiology sample and waypoint maps."""

from __future__ import annotations

import math
import uuid

from . import biovalues, flight
from .workflowdb import WorkflowStore, event_epoch_ms


WORKFLOW = "exobiology_map"
MAX_PINS_PER_BODY = 2_000
DEFAULT_SURVEY_PAGE_SIZE = 50
MAX_SURVEY_PAGE_SIZE = 200
DEFAULT_PIN_PAGE_SIZE = 200
MAX_PIN_PAGE_SIZE = 500
JOURNAL_EVENTS = frozenset(
    {
        "ApproachBody",
        "CodexEntry",
        "Died",
        "FSDJump",
        "Location",
        "SAASignalsFound",
        "Scan",
        "ScanOrganic",
        "SellOrganicData",
        "Touchdown",
    }
)


def _integer(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _default_state() -> dict:
    return {
        "version": 1,
        "system": None,
        "system_address": None,
        "body_ids": {},
        "surveys": {},
        "sampling": None,
        "last_sale_ts": None,
    }


def _longitude(value: float) -> float:
    return ((value + 180.0) % 360.0) - 180.0


def normalise_position(position: dict | None) -> dict | None:
    if not position:
        return None
    lat = _number(position.get("lat", position.get("Latitude")))
    lon = _number(position.get("lon", position.get("Longitude")))
    if lat is None or lon is None or not -90 <= lat <= 90:
        return None
    radius = _number(
        position.get("radius_m", position.get("PlanetRadius", position.get("Radius")))
    )
    heading = _number(position.get("heading", position.get("Heading")))
    altitude = _number(position.get("alt_m", position.get("Altitude")))
    return {
        "lat": lat,
        "lon": _longitude(lon),
        "body": position.get("body") or position.get("BodyName"),
        "radius_m": radius if radius and radius > 0 else None,
        "heading": heading % 360 if heading is not None else None,
        "alt_m": altitude,
    }


def surface_vector(origin: dict, destination: dict, radius_m: float | None = None) -> dict:
    """Great-circle distance/bearing plus local east/north map coordinates."""
    radius = _number(radius_m) or _number(origin.get("radius_m")) or _number(destination.get("radius_m"))
    if not radius:
        return {"distance_m": None, "bearing_deg": None, "east_m": None, "north_m": None}
    lat1, lon1 = _number(origin.get("lat")), _number(origin.get("lon"))
    lat2, lon2 = _number(destination.get("lat")), _number(destination.get("lon"))
    if None in (lat1, lon1, lat2, lon2):
        return {"distance_m": None, "bearing_deg": None, "east_m": None, "north_m": None}
    distance = flight.surface_distance_m(lat1, lon1, lat2, lon2, radius)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_lon = math.radians(_longitude(lon2 - lon1))
    y = math.sin(delta_lon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lon)
    bearing = math.degrees(math.atan2(y, x)) % 360
    return {
        "distance_m": round(distance, 1),
        "bearing_deg": round(bearing, 1),
        "east_m": round(distance * math.sin(math.radians(bearing)), 1),
        "north_m": round(distance * math.cos(math.radians(bearing)), 1),
    }


def _survey_key(system_address, system, body) -> str:
    location = str(system_address) if system_address is not None else str(system or "unknown")
    return f"{location}|{body or 'unknown body'}"


def _ensure_survey(state: dict, body: str | None, radius_m=None, body_id=None) -> dict:
    body = body or "Unknown body"
    key = _survey_key(state.get("system_address"), state.get("system"), body)
    survey = state["surveys"].setdefault(
        key,
        {
            "key": key,
            "system": state.get("system"),
            "system_address": state.get("system_address"),
            "body": body,
            "body_id": body_id,
            "radius_m": radius_m,
            "signal_count": None,
            "genuses": [],
            "pins": [],
            "completed": {},
            "truncated_pins": 0,
            "updated_ts": None,
        },
    )
    if radius_m:
        survey["radius_m"] = radius_m
    if body_id is not None:
        survey["body_id"] = body_id
    return survey


def _body_name(state: dict, event: dict, position: dict | None = None) -> str | None:
    explicit = event.get("BodyName")
    if explicit:
        return explicit
    body = event.get("Body")
    if isinstance(body, str):
        return body
    if body is not None:
        mapped = state.get("body_ids", {}).get(str(body))
        if mapped:
            return mapped.get("name")
    return (position or {}).get("body")


def _pin(
    survey: dict,
    position: dict,
    *,
    kind: str,
    label: str,
    ts: int,
    source: str,
    metadata: dict | None = None,
) -> dict:
    pin = {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "label": label,
        "lat": position["lat"],
        "lon": position["lon"],
        "heading": position.get("heading"),
        "alt_m": position.get("alt_m"),
        "timestamp": ts,
        "source": source,
        "metadata": metadata or {},
    }
    survey["pins"].append(pin)
    if len(survey["pins"]) > MAX_PINS_PER_BODY:
        overflow = len(survey["pins"]) - MAX_PINS_PER_BODY
        del survey["pins"][:overflow]
        survey["truncated_pins"] = (survey.get("truncated_pins") or 0) + overflow
    survey["updated_ts"] = ts
    return pin


def _page_number(value, default=1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _page_size(value, default, maximum) -> int:
    try:
        return max(1, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _present(
    state: dict,
    position: dict | None,
    body=None,
    *,
    survey_page=1,
    survey_page_size=DEFAULT_SURVEY_PAGE_SIZE,
    pin_page=1,
    pin_page_size=DEFAULT_PIN_PAGE_SIZE,
    _pin_page_size_cap=MAX_PIN_PAGE_SIZE,
) -> dict:
    position = normalise_position(position)
    surveys = state.get("surveys") or {}
    chosen = None
    if body:
        matches = [survey for survey in surveys.values() if survey.get("body") == body]
        chosen = max(matches, key=lambda row: row.get("updated_ts") or 0, default=None)
    elif position and position.get("body"):
        matches = [survey for survey in surveys.values() if survey.get("body") == position["body"]]
        chosen = max(matches, key=lambda row: row.get("updated_ts") or 0, default=None)
    elif state.get("sampling"):
        chosen = surveys.get(state["sampling"].get("survey_key"))
    if chosen is None and surveys:
        chosen = max(surveys.values(), key=lambda row: row.get("updated_ts") or 0)

    rendered = None
    if chosen:
        radius = (position or {}).get("radius_m") or chosen.get("radius_m")
        center = position if position and (
            not position.get("body") or position.get("body") == chosen.get("body")
        ) else None
        if center is None and chosen.get("pins"):
            first = chosen["pins"][0]
            center = {"lat": first["lat"], "lon": first["lon"], "radius_m": radius}
        all_pins = chosen.get("pins") or []
        pin_page = _page_number(pin_page)
        pin_page_size = _page_size(
            pin_page_size, DEFAULT_PIN_PAGE_SIZE, _pin_page_size_cap)
        pin_total = len(all_pins)
        # Page one is the most recent set because that is what the live cockpit
        # needs. Keep each page chronological so the existing reverse render
        # still displays newest-first.
        pin_end = max(0, pin_total - (pin_page - 1) * pin_page_size)
        pin_start = max(0, pin_end - pin_page_size)
        pins = []
        for item in all_pins[pin_start:pin_end]:
            point = dict(item)
            vector = surface_vector(center, point, radius) if center else {
                "distance_m": None, "bearing_deg": None, "east_m": None, "north_m": None
            }
            if center and center.get("heading") is not None and vector["bearing_deg"] is not None:
                vector["relative_bearing_deg"] = round(
                    (vector["bearing_deg"] - center["heading"] + 540) % 360 - 180, 1
                )
            else:
                vector["relative_bearing_deg"] = None
            pins.append({**point, **vector})
        rendered = {
            **chosen,
            "radius_m": radius,
            "pins": pins,
            "pins_total": pin_total,
            "pin_page": pin_page,
            "pin_page_size": pin_page_size,
            "pin_pages": max(1, math.ceil(pin_total / pin_page_size)),
            "center": center,
        }

    sampling = dict(state.get("sampling") or {}) or None
    if sampling and position:
        survey = surveys.get(sampling.get("survey_key")) or {}
        sample_points = [
            {"lat": pin["lat"], "lon": pin["lon"], "body": survey.get("body")}
            for pin in survey.get("pins") or []
            if pin.get("metadata", {}).get("sample_group") == sampling.get("sample_group")
            and pin.get("metadata", {}).get("scan_type") in {"Log", "Sample"}
        ]
        pos = {**position, "body": position.get("body") or survey.get("body")}
        sampling["clearance"] = flight.sample_clearance(
            sample_points, pos, sampling.get("colony_m")
        )

    index = [
        {
            "key": survey["key"],
            "system": survey.get("system"),
            "body": survey.get("body"),
            "pins": len(survey.get("pins") or []),
            "completed": len(survey.get("completed") or {}),
            "updated_ts": survey.get("updated_ts"),
        }
        for survey in surveys.values()
    ]
    index.sort(key=lambda row: row.get("updated_ts") or 0, reverse=True)
    survey_page = _page_number(survey_page)
    survey_page_size = _page_size(
        survey_page_size, DEFAULT_SURVEY_PAGE_SIZE, MAX_SURVEY_PAGE_SIZE)
    survey_total = len(index)
    survey_start = (survey_page - 1) * survey_page_size
    survey_rows = index[survey_start:survey_start + survey_page_size]
    return {
        "system": state.get("system"),
        "system_address": state.get("system_address"),
        "position": position,
        "sampling": sampling,
        "current_map": rendered,
        "surveys": survey_rows,
        "survey_page": survey_page,
        "survey_page_size": survey_page_size,
        "surveys_total": survey_total,
        "survey_pages": max(1, math.ceil(survey_total / survey_page_size)),
        "last_sale_ts": state.get("last_sale_ts"),
    }


class ExobiologyMapper:
    """Surface map reducer; Status.json positions are supplied with update_position."""

    def __init__(self, commander_id: str | None = None):
        self.store = WorkflowStore(WORKFLOW, _default_state, commander_id)
        self._position = None

    def update_position(self, position: dict | None, *, body=None, radius_m=None) -> dict:
        merged = dict(position or {})
        if body is not None:
            merged["body"] = body
        if radius_m is not None:
            merged["radius_m"] = radius_m
        self._position = normalise_position(merged)
        return self.snapshot()

    def _reduce(self, state: dict, event: dict, ts: int) -> bool:
        kind = event["event"]
        position = self._position
        if kind in {"Location", "FSDJump"}:
            old_address = state.get("system_address")
            new_address = event.get("SystemAddress")
            state["system"] = event.get("StarSystem") or event.get("SystemName")
            state["system_address"] = new_address
            if old_address is not None and new_address != old_address:
                state["body_ids"] = {}
            if state.get("sampling") and state["sampling"].get("system_address") != state["system_address"]:
                state["sampling"] = None
            return True
        if kind == "ApproachBody":
            body = _body_name(state, event, position)
            _ensure_survey(state, body, body_id=event.get("BodyID"))["updated_ts"] = ts
            return True
        if kind == "Scan":
            body = _body_name(state, event, position)
            body_id = event.get("BodyID")
            radius = _number(event.get("Radius"))
            if body_id is not None and body:
                state["body_ids"][str(body_id)] = {"name": body, "radius_m": radius}
            _ensure_survey(state, body, radius, body_id)["updated_ts"] = ts
            return True
        if kind == "SAASignalsFound":
            body = _body_name(state, event, position)
            survey = _ensure_survey(state, body, body_id=event.get("BodyID"))
            biological = next(
                (
                    _integer(row.get("Count"))
                    for row in event.get("Signals") or []
                    if "biological" in str(row.get("Type") or "").casefold()
                ),
                None,
            )
            if biological is not None:
                survey["signal_count"] = biological
            survey["genuses"] = [
                {
                    "symbol": row.get("Genus"),
                    "name": row.get("Genus_Localised") or row.get("Genus"),
                }
                for row in event.get("Genuses") or []
                if row.get("Genus")
            ]
            survey["updated_ts"] = ts
            return True
        if kind == "ScanOrganic":
            body = _body_name(state, event, position)
            body_id = event.get("Body") if not isinstance(event.get("Body"), str) else None
            mapped = state.get("body_ids", {}).get(str(body_id)) if body_id is not None else None
            radius = (position or {}).get("radius_m") or (mapped or {}).get("radius_m")
            survey = _ensure_survey(state, body, radius, body_id)
            scan_type = event.get("ScanType")
            genus = event.get("Genus_Localised") or event.get("Genus")
            species = event.get("Species_Localised") or event.get("Species")
            variant = event.get("Variant_Localised") or event.get("Variant")
            previous = state.get("sampling") or {}
            same = previous.get("species") == species and previous.get("survey_key") == survey["key"]
            if scan_type == "Log" or not same:
                group = uuid.uuid4().hex
                progress = 1 if scan_type == "Log" else (3 if scan_type == "Analyse" else 2)
            else:
                group = previous.get("sample_group") or uuid.uuid4().hex
                progress = min(3, (previous.get("progress") or 1) + 1)
            if position:
                _pin(
                    survey,
                    position,
                    kind="organic_sample",
                    label=variant or species or genus or "Organic sample",
                    ts=ts,
                    source="ScanOrganic",
                    metadata={
                        "scan_type": scan_type,
                        "sample_group": group,
                        "progress": progress,
                        "genus": genus,
                        "species": species,
                        "variant": variant,
                    },
                )
            if scan_type == "Analyse":
                key = str(species or genus or "unknown")
                completed = survey["completed"].setdefault(
                    key,
                    {"genus": genus, "species": species, "variant": variant, "count": 0},
                )
                completed["count"] += 1
                completed["last_completed_ts"] = ts
                state["sampling"] = None
            else:
                state["sampling"] = {
                    "survey_key": survey["key"],
                    "system_address": state.get("system_address"),
                    "body": survey.get("body"),
                    "sample_group": group,
                    "genus": genus,
                    "species": species,
                    "variant": variant,
                    "progress": progress,
                    "colony_m": biovalues.GENUS_COLONY_M.get(genus),
                }
            survey["updated_ts"] = ts
            return True
        if kind in {"Touchdown", "CodexEntry"}:
            if not position:
                return False
            body = _body_name(state, event, position)
            survey = _ensure_survey(state, body, position.get("radius_m"))
            if kind == "Touchdown":
                label, pin_kind = "Landing site", "landing"
            else:
                label = event.get("Name_Localised") or event.get("Name") or "Codex discovery"
                pin_kind = "codex"
            _pin(survey, position, kind=pin_kind, label=label, ts=ts, source=kind)
            return True
        if kind == "Died":
            state["sampling"] = None
            return True
        if kind == "SellOrganicData":
            state["last_sale_ts"] = ts
            return True
        return False

    def observe_event(self, event: dict, event_uid: str | None = None) -> dict:
        if not isinstance(event, dict) or event.get("event") not in JOURNAL_EVENTS:
            return self.snapshot()
        state, _ = self.store.apply_event(event, self._reduce, event_uid)
        return _present(state, self._position)

    def add_pin(
        self, label: str, *, kind="waypoint", position=None, metadata=None, timestamp=None
    ) -> dict:
        point = normalise_position(position) if position is not None else self._position
        if not point:
            raise ValueError("a valid surface position is required")
        ts = event_epoch_ms(timestamp)

        def change(state):
            survey = _ensure_survey(state, point.get("body"), point.get("radius_m"))
            _pin(
                survey,
                point,
                kind=str(kind),
                label=str(label or "Waypoint"),
                ts=ts,
                source="manual",
                metadata=dict(metadata or {}),
            )
            return True

        state, _ = self.store.mutate(change)
        return _present(state, self._position)

    def remove_pin(self, pin_id: str) -> bool:
        removed = False

        def change(state):
            nonlocal removed
            for survey in state.get("surveys", {}).values():
                before = len(survey.get("pins") or [])
                survey["pins"] = [pin for pin in survey.get("pins") or [] if pin.get("id") != pin_id]
                if len(survey["pins"]) != before:
                    removed = True
                    return True
            return False

        self.store.mutate(change)
        return removed

    def clear_body(self, body: str) -> int:
        removed = 0

        def change(state):
            nonlocal removed
            keys = [key for key, survey in state.get("surveys", {}).items() if survey.get("body") == body]
            for key in keys:
                removed += len(state["surveys"][key].get("pins") or [])
                state["surveys"].pop(key, None)
            if state.get("sampling") and state["sampling"].get("body") == body:
                state["sampling"] = None
            return bool(keys)

        self.store.mutate(change)
        return removed

    def snapshot(
        self, body=None, *, survey_page=1, survey_page_size=DEFAULT_SURVEY_PAGE_SIZE,
        pin_page=1, pin_page_size=DEFAULT_PIN_PAGE_SIZE,
    ) -> dict:
        return _present(
            self.store.load(), self._position, body,
            survey_page=survey_page, survey_page_size=survey_page_size,
            pin_page=pin_page, pin_page_size=pin_page_size,
        )

    def geojson(self, body=None) -> dict:
        # Explicit export remains complete (and is still bounded by
        # MAX_PINS_PER_BODY); ordinary polling uses the much smaller page.
        current = _present(
            self.store.load(), self._position, body,
            pin_page_size=MAX_PINS_PER_BODY,
            _pin_page_size_cap=MAX_PINS_PER_BODY,
        ).get("current_map")
        features = []
        for pin in (current or {}).get("pins") or []:
            properties = {
                key: pin.get(key)
                for key in ("id", "kind", "label", "timestamp", "source", "heading", "alt_m")
            }
            properties.update(pin.get("metadata") or {})
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [pin["lon"], pin["lat"]]},
                    "properties": properties,
                }
            )
        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "system": (current or {}).get("system"),
                "body": (current or {}).get("body"),
                "radius_m": (current or {}).get("radius_m"),
            },
        }
