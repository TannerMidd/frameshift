"""Galaxy-wide system-name tripwire riding the EDDN firehose.

The EDDN subscriber already receives every schema on the relay and discards
everything that is not ``commodity/3``.  This module reuses that discarded
stream to answer the one question a local journal can never answer: has
*anyone*, anywhere, reached a system Frameshift is watching?  Elite's oldest
unsolved legend, Raxxla, is the default watch -- if it is ever real and ever
found, the first public evidence crosses this feed.

Three boundaries are deliberate and load-bearing:

* **Only game-assigned system names are read** (``StarSystem``, ``System``,
  ``systemName``).  Station and fleet-carrier names are commander-authored and
  travel in ``stationName``; reading those would let anyone fire this alert by
  renaming a carrier.  Do not add a station or body field here.
* **Nothing touches the database, blocks, or retains an EDDN message.**  This
  runs inside the subscriber's receive loop, so it stays O(1) per message.
* **The latch is per process, not persistent.**  A discovery fires once per
  run.  A durable "already fired" marker would risk never notifying again
  after a restart during the one hour it would ever matter.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

DEFAULT_WATCH = ("Raxxla",)
_MAX_WATCHED = 32
_MAX_NAME_LENGTH = 64
_CACHE_TTL_SECONDS = 60.0
# Game-assigned system-name fields only. See the module docstring before adding.
_SYSTEM_FIELDS = ("StarSystem", "System", "systemName")

_lock = threading.Lock()
_listeners: list[Callable[[dict[str, Any]], None]] = []
_fired: set[str] = set()
_checked = 0
_cached_watch: frozenset[str] = frozenset()
_cached_display: tuple[str, ...] = ()
_cache_expires = 0.0


def watched_names(force: bool = False) -> frozenset[str]:
    """The casefolded watch set, refreshed from settings at most once a minute.

    ``settings._normalise`` only proves a list-valued setting is a bounded
    list, so a hand-edited ``settings.json`` can put anything in the elements.
    They are validated here rather than trusted, because this feeds a loop that
    runs against every message on the relay.
    """
    global _cached_watch, _cached_display, _cache_expires
    now = time.monotonic()
    with _lock:
        if not force and now < _cache_expires:
            return _cached_watch

    raw: Any = list(DEFAULT_WATCH)
    try:
        from . import settings

        value = settings.get("galaxy_watch_systems", list(DEFAULT_WATCH))
        if isinstance(value, list):
            raw = value
    except Exception:
        raw = list(DEFAULT_WATCH)

    display: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not name or len(name) > _MAX_NAME_LENGTH:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        display.append(name)
        if len(display) >= _MAX_WATCHED:
            break

    watch = frozenset(seen)
    with _lock:
        _cached_watch = watch
        _cached_display = tuple(display)
        _cache_expires = now + _CACHE_TTL_SECONDS
    return watch


def observe(envelope: Any) -> str | None:
    """Inspect one decoded EDDN envelope; return the watched name it matched.

    Runs for every message on the relay, so the empty-watch and no-match paths
    stay as short as possible.  Returns ``None`` when nothing matched or when
    this name has already fired during this run.
    """
    global _checked
    watch = watched_names()
    if not watch or not isinstance(envelope, dict):
        return None
    message = envelope.get("message")
    if not isinstance(message, dict):
        return None

    with _lock:
        _checked += 1

    for field in _SYSTEM_FIELDS:
        name = message.get(field)
        if not isinstance(name, str) or not name:
            continue
        key = name.casefold()
        if key not in watch:
            continue
        with _lock:
            if key in _fired:
                return None
            _fired.add(key)
            listeners = tuple(_listeners)
        hit = {
            "system": name[:_MAX_NAME_LENGTH],
            "event": str(message.get("event") or "market update")[:64],
            "schema": str(envelope.get("$schemaRef") or "")[:200],
        }
        for listener in listeners:
            try:
                listener(dict(hit))
            except Exception:
                # One bad sink must not swallow the alert for the others, and
                # must never propagate into the EDDN receive loop.
                continue
        return name
    return None


def subscribe(listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
    """Register a sink for tripwire hits; returns its unsubscribe callable."""
    with _lock:
        _listeners.append(listener)

    def unsubscribe() -> None:
        with _lock:
            if listener in _listeners:
                _listeners.remove(listener)

    return unsubscribe


def stats() -> dict[str, Any]:
    """Diagnostic snapshot for the local health card."""
    watched_names()
    with _lock:
        return {
            "watching": list(_cached_display),
            "fired": sorted(_fired),
            "messages_checked": _checked,
        }


def reset() -> None:
    """Drop listeners, latches, counters and the settings cache."""
    global _checked, _cache_expires
    with _lock:
        _listeners.clear()
        _fired.clear()
        _checked = 0
        _cache_expires = 0.0
