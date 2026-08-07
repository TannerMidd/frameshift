# Frameshift UI architecture modernization — completed plan

**Status:** Final architecture implemented and enforced on 2026-07-18.

The frontend remains a zero-build, native-ES-module application served directly
by Flask and packaged recursively by PyInstaller. The modernization is complete:
the monolith has been replaced by typed domain modules, named API clients,
feature-owned state, safe rendering, layered CSS, and behavior-level tests.

The living contribution contract is
[`UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md). This plan records the delivered
target and its acceptance gates.

## Final decisions

| Area | Final decision |
|---|---|
| Runtime | Native ESM, no bundler, no runtime packages, no CDN assets |
| Types | Strict TypeScript `checkJs` over production JavaScript with JSDoc contracts |
| State | One typed `ApplicationState` store; mutable workspace state stays feature-owned |
| API | Named domain clients with explicit request/response contracts |
| Rendering | Escaping `html\`\`` templates and one reviewed DOM write boundary |
| Markup | Static pane views under `features/views/`; runtime data rendered by features |
| Controls | Idempotent pane wiring under `features/controls/`; bootstrap only composes |
| CSS | Declared cascade layers with one stylesheet entrypoint |
| Tests | Vitest module tests, Chromium journeys, WebKit tablet journeys, isolated Python scripts |
| Release | Repeat all gates before packaging, then verify the served executable’s ESM graph |

Framework adoption, a state library, a transpilation pipeline, an SPA router,
SSR, and a third-party component library are outside the architecture. The
existing imperative DOM model and Holo Bracket component system remain the
right fit for an offline cockpit interface.

## Delivered module graph

```text
ui/
  index.html
  panel-bootstrap.js
  hb.js
  src/
    main.js
    core/
    api/
      contracts/
    data/
    features/
      controls/
      views/
    shell/
    bootstrap/
  styles/
    index.css
    tokens/
    base/
    components/
    features/
    panel/
    themes/
    motion/
```

`reset` is a reserved logical cascade layer, not a source directory.

The enforced import direction is:

```text
core/data <- api <- features/shell <- bootstrap <- main
```

The dependency gate resolves every production import, rejects missing and bare
runtime specifiers, checks layer direction, and fails every cycle.

## Delivered state and commander model

`core/store.js` owns `createStore<ApplicationState>()`. `/api/state` is
published as one complete typed snapshot. Feature renderers read the store
directly or receive an explicitly typed slice.

Commander isolation uses two values:

- `commander_id`, the stable storage and backend discriminator; and
- store generation, which invalidates all work captured before a profile
  change.

Commander-scoped API methods set `scope: "commander"`. The HTTP layer requires a
current identity, sends `X-Frameshift-Commander`, aborts scoped requests during
a handoff, and rejects stale responses. Features verify the captured identity
again before committing workspace state or user-visible errors.

Browser persistence uses stable commander IDs. Display names are never
isolation keys.

Archived-profile selection is an offline viewer, not a second live identity.
The server and journal share one re-entrant transition guard, live
cross-profile activation fails closed, and the next live event restores the
journal commander before dispatch. Every journal-derived analytics or
tracked-market write carries that explicit commander ID.

## Delivered API boundary

Every domain client under `ui/src/api/`, except `index.js`, `query.js`, and
`errors.js`, imports its matching explicit contract. `system.js` imports the
`ApplicationState` contract.

Client methods return named response DTOs. Generic response declarations such
as `Promise<unknown>`, `Promise<Record<string, unknown>>`, or inline Promise
object types fail the architecture gate.

Only `core/http.js` may call `fetch()`. Presentation and bootstrap modules may
not import the transport or embed API paths.

## Delivered safety boundary

Dynamic HTML uses `html\`\`` and `render()`. Interpolated API, journal, EDDN,
extension, localStorage, and form values are escaped. Direct HTML sinks outside
the central renderer fail the architecture gate.

The server enforces a self-only script CSP. UI assets use `no-cache`, API
responses use `no-store`, and `X-Frameshift-Version` coordinates a single
browser reload when an updated server replaces the running module graph.

## Zero-tolerance source gates

Production JavaScript must satisfy all of the following:

- zero `@ts-nocheck`;
- zero explicit JSDoc `any`, including `Record<string, any>`;
- zero `legacy-base.js` or `legacy-runtime.js` files or imports;
- zero generated mechanical-transition comments;
- zero literal mojibake in shipped JavaScript, HTML, or CSS;
- at most 400 lines for every non-data module;
- at most 550 lines for each module under `ui/src/data/`;
- explicit contracts for every domain API client; and
- named, non-generic Promise response DTOs.

The architecture checker has a focused self-test mode:

```text
node tools/check_ui_architecture.mjs --self-test
```

It proves both accepted examples and every zero-tolerance rejection independently
of the current source tree.

## Performance acceptance gate

`tools/check_ui_performance_budget.mjs` warns at the aggregate soft thresholds
and fails at the hard caps:

| Budget | Soft warning | Hard cap |
|---|---:|---:|
| Production modules | 145 | 160 |
| Production JavaScript | 800 KiB | 900 KiB |
| Stylesheets | — | 180 KiB |
| HTML shell | — | 4 KiB |
| Single production module | — | 24 KiB |

These budgets protect startup and recursive ESM fetch cost on LAN tablets and
the desktop WebView. Crossing a soft threshold calls for consolidation or
replacement of equivalent code; crossing a hard cap blocks CI and release.

## Browser acceptance gate

Playwright runs two projects:

- `chromium` executes the complete desktop and shared journey suite.
- `webkit-tablet` executes `@tablet` journeys with an iPad Pro landscape
  profile.

CI installs both browsers on Windows and Linux. The harness fixes locale,
timezone, viewport, scale, and reduced motion; retains traces and failure
screenshots; and applies a bounded screenshot-diff tolerance.

Desktop tabs and Panel pages remain two presentations of the same application
state. Changes to shared functionality cover both when applicable.

## Static and unit acceptance gate

`npm run verify:frontend` runs:

1. entrypoint syntax checks;
2. architecture and dependency checks;
3. the DOM ID contract;
4. CSS layer/import/size checks;
5. performance budgets;
6. formatting and ESLint;
7. strict `checkJs`; and
8. Vitest.

Tests import behavior through the ESM graph. Source-text assertions against
implementation files and `node:vm` bundle execution are prohibited.

## Python test-entrypoint gate

Python test files run in separate processes so their isolated application-data
directories remain deterministic. CI calls:

```text
python tools/run_python_test_file.py tests/test_example.py
```

The wrapper parses the file, records every local `test_*` function, profiles the
script execution, and fails if any discovered test function was not invoked.
This preserves script-style tests without allowing silent pytest-only
definitions.

## Release acceptance gate

The release workflow repeats the complete frontend verification, Chromium and
WebKit journeys, and every isolated Python test before building
`Frameshift.exe`.

After packaging, the workflow starts the executable in headless mode and runs
`tools/verify_served_ui.py`. Publication is blocked unless the verifier can:

- fetch the document and recursively traverse the same-origin ESM graph;
- reject bare or cross-origin module specifiers;
- observe one expected `X-Frameshift-Version` on every response;
- confirm `no-cache` on UI assets; and
- confirm `no-store` on `/api/state`.

After the packaged smoke test succeeds, the tag workflow creates an unsigned
draft candidate with both executable names and convenience checksums. The
release remains unpublished until the owner uploads an offline Ed25519-signed
manifest and the manual publish workflow verifies both executable digests.

## Completion criteria

The modernization is accepted when all of these commands pass:

```text
node tools/check_ui_architecture.mjs --self-test
npm run verify:frontend
npm run test:e2e:ci
```

Release acceptance additionally requires every Python test file through
`tools/run_python_test_file.py` and the packaged served-graph smoke test.

Future frontend work extends this architecture; it does not add alternate
state, transport, rendering, boot, or test paths.
