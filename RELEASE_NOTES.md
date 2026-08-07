## Frameshift v2.11.0 — trust the update, tame the background work

This release is about the machinery that keeps Frameshift dependable while it
runs beside the game: updates now have a real publisher identity, EDDN uploads
cannot grow into an unbounded swarm of threads, simultaneous maintenance
requests no longer race each other, and the browser UI is tested against the
actual Python server rather than fixtures alone.

There is no commander-data migration and no new account, subscription, API key,
or paid service.

### Publisher-authenticated automatic updates

Frameshift now verifies an Ed25519 signature before it offers or installs a new
version. The signed manifest binds the release version, both executable names,
their exact sizes, and their SHA-256 digests. A missing manifest, altered
signature, renamed executable, truncated download, or mismatched digest makes
the updater fail closed.

The private release key stays off GitHub and outside the build workflow. A tag
build creates an unpublished draft; its exact executables are signed with the
owner-held key, and a separate public-key verification workflow publishes the
draft only after both files pass. This gives the in-app updater independent
publisher authentication without a commercial signing service.

This is deliberately not presented as Windows Authenticode. Windows or an
antivirus product may still show a reputation warning for a newly downloaded
executable, but Frameshift itself can now prove that an automatic update came
from the project's release key.

### Bounded EDDN contribution

Anonymous EDDN contribution previously started a fresh thread for every upload.
A burst of journal events could therefore create an unbounded number of threads
while the network was slow.

Uploads now use two reusable workers and a bounded 32-item waiting queue. The
journal never blocks on community-network latency, duplicate observations are
coalesced while pending, saturation is visible in diagnostics, and shutdown
drains accepted work cleanly within a fixed timeout.

### Maintenance requests no longer race

Two simultaneous **Build Database** requests could both pass the old liveness
check and start seed jobs against the same dump and staging database. The updater
had the equivalent check-then-start window around its shared `.new.exe` path.

Both lifecycles now reserve their operation atomically before starting any
worker or network work. A duplicate request receives the existing
already-running response instead of launching competing background jobs.

### Tougher local API boundary

- JSON endpoints consistently reject malformed JSON, `null`, arrays, and scalar
  bodies with a JSON `400` instead of allowing route-specific `500` errors.
- Numeric query parameters reject `NaN`, infinity, and malformed values before
  they reach market or colonisation searches.
- The single-instance probe now requires Frameshift product/version headers and
  a recognizable state payload; an unrelated local HTTP service returning
  `200 OK` is no longer mistaken for an existing Frameshift process.

### A backend that is easier to change safely

Twenty system and administration routes now live outside the main Flask app
factory, and the completed-journal history rebuild is a dedicated collaborator
instead of another 568 lines inside `JournalWatcher`. Automated architecture
budgets prevent those composition points from quietly growing back past their
new boundaries.

The Python side now has pinned Ruff, mypy, and branch-coverage gates alongside
the existing frontend checks. The browser suite also boots the native-ESM UI
through a real temporary Flask server, covering the response headers, state
contract, and visible commander/system boundary end to end.

### Upgrade notes

- Update normally from any 2.x release. No database rebuild or commander-data
  migration is required.
- Versions through v2.10.0 cannot know about the new publisher key, so the move
  to v2.11.0 is the one-time bridge through the previous checksum-only updater.
  Once v2.11.0 is installed, later automatic updates require the Ed25519
  signature and fail closed without it.
- Existing EDDN privacy and opt-in settings are unchanged.
- The release remains fully usable and buildable without paid infrastructure.
