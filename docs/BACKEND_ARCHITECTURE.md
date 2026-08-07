# Backend Architecture

Frameshift's backend is a local-first Python application. Flask exposes the UI
and LAN API, the journal pipeline owns live commander state, and SQLite stores
durable commander and market projections. The seams below keep the two largest
composition points from absorbing new responsibilities indefinitely.

## Module responsibilities

| Module | Responsibility |
| --- | --- |
| elite/server.py | Constructs the Flask app; owns request-size limits, Host and Origin validation, capability authentication, scope enforcement, commander-request pinning, API input errors, and response security headers. Domain routes that still require close access to the live request snapshot remain here. |
| elite/system_routes.py | Registers process-level administration routes: market database status/seeding, updates, diagnostics, extensions, settings, journal-folder validation, and autoplot control. |
| elite/journal.py | Discovers and tails Elite journal/sidecar files, coordinates live commander transitions, reduces live events into AppState, and owns the watcher thread lifecycle. |
| elite/journal_rebuild.py | Performs chronological completed-journal sweeps and the preserve/rebuild/finalize migration for durable derived projections. |
| elite/state.py | Provides the locked in-memory read model published to API consumers. |
| elite/marketdb.py | Owns SQLite connections, schemas, commander profiles, and durable market/user data primitives. |

## Flask route and security boundary

create_app() remains the only Flask composition root. It installs global
before_request, after_request, and error handlers before registering the system
routes. Those hooks apply to every route regardless of which module contains
its view function.

register_system_routes() receives the security-sensitive seams it needs from
the composition root:

- commander_id reads the commander identity pinned for the current request.
- commander_scoped_when marks the history-backed extension test as
  conditionally commander-scoped.
- json_object_body applies the shared valid-JSON-object contract.
- journal_path applies the guarded journal-folder containment policy.
- error_response keeps unexpected internal details out of API responses.

The registrar does not create a second Flask app, authentication path, or error
policy. URL paths still flow through the default-deny scope policy in
elite/server.py, and Flask endpoint names remain stable.

When adding routes, keep global transport and security policy in
elite/server.py. Put a cohesive process-level route group in a dedicated
registrar and inject the narrow callbacks it needs. Commander domain routes
must retain their commander_scoped metadata and use the request-pinned
snapshot/identity rather than process-global profile state.

## Journal rebuild collaborator

JournalWatcher constructs one JournalHistoryRebuild collaborator and passes the
journal-file ordering, galaxy-mode classifier, and session-chrome event policy
explicitly. The collaborator delegates watcher lifecycle, progress, and
event-reducer seams back to its owner; it does not start another thread or
publish an independent live state.

The split follows the data lifetime:

- JournalWatcher handles current files, sidecars, live tailing, and event
  reduction.
- JournalHistoryRebuild handles completed-file history, replay ordering,
  preserved specialist fragments, durable projection replacement, and
  finalization.

Thin compatibility methods remain on JournalWatcher so existing callers and
tests keep the same API while the expensive rebuild implementation has a clear
owner.

## Architecture growth guard

tools/check_backend_architecture.py uses only ast, pathlib, and other Python
standard-library modules. Named definitions are measured from their AST start
through end_lineno; extracted modules are measured by physical source lines. A
final newline is not counted as an extra line.

The introduction baselines and hard ceilings are:

| Target | Current span/size | Hard ceiling | Headroom |
| --- | ---: | ---: | ---: |
| elite.server.create_app | 1,513 | 1,600 | 87 |
| elite.journal.JournalWatcher | 2,315 | 2,400 | 85 |
| elite/system_routes.py | 309 | 360 | 51 |
| elite/journal_rebuild.py | 637 | 700 | 63 |

Run the checker and its internal negative controls from the repository root:

    python tools/check_backend_architecture.py
    python tools/check_backend_architecture.py --self-test
    python tools/run_python_test_file.py tests/test_backend_architecture.py

These ceilings are anti-regression boundaries, not targets. If a change would
cross one, extract the next cohesive route group, journal reducer, lifecycle
service, or rebuild phase. Raising a ceiling should be a deliberate
architecture decision with an explanation, not the default response to a
failed guard.

The guard has no package dependency, hosted service, subscription, or other
cost.
