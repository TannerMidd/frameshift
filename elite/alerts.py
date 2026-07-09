"""Route watches: the EDDN listener feeds every market update through here,
so the moment another player's visit reveals your active loop has degraded
(price drop, demand/stock drained) you get an alert - before wasting a trip.

Watches persist in the market database and survive restarts. After a price
alert fires, that condition's baseline re-anchors to the observed price, so
continued decay produces a fresh alert per further 10% step instead of
repeating the first one forever."""

import json
import threading
from collections import deque

from . import marketdb
from .errors import UserFacingError

SELL_DROP = 0.90   # alert when a sell price falls below 90% of baseline
BUY_RISE = 1.10    # alert when a buy price rises above 110% of baseline
MAX_ALERTS = 50

_lock = threading.Lock()
_loaded = False
WATCHES = {}   # id -> watch
ALERTS = deque(maxlen=MAX_ALERTS)


def _ensure_loaded():
    """Load persisted watches once (lazily, so import order doesn't matter)."""
    global _loaded
    with _lock:
        if _loaded:
            return
        conn = marketdb.connect()
        try:
            rows = conn.execute("SELECT id, created, payload FROM watches").fetchall()
        finally:
            conn.close()
        for wid, created, payload in rows:
            try:
                w = json.loads(payload)
            except json.JSONDecodeError:
                continue
            w["id"] = wid
            w["created"] = created
            w["market_ids"] = set(w.get("market_ids") or [])
            w["conditions"] = [tuple(c) for c in w.get("conditions") or []]
            WATCHES[wid] = w
        _loaded = True


def _payload(watch):
    return json.dumps({
        "label": watch["label"],
        "market_ids": sorted(watch["market_ids"]),
        "conditions": [list(c) for c in watch["conditions"]],
        "profit": watch.get("profit"),
    })


def watched_market_ids():
    """Set of market ids any watch cares about (for price-history recording)."""
    _ensure_loaded()
    with _lock:
        out = set()
        for w in WATCHES.values():
            out |= w["market_ids"]
        return out


def add_loop_watch(loop):
    """loop: the JSON the UI got from /api/trade-route (one loop entry)."""
    _ensure_loaded()
    a, b = loop.get("a") or {}, loop.get("b") or {}
    if not a.get("market_id") or not b.get("market_id"):
        raise UserFacingError("Loop has no market ids - re-run the route search first.")
    label = f"{a.get('station')} ⇄ {b.get('station')}"
    conditions = []  # (market_id, symbol, side, units, baseline_price, station)

    def leg(src, dst, commodities):
        for c in commodities or []:
            sym, units = c.get("symbol"), c.get("amount") or 0
            if not sym:
                continue
            conditions.append((src["market_id"], sym, "buy", units, c.get("buy_price") or 0, src.get("station")))
            conditions.append((dst["market_id"], sym, "sell", units, c.get("sell_price") or 0, dst.get("station")))

    leg(a, b, (loop.get("outbound") or {}).get("commodities"))
    leg(b, a, (loop.get("inbound") or {}).get("commodities"))
    if not conditions:
        raise UserFacingError("Loop has no commodities to watch.")
    watch = {
        "label": label,
        "market_ids": {a["market_id"], b["market_id"]},
        "conditions": conditions,
        "created": marketdb.utc_now_iso(),
        "profit": loop.get("profit"),
    }
    conn = marketdb.connect()
    try:
        cur = conn.execute(
            "INSERT INTO watches(created, payload) VALUES(?, ?)",
            (watch["created"], _payload(watch)),
        )
        conn.commit()
        watch["id"] = cur.lastrowid
    finally:
        conn.close()
    with _lock:
        WATCHES[watch["id"]] = watch
        return watch


def remove_watch(wid):
    _ensure_loaded()
    wid = int(wid)
    conn = marketdb.connect()
    try:
        conn.execute("DELETE FROM watches WHERE id = ?", (wid,))
        conn.commit()
    finally:
        conn.close()
    with _lock:
        return WATCHES.pop(wid, None) is not None


def snapshot():
    _ensure_loaded()
    with _lock:
        return {
            "watches": [
                {"id": w["id"], "label": w["label"], "created": w["created"], "profit": w["profit"]}
                for w in WATCHES.values()
            ],
            "alerts": list(ALERTS),
        }


def clear_alerts():
    with _lock:
        ALERTS.clear()


def on_market_update(market_id, station_name, rows):
    """Called by the EDDN listener. rows: (symbol, buy, sell, supply, demand)."""
    _ensure_loaded()
    with _lock:
        interested = [w for w in WATCHES.values() if market_id in w["market_ids"]]
    if not interested:
        return
    by_symbol = {r[0]: r for r in rows}
    for watch in interested:
        rebase = {}  # condition index -> new baseline
        for i, (mid, sym, side, units, base_price, station) in enumerate(watch["conditions"]):
            if mid != market_id:
                continue
            row = by_symbol.get(sym)
            name = sym.title()
            if row is None:
                _alert(watch, f"{name} vanished from {station}'s market ({watch['label']})")
                continue
            _, buy, sell, supply, demand = row
            if side == "sell":
                if base_price and sell < base_price * SELL_DROP:
                    _alert(watch, f"{name} sell price at {station} dropped {base_price:,} → {sell:,} cr ({watch['label']})")
                    rebase[i] = sell
                if units and demand < units:
                    _alert(watch, f"{name} demand at {station} down to {demand:,} — below your {units} t load ({watch['label']})")
            else:
                if base_price and buy > base_price * BUY_RISE:
                    _alert(watch, f"{name} buy price at {station} rose {base_price:,} → {buy:,} cr ({watch['label']})")
                    rebase[i] = buy
                if units and supply < units:
                    _alert(watch, f"{name} stock at {station} down to {supply:,} — below your {units} t load ({watch['label']})")
        if rebase:
            _rebaseline(watch, rebase)


def _rebaseline(watch, rebase):
    """Anchor alerted price conditions to the price that fired them, so the
    next alert means *further* movement, not the same drop repeated."""
    with _lock:
        conds = list(watch["conditions"])
        for i, price in rebase.items():
            mid, sym, side, units, _old, station = conds[i]
            conds[i] = (mid, sym, side, units, price, station)
        watch["conditions"] = conds
        payload = _payload(watch)
    conn = marketdb.connect()
    try:
        conn.execute("UPDATE watches SET payload = ? WHERE id = ?", (payload, watch["id"]))
        conn.commit()
    finally:
        conn.close()


def _alert(watch, text):
    with _lock:
        for existing in ALERTS:
            if existing["text"] == text:
                return  # de-duplicate repeats
        ALERTS.appendleft({"ts": marketdb.utc_now_iso(), "watch_id": watch["id"], "text": text})
