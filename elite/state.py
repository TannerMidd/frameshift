"""Thread-safe store for everything the app knows about the game."""

import threading
from collections import deque


class AppState:
    def __init__(self):
        self._lock = threading.Lock()

        # Commander / ship
        self.commander = None
        self.ship_type = None
        self.ship_name = None
        self.ship_ident = None
        self.cargo_capacity = None
        self.max_jump_range = None
        self.fuel_capacity = None

        # Location
        self.system = None
        self.system_address = None
        self.star_pos = None
        self.body = None
        self.docked = False
        self.station = None
        self.station_type = None
        self.station_market_id = None
        self.dist_from_star_ls = None

        # Live status (Status.json)
        self.credits = None
        self.fuel_main = None
        self.fuel_reservoir = None
        self.cargo_tons = None
        self.legal_state = None
        self.destination = None

        # Collections
        self.jump_history = deque(maxlen=20)  # newest first
        self.cargo_inventory = []
        self.market = None  # {"market_id", "station", "system", "timestamp", "items": [...]}

        self.last_journal_event = None  # timestamp string of most recent event seen
        self.journal_dir_found = True

    def update(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def add_jump(self, system, dist, timestamp):
        with self._lock:
            self.jump_history.appendleft(
                {"system": system, "dist": dist, "timestamp": timestamp}
            )

    def snapshot(self):
        with self._lock:
            market = None
            if self.market:
                market = dict(self.market)
                market["is_current_station"] = (
                    self.docked and self.market.get("market_id") == self.station_market_id
                )
            return {
                "commander": self.commander,
                "ship_type": self.ship_type,
                "ship_name": self.ship_name,
                "ship_ident": self.ship_ident,
                "cargo_capacity": self.cargo_capacity,
                "max_jump_range": self.max_jump_range,
                "fuel_capacity": self.fuel_capacity,
                "system": self.system,
                "star_pos": self.star_pos,
                "body": self.body,
                "docked": self.docked,
                "station": self.station,
                "station_type": self.station_type,
                "dist_from_star_ls": self.dist_from_star_ls,
                "credits": self.credits,
                "fuel_main": self.fuel_main,
                "fuel_reservoir": self.fuel_reservoir,
                "cargo_tons": self.cargo_tons,
                "legal_state": self.legal_state,
                "destination": self.destination,
                "jump_history": list(self.jump_history),
                "cargo_inventory": list(self.cargo_inventory),
                "market": market,
                "last_journal_event": self.last_journal_event,
                "journal_dir_found": self.journal_dir_found,
            }
