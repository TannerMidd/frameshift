# Frameshift UI architecture

Frameshift’s frontend is a zero-build, native-ES-module application. Flask
serves `ui/` directly, and PyInstaller includes the directory recursively. The
browser runtime has no package dependencies, no CDN assets, and no generated
bundle.

This document describes the enforced production architecture. The implementation
record and completed modernization checklist are in
[`UI_ARCHITECTURE_PLAN.md`](UI_ARCHITECTURE_PLAN.md).

## Runtime flow

1. `ui/index.html` loads the synchronous Panel pre-paint guard, layered CSS,
   Holo Bracket interaction helper, and `ui/src/main.js`.
2. `main.js` calls the boot composition. Static shell and pane views mount
   before feature controls and background polling start.
3. `api/system.js` retrieves `/api/state` as the explicit `ApplicationState`
   contract.
4. `core/store.js` publishes each complete snapshot. The composition renderer
   fans that snapshot out to desktop and Panel renderers.
5. Domain features call named API clients and commit asynchronous results only
   while their captured commander identity remains current.

The application remains imperative by design. Module ownership, typed
boundaries, safe rendering, and behavioral tests provide the structure; a
framework or build pipeline is not required.

## Source topology

- `ui/src/core/`: typed store, same-origin HTTP policy, safe templates, DOM
  helpers, clipboard support, form persistence, and formatters.
- `ui/src/api/`: named domain clients.
- `ui/src/api/contracts/`: request, response, and state JSDoc/declaration
  contracts.
- `ui/src/data/`: pure offline data. Data modules may contain no DOM, store, or
  transport work.
- `ui/src/features/`: domain behavior, renderers, and module-private state.
- `ui/src/features/controls/`: idempotent pane-level listener wiring and form
  persistence.
- `ui/src/features/views/`: application-owned static pane templates. Runtime
  values never belong here.
- `ui/src/shell/`: desktop and Panel presentation, cards, tabs, status, voice,
  and shell controls.
- `ui/src/bootstrap/`: declarative initialization and background scheduling.
  Bootstrap calls named initializers; it does not own element IDs or listeners.
- `ui/src/main.js`: the single ESM entrypoint.
- `ui/styles/`: the layered stylesheet graph; `styles/index.css` is its only
  entrypoint.

## Dependency direction

Production imports follow one direction:

```text
core/data <- api <- features/shell <- bootstrap <- main
```

`features/` and `shell/` form one presentation rank and may collaborate while
the overall graph remains acyclic. `main.js` imports only bootstrap
composition. `tools/check_ui_dependencies.mjs` validates every relative import,
layer direction, missing module, bare runtime package, and cycle. An unavoidable
inversion must be one exact documented edge in that checker; broad folder
exceptions are not permitted.

## Production JavaScript policy

All production JavaScript is checked by TypeScript in strict `checkJs` mode.
The architecture gate enforces:

- no `@ts-nocheck`;
- no explicit JSDoc `any`, including `Record<string, any>`;
- no `legacy-base.js`, `legacy-runtime.js`, or imports of either file;
- no generated mechanical-transition comments;
- no literal mojibake in shipped JavaScript, HTML, or CSS;
- at most 400 lines for every non-data module; and
- at most 550 lines for modules under `ui/src/data/`.

These are absolute limits. A module that exceeds a limit is split by cohesive
ownership before it lands.

## Typed application state

`appStore` is instantiated as `createStore<ApplicationState>()`.
`ApplicationState` is defined in `api/contracts/state.js`; features do not
recreate a parallel untyped snapshot.

Use:

- `appStore.getSnapshot()` for the current immutable snapshot;
- `appStore.subscribe()` when a component owns snapshot-driven rendering;
- `appStore.onProfileChange()` for commander-owned reset and reload work;
- `appStore.identity()` before commander-scoped asynchronous work; and
- `appStore.isCurrent(identity)` before committing success, error, cache, or
  control state.

Mutable UI state belongs to the narrowest feature-owned module. Cross-feature
access is through named typed functions, not ambient objects.

## Commander identity and request metadata

`commander_id` is the stable profile discriminator. Display names are labels,
not storage or isolation keys. Profile changes increment the store generation,
which invalidates captured identities even if a later profile uses the same
display name.

Named API methods mark commander-derived work with `scope: "commander"`. The
HTTP client then:

- requires a current commander identity;
- sends `X-Frameshift-Commander` request metadata;
- owns an `AbortController` for the request;
- aborts outstanding scoped work on a profile change; and
- rejects a response whose captured identity is no longer current.

Features still verify `appStore.isCurrent(identity)` before mutating their own
workspace. Browser storage keys use the stable commander ID.

The live journal is the sole authority while Elite Dangerous is running.
Selecting an archived profile is an offline viewing operation: the server
serializes it with journal handoffs, updates the database and `AppState`
together, and rejects a cross-profile selection while the game is live. The
next live journal event reasserts its commander before feature state is
updated. Journal-derived analytics and tracked-market writes always carry the
watcher's explicit commander ID rather than consulting a mutable active-profile
fallback.

