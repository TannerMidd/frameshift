"""Publishes the market you just visited back to EDDN (the feed this app
consumes). Same message format EDMC sends; the gateway anonymises uploads.

Disable with ET_EDDN_UPLOAD=0."""

import gzip
import json
import threading

import requests

from . import marketdb

try:
    from ._version import VERSION as SOFTWARE_VERSION
except Exception:
    SOFTWARE_VERSION = "0.0.0"

UPLOAD_URL = "https://eddn.edcd.io:4430/upload/"
SCHEMA = "https://eddn.edcd.io/schemas/commodity/3"
SOFTWARE_NAME = "EliteTrader"
MAX_AGE_S = 120  # never upload stale snapshots (e.g. bootstrap replays)

SKIP_CATEGORIES = {"nonmarketable"}


def enabled():
    from . import settings

    return bool(settings.get("eddn_upload", True))


def _symbol(raw):
    return (raw or "").strip("$;").removesuffix("_name").lower()


class EddnUploader:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_key = None
        self.uploads = 0
        self.last_upload_at = None
        self.last_error = None

    def stats(self):
        with self._lock:
            return {
                "enabled": enabled(),
                "uploads": self.uploads,
                "last_upload_at": self.last_upload_at,
                "last_error": self.last_error,
            }

    def maybe_publish(self, market, commander):
        """Called by the journal watcher whenever Market.json changes."""
        if not enabled():
            return
        market_id = market.get("MarketID")
        timestamp = market.get("timestamp")
        if not market_id or not timestamp or not market.get("Items"):
            return
        updated = marketdb.parse_update_time(timestamp)
        if not updated or marketdb.now_epoch() - updated > MAX_AGE_S:
            return  # old snapshot (app restart, journal replay)
        key = (market_id, timestamp)
        with self._lock:
            if key == self._last_key:
                return
            self._last_key = key
        threading.Thread(
            target=self._publish, args=(market, commander), name="eddn-upload", daemon=True
        ).start()

    def _publish(self, market, commander):
        commodities = []
        for item in market.get("Items") or []:
            category = _symbol(item.get("Category"))
            if category in SKIP_CATEGORIES or item.get("Legality"):
                continue
            name = _symbol(item.get("Name"))
            if not name:
                continue
            commodities.append({
                "name": name,
                "meanPrice": item.get("MeanPrice", 0),
                "buyPrice": item.get("BuyPrice", 0),
                "stock": item.get("Stock", 0),
                "stockBracket": item.get("StockBracket", 0),
                "sellPrice": item.get("SellPrice", 0),
                "demand": item.get("Demand", 0),
                "demandBracket": item.get("DemandBracket", 0),
            })
        if not commodities:
            return
        envelope = {
            "$schemaRef": SCHEMA,
            "header": {
                "uploaderID": commander or "unknown",
                "softwareName": SOFTWARE_NAME,
                "softwareVersion": SOFTWARE_VERSION,
            },
            "message": {
                "systemName": market.get("StarSystem"),
                "stationName": market.get("StationName"),
                "marketId": market.get("MarketID"),
                "timestamp": market.get("timestamp"),
                "commodities": commodities,
            },
        }
        try:
            body = gzip.compress(json.dumps(envelope).encode("utf-8"))
            resp = requests.post(
                UPLOAD_URL, data=body,
                headers={"Content-Type": "application/json; charset=utf-8",
                         "Content-Encoding": "gzip"},
                timeout=20,
            )
            with self._lock:
                if resp.status_code == 200:
                    self.uploads += 1
                    self.last_upload_at = marketdb.utc_now_iso()
                    self.last_error = None
                else:
                    self.last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
        except requests.RequestException as exc:
            with self._lock:
                self.last_error = str(exc)[:200]


UPLOADER = EddnUploader()
