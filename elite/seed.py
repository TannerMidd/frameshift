"""Downloads the Spansh galaxy_populated dump and imports station markets into
the local SQLite database. Runs in a background thread; progress is polled via
Seeder.progress() -> /api/marketdb/status."""

import gzip
import json
import threading

import requests

from . import marketdb

DUMP_URL = "https://downloads.spansh.co.uk/galaxy_populated.json.gz"
DUMP_PATH = marketdb.DATA_DIR / "galaxy_populated.json.gz"
HEADERS = {"User-Agent": "EliteTrader/1.0 (personal ED companion app)"}
COMMIT_EVERY = 500  # systems per transaction


class Seeder:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self.reset()

    def reset(self):
        self.phase = "idle"  # idle | downloading | importing | done | error
        self.error = None
        self.downloaded = 0
        self.total_bytes = 0
        self.systems_done = 0
        self.stations_done = 0
        self.started_at = None
        self.finished_at = None

    def progress(self):
        with self._lock:
            return {
                "phase": self.phase,
                "error": self.error,
                "downloaded_mb": round(self.downloaded / 1e6),
                "total_mb": round(self.total_bytes / 1e6),
                "systems_done": self.systems_done,
                "stations_done": self.stations_done,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, include_carriers=False, keep_dump=False):
        if self.running():
            return False
        self.reset()
        self._thread = threading.Thread(
            target=self._run, args=(include_carriers, keep_dump), name="market-seeder", daemon=True
        )
        self._thread.start()
        return True

    # ---------- internals ----------

    def _set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _run(self, include_carriers, keep_dump):
        self._set(started_at=marketdb.utc_now_iso())
        try:
            self._download()
            self._import(include_carriers)
            if not keep_dump:
                DUMP_PATH.unlink(missing_ok=True)
            self._set(phase="done", finished_at=marketdb.utc_now_iso())
        except Exception as exc:  # surfaced to the UI, not raised into the void
            self._set(phase="error", error=f"{type(exc).__name__}: {exc}")

    def _download(self):
        marketdb.DATA_DIR.mkdir(parents=True, exist_ok=True)
        head = requests.head(DUMP_URL, headers=HEADERS, timeout=30)
        head.raise_for_status()
        total = int(head.headers.get("Content-Length", 0))
        self._set(phase="downloading", total_bytes=total)

        if DUMP_PATH.exists() and total and DUMP_PATH.stat().st_size == total:
            self._set(downloaded=total)  # already fully downloaded earlier
            return

        part = DUMP_PATH.with_suffix(".gz.part")
        with requests.get(DUMP_URL, headers=HEADERS, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            done = 0
            with open(part, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    self._set(downloaded=done)
        part.replace(DUMP_PATH)

    def _import(self, include_carriers):
        self._set(phase="importing")
        conn = marketdb.connect()
        try:
            cur = conn.cursor()
            # Full rebuild: a re-seed replaces everything.
            cur.execute("DELETE FROM commodities")
            cur.execute("DELETE FROM stations")
            cur.execute("DELETE FROM systems")
            conn.commit()

            names_seen = set()
            in_batch = 0
            with gzip.open(DUMP_PATH, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip().rstrip(",")
                    if not line or line in ("[", "]"):
                        continue
                    try:
                        system = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._import_system(cur, system, include_carriers, names_seen)
                    in_batch += 1
                    if in_batch >= COMMIT_EVERY:
                        conn.commit()
                        in_batch = 0
            marketdb.set_meta(cur, "seeded_at", marketdb.utc_now_iso())
            marketdb.set_meta(cur, "seed_source", DUMP_URL)
            conn.commit()
        finally:
            conn.close()

    def _import_system(self, cur, system, include_carriers, names_seen):
        coords = system.get("coords") or {}
        id64, name = system.get("id64"), system.get("name")
        if id64 is None or not name or not coords:
            return

        stations = list(system.get("stations") or [])
        for body in system.get("bodies") or []:
            stations.extend(body.get("stations") or [])

        station_rows = []
        for st in stations:
            market = st.get("market") or {}
            commodities = market.get("commodities") or []
            market_id = st.get("id")
            if not commodities or market_id is None:
                continue
            if not include_carriers and marketdb.is_carrier(st.get("type"), st.get("name")):
                continue
            updated = marketdb.parse_update_time(market.get("updateTime") or st.get("updateTime"))
            if updated is None:
                continue
            pads = st.get("landingPads") or {}
            rows = []
            for c in commodities:
                buy, sell = c.get("buyPrice") or 0, c.get("sellPrice") or 0
                supply, demand = c.get("supply") or 0, c.get("demand") or 0
                symbol = (c.get("symbol") or c.get("name") or "").lower()
                if not symbol or not marketdb.keep_commodity(buy, sell, supply, demand):
                    continue
                rows.append((symbol, buy, sell, supply, demand))
                if symbol not in names_seen:
                    names_seen.add(symbol)
                    cur.execute(
                        "INSERT OR IGNORE INTO commodity_names(symbol, name, category) VALUES(?, ?, ?)",
                        (symbol, c.get("name") or symbol.title(), c.get("category")),
                    )
            if not rows:
                continue
            station_rows.append(
                (market_id, id64, st.get("name"), st.get("type"),
                 st.get("distanceToArrival"), 1 if (pads.get("large") or 0) > 0 else 0, updated)
            )
            marketdb.replace_market(cur, market_id, rows)

        if not station_rows:
            return
        cur.execute(
            "INSERT OR REPLACE INTO systems(id64, name, x, y, z) VALUES(?, ?, ?, ?, ?)",
            (id64, name, coords.get("x"), coords.get("y"), coords.get("z")),
        )
        cur.executemany(
            "INSERT OR REPLACE INTO stations(market_id, system_id64, name, type, dist_ls, large_pad, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?)",
            station_rows,
        )
        with self._lock:
            self.systems_done += 1
            self.stations_done += len(station_rows)


SEEDER = Seeder()
