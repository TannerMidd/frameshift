"""Live EDDN subscriber: keeps local market prices fresh in near-real-time.
Subscribes to the community relay and applies commodity/3 messages for any
station whose system we know (seeded from the Spansh dump)."""

import json
import threading
import zlib

from . import galaxywatch, marketdb

RELAY = "tcp://eddn.edcd.io:9500"
COMMODITY_SCHEMA = "https://eddn.edcd.io/schemas/commodity/3"
RECV_TIMEOUT_MS = 60_000
RECONNECT_DELAY = 10
SHUTDOWN_POLL_MS = 250


class EddnListener:
    def __init__(self, include_carriers=False):
        self.include_carriers = include_carriers
        self._lock = threading.Lock()
        self.connected = False
        self.last_message_at = None
        self.markets_updated = 0
        self.skipped = 0
        self.skipped_legacy = 0
        # DB-swap coordination (see marketdb.swap_in): while _pause is set the
        # loop keeps no database connection open; _db_released confirms it.
        self._pause = threading.Event()
        self._db_released = threading.Event()
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread = None

    def pause_db(self, timeout=20):
        """Ask the listener to close its long-lived DB connection and hold off
        until resume_db(). Returns once it has (or after `timeout` if the
        listener is wedged in a long recv — swap_in's retries cover that)."""
        self._pause.set()
        if not self.running():
            self._db_released.set()
            return True
        return self._db_released.wait(timeout)

    def resume_db(self):
        self._db_released.clear()
        self._pause.clear()

    def stats(self):
        with self._lock:
            return {
                "connected": self.connected,
                "last_message_at": self.last_message_at,
                "markets_updated": self.markets_updated,
                "skipped_unknown": self.skipped,
                "skipped_legacy": self.skipped_legacy,
            }

    def _carriers_wanted(self):
        """The 'Exclude fleet carriers' setting controls ingestion too, so
        unticking it genuinely starts collecting carrier markets from the live
        feed instead of only un-hiding data that was never stored."""
        if self.include_carriers:
            return True
        try:
            from . import settings

            return not settings.get("exclude_carriers", True)
        except Exception:
            return False

    @staticmethod
    def _is_legacy(header):
        """True for messages from the Legacy (3.8) galaxy — its prices belong
        to a different universe and would poison the Live database. Messages
        without a gameversion (old uploaders) are kept: post-split tools all
        stamp it, and the handful that don't are overwhelmingly Live."""
        gv = str((header or {}).get("gameversion") or "").strip()
        if not gv:
            return False
        if gv.startswith("CAPI-"):
            return "Live" not in gv
        digits = gv.split(".", 1)[0]
        return digits.isdigit() and int(digits) < 4

    def start(self):
        """Start the subscriber once and return its worker thread."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return self._thread
            self._stop.clear()
            self._db_released.clear()
            self._thread = threading.Thread(
                target=self._run_forever, name="eddn-listener", daemon=True
            )
            self._thread.start()
            return self._thread

    def running(self):
        with self._lifecycle_lock:
            return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout=5):
        """Stop the worker and release its ZeroMQ and SQLite resources."""
        self._stop.set()
        # A database swap may have parked the listener in the pause loop.
        self._pause.clear()
        with self._lifecycle_lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0, timeout))
        stopped = thread is None or not thread.is_alive()
        if stopped:
            with self._lock:
                self.connected = False
        return stopped

    # ---------- internals ----------

    def _run_forever(self):
        import zmq

        # The subscriber owns its context. A process-global Context.instance()
        # can deadlock interpreter finalisation while this daemon still owns a
        # socket and a long-lived SQLite connection.
        context = zmq.Context()
        try:
            while not self._stop.is_set():
                socket = context.socket(zmq.SUB)
                socket.setsockopt(zmq.SUBSCRIBE, b"")
                socket.setsockopt(
                    zmq.RCVTIMEO, min(RECV_TIMEOUT_MS, SHUTDOWN_POLL_MS)
                )
                try:
                    socket.connect(RELAY)
                    with self._lock:
                        self.connected = True
                    if self._pause.is_set():
                        # No connection exists yet, which already satisfies a
                        # database-swap request. Do not make pause_db() wait for
                        # its full timeout just because startup hit this edge.
                        self._db_released.set()
                        while self._pause.is_set() and not self._stop.wait(0.1):
                            pass  # don't open the database mid-swap
                    if self._stop.is_set():
                        break
                    self._db_released.clear()
                    conn = marketdb.connect()
                    try:
                        while not self._stop.is_set():
                            if self._pause.is_set():
                                conn.close()
                                self._db_released.set()
                                while self._pause.is_set() and not self._stop.wait(0.1):
                                    pass
                                if self._stop.is_set():
                                    break
                                self._db_released.clear()
                                conn = marketdb.connect()
                            try:
                                raw = socket.recv()
                            except zmq.Again:
                                continue
                            self._handle(conn, raw)
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        if self._pause.is_set() or self._stop.is_set():
                            self._db_released.set()
                except Exception:
                    if self._stop.is_set():
                        break
                    # Network drop or relay restart: reconnect below.
                    pass
                finally:
                    with self._lock:
                        self.connected = False
                    socket.close(linger=0)
                self._stop.wait(RECONNECT_DELAY)
        finally:
            self._db_released.set()
            # Every socket was closed above by this same worker, so termination
            # cannot wait on a socket owned by another thread.
            context.term()

    def _handle(self, conn, raw):
        try:
            envelope = json.loads(zlib.decompress(raw))
        except (zlib.error, json.JSONDecodeError):
            return
        with self._lock:
            self.last_message_at = marketdb.utc_now_iso()
        # Every schema on the relay passes through here and all but commodity/3
        # is dropped below, so the galaxy-wide name tripwire reads the message
        # while it is already decoded. Legacy-galaxy traffic is a frozen fork of
        # the universe and is excluded, matching how its prices are handled.
        try:
            if not self._is_legacy(envelope.get("header")):
                galaxywatch.observe(envelope)
        except Exception:
            pass  # the tripwire must never break market ingestion
        if envelope.get("$schemaRef") != COMMODITY_SCHEMA:
            return
        if self._is_legacy(envelope.get("header")):
            with self._lock:
                self.skipped_legacy += 1
            return
        msg = envelope.get("message") or {}
        market_id = msg.get("marketId")
        system_name = msg.get("systemName")
        station_name = msg.get("stationName")
        commodities = msg.get("commodities") or []
        if not market_id or not system_name or not commodities:
            return
        if not self._carriers_wanted() and marketdb.is_carrier(None, station_name):
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
            # Price history only for markets the player cares about (docked-at
            # or watched) — recording the whole galaxy would grow unbounded.
            try:
                from . import alerts

                if market_id in marketdb.tracked_ids() or market_id in alerts.watched_market_ids():
                    marketdb.record_price_history(conn, market_id, rows, updated)
            except Exception:
                pass  # history is a nicety; ingestion must not break
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
