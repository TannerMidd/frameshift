"""Live EDDN subscriber: keeps local market prices fresh in near-real-time.
Subscribes to the community relay and applies commodity/3 messages for any
station whose system we know (seeded from the Spansh dump)."""

import json
import threading
import time
import zlib

from . import marketdb

RELAY = "tcp://eddn.edcd.io:9500"
COMMODITY_SCHEMA = "https://eddn.edcd.io/schemas/commodity/3"
RECV_TIMEOUT_MS = 60_000
RECONNECT_DELAY = 10


class EddnListener:
    def __init__(self, include_carriers=False):
        self.include_carriers = include_carriers
        self._lock = threading.Lock()
        self.connected = False
        self.last_message_at = None
        self.markets_updated = 0
        self.skipped = 0

    def stats(self):
        with self._lock:
            return {
                "connected": self.connected,
                "last_message_at": self.last_message_at,
                "markets_updated": self.markets_updated,
                "skipped_unknown": self.skipped,
            }

    def start(self):
        thread = threading.Thread(target=self._run_forever, name="eddn-listener", daemon=True)
        thread.start()
        return thread

    # ---------- internals ----------

    def _run_forever(self):
        import zmq

        context = zmq.Context.instance()
        while True:
            socket = context.socket(zmq.SUB)
            socket.setsockopt(zmq.SUBSCRIBE, b"")
            socket.setsockopt(zmq.RCVTIMEO, RECV_TIMEOUT_MS)
            try:
                socket.connect(RELAY)
                with self._lock:
                    self.connected = True
                conn = marketdb.connect()
                try:
                    while True:
                        raw = socket.recv()  # raises zmq.Again on timeout
                        self._handle(conn, raw)
                finally:
                    conn.close()
            except Exception:
                pass  # timeout, network drop, relay restart - just reconnect
            finally:
                with self._lock:
                    self.connected = False
                socket.close(linger=0)
            time.sleep(RECONNECT_DELAY)

    def _handle(self, conn, raw):
        try:
            envelope = json.loads(zlib.decompress(raw))
        except (zlib.error, json.JSONDecodeError):
            return
        with self._lock:
            self.last_message_at = marketdb.utc_now_iso()
        if envelope.get("$schemaRef") != COMMODITY_SCHEMA:
            return
        msg = envelope.get("message") or {}
        market_id = msg.get("marketId")
        system_name = msg.get("systemName")
        station_name = msg.get("stationName")
        commodities = msg.get("commodities") or []
        if not market_id or not system_name or not commodities:
            return
        if not self.include_carriers and marketdb.is_carrier(None, station_name):
            return

        rows = []
        for c in commodities:
            symbol = (c.get("name") or "").lower()
            buy, sell = c.get("buyPrice") or 0, c.get("sellPrice") or 0
            supply, demand = c.get("stock") or 0, c.get("demand") or 0
            if symbol and marketdb.keep_commodity(buy, sell, supply, demand):
                rows.append((symbol, buy, sell, supply, demand))
        if not rows:
            return
        updated = marketdb.parse_update_time(msg.get("timestamp")) or marketdb.now_epoch()

        try:
            known = conn.execute(
                "SELECT system_id64 FROM stations WHERE market_id = ?", (market_id,)
            ).fetchone()
            if known:
                conn.execute(
                    "UPDATE stations SET updated_at = ? WHERE market_id = ?", (updated, market_id)
                )
            else:
                system = marketdb.find_system(conn, system_name)
                if not system:
                    with self._lock:
                        self.skipped += 1
                    return
                # New station discovered live; pad size unknown until a dump re-seed.
                conn.execute(
                    "INSERT OR REPLACE INTO stations"
                    "(market_id, system_id64, name, type, dist_ls, large_pad, updated_at)"
                    " VALUES(?, ?, ?, NULL, NULL, 0, ?)",
                    (market_id, system[0], station_name or "?", updated),
                )
            marketdb.replace_market(conn, market_id, rows)
            conn.commit()
            with self._lock:
                self.markets_updated += 1
        except Exception:
            conn.rollback()
            return
        try:
            from . import alerts

            alerts.on_market_update(market_id, station_name, rows)
        except Exception:
            pass  # alerting must never break ingestion


LISTENER = EddnListener()