## API and HTTP boundary

Every domain client in `ui/src/api/*.js`, except the `index`, `query`, and
`errors` support modules, imports its matching explicit contract module.
`system.js` imports `contracts/state.js`. Public client methods return named
response DTOs; generic Promise responses such as `Promise<unknown>`,
`Promise<Record<string, unknown>>`, or inline object DTOs are prohibited.

Features, shell, and bootstrap modules:

- never call `fetch()` directly;
- never import `core/http.js`; and
- never embed `/api/` endpoint paths.

`core/http.js` is the only transport boundary. It accepts same-origin `/api/`
URLs, uses `cache: "no-store"` and same-origin credentials, normalizes expected
failures as `HttpError`, handles commander scope, preserves download metadata,
and observes `X-Frameshift-Version`.

## Safe DOM boundary

Use `html\`\`` for dynamic markup and `render(element, template)` for the DOM
write. Every interpolation is escaped, including arrays and nested branded
templates.

`raw()` is reserved for reviewed application-owned static fragments. API,
journal, EDDN, localStorage, extension, and form values must never reach it.
Direct `innerHTML`, `outerHTML`, and `insertAdjacentHTML` writes outside the
central renderer are prohibited.

Prefer `textContent`, `replaceChildren()`, typed helpers from `core/dom.js`, and
DOM construction when a template is unnecessary.

## Views, controls, and bootstrap

Static pane markup lives in `features/views/`. Feature renderers own dynamic
content. Pane listeners live in an idempotent initializer under
`features/controls/` or, for shared presentation controls, under `shell/`.

Bootstrap modules compose those named entrypoints. The architecture gate rejects
listener registration and feature DOM queries in `bootstrap/`.

Desktop tabs and Panel pages are two presentations of the same state. A
user-visible change is complete only when both relevant shells remain usable.
`panel-bootstrap.js` stays synchronous and ahead of styles so a saved Panel
preference cannot flash the desktop shell.

## CSS layers

`ui/styles/index.css` declares:

```text
reset -> tokens -> base -> components -> features -> panel -> themes -> motion
```

Import order within a layer is intentional. New rules go in the narrowest
existing sheet. CSS files are limited to 400 lines, and the CSS gate validates
layer declarations, imports, and file sizes. Desktop, Panel, and a non-default
theme require visual review after layer or import-order changes.

## Static, performance, and browser gates

`npm run verify:frontend` runs syntax, architecture, dependency, DOM-contract,
CSS, performance, formatting, lint, strict typecheck, and Vitest gates.

The performance gate enforces:

- no more than 145 production modules;
- no more than 800 KiB of production JavaScript;
- no more than 180 KiB of CSS;
- no more than 4 KiB for `ui/index.html`; and
- no production module larger than 24 KiB.

Vitest owns importable unit and DOM behavior tests. Playwright owns real-browser
journeys:

- Chromium runs the complete journey suite.
- The `webkit-tablet` project runs journeys tagged `@tablet` using an iPad Pro
  landscape profile.
- CI installs Chromium and WebKit on Windows and Linux.
- Reduced motion, fixed locale/time zone, retained failure traces, and
  screenshot tolerances keep visual assertions deterministic.

Python tests continue to run one file per process because application modules
establish isolated data directories at import time. CI invokes each file
through `tools/run_python_test_file.py`; the wrapper discovers every `test_*`
definition and fails if the script entrypoint did not execute one.

## Release and cache contract

The release workflow repeats all frontend gates, Chromium/WebKit journeys, and
isolated Python test entrypoints before building the executable.

The packaged smoke test starts `Frameshift.exe` headlessly and runs
`tools/verify_served_ui.py`. That verifier recursively fetches the served ESM
graph, rejects bare or cross-origin module imports, checks one coherent
`X-Frameshift-Version`, requires `no-cache` for UI assets, and requires
`no-store` for `/api/state`.

Flask serves a self-only script CSP. UI assets revalidate with `no-cache`; APIs
use `no-store`. The browser HTTP client reloads once when the server version
changes, preventing an old module graph from continuing against a newer
backend.

## Adding or changing a pane

1. Add static markup under `features/views/` and expose it through
   `shell/view.js`.
2. Add a focused feature module with explicit local types, renderer functions,
   and module-private state.
3. Add idempotent listener wiring under `features/controls/`.
4. Add endpoint methods to the narrowest domain client and define named request
   and response contracts.
5. Choose commander scope deliberately and guard asynchronous commits with a
   captured store identity.
6. Render runtime values through safe templates or text nodes.
7. Add Vitest behavior coverage and a Playwright journey for the visible
   contract, including `@tablet` coverage when Panel/tablet behavior matters.
8. Run:

   ```text
   npm run verify:frontend
   npm run test:e2e
   ```

9. For release-sensitive changes, run the relevant isolated Python test files
   through `tools/run_python_test_file.py` and verify the packaged served graph.
