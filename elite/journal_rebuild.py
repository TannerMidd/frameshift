"""Chronological journal history and derived-projection reconstruction.

This collaborator owns the expensive completed-journal sweep and the durable
preserve/rebuild/finalize migration. JournalWatcher retains thin compatibility
methods and supplies its lifecycle, progress, and reducer extension points.
"""

import hashlib
import json
import logging

from . import marketdb


# Preserve the logger identity used before this code moved out of journal.py.
log = logging.getLogger("elite.journal")


class JournalHistoryRebuild:
    def __init__(
        self, watcher, *, journal_files, galaxy_mode, session_chrome_events
    ):
        self._watcher = watcher
        self._journal_files = journal_files
        self._galaxy_mode = galaxy_mode
        self._session_chrome_events = session_chrome_events

    def __getattr__(self, name):
        """Delegate lifecycle, constants, and reducer seams to the watcher."""
        return getattr(self._watcher, name)

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
        replay_phase = self._watcher._prepare_derived_history_replay()
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
        files = self._journal_files(self.journal_dir)
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
                        galaxy_mode = self._galaxy_mode(event.get("gameversion"))
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
                event.get("event") in self._session_chrome_events for _, event in parsed
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
                    fragment = self._watcher._preservable_specialist_fragment(
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
                        else self._watcher._specialist_default_state(workflow)
                    )
                merged = self._watcher._merge_preserved_specialist_state(
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
