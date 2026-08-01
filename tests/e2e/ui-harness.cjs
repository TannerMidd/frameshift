"use strict";

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const UI_ROOT = path.join(PROJECT_ROOT, "ui");
const FIXTURE_ROOT = path.join(PROJECT_ROOT, "tests", "fixtures", "ui-api");

const MIME_TYPES = Object.freeze({
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
});

function loadJson(name) {
  return JSON.parse(fs.readFileSync(path.join(FIXTURE_ROOT, name), "utf8"));
}

const API_FIXTURES = Object.freeze({
  analytics: loadJson("analytics.json"),
  commoditySearch: loadJson("commodity-search.json"),
  engineering: loadJson("engineering.json"),
  marketDbStatus: loadJson("marketdb-status.json"),
  securityStatus: loadJson("security-status.json"),
  settings: loadJson("settings.json"),
  specialists: loadJson("specialists.json"),
  state: loadJson("state.json"),
});

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function jsonResponse(body, status = 200) {
  return { status, body };
}

function staticFileFor(requestUrl) {
  const url = new URL(requestUrl, "http://127.0.0.1");
  let pathname;
  try {
    pathname = decodeURIComponent(url.pathname);
  } catch (_error) {
    return null;
  }
  if (pathname === "/") pathname = "/index.html";
  const candidate = path.resolve(UI_ROOT, `.${pathname}`);
  const boundary = `${UI_ROOT}${path.sep}`;
  if (candidate !== UI_ROOT && !candidate.startsWith(boundary)) return null;
  return candidate;
}

async function startUiServer() {
  const server = http.createServer((request, response) => {
    if (request.url.startsWith("/api/")) {
      response.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
      response.end(
        JSON.stringify({
          error: "An API request escaped Playwright interception.",
        }),
      );
      return;
    }

    const filename = staticFileFor(request.url);
    if (!filename) {
      response.writeHead(403);
      response.end("Forbidden");
      return;
    }

    fs.stat(filename, (statError, stat) => {
      if (statError || !stat.isFile()) {
        response.writeHead(404);
        response.end("Not found");
        return;
      }
      response.writeHead(200, {
        "Cache-Control": "no-store",
        "Content-Security-Policy":
          "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; " +
          "form-action 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
          "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'",
        "Content-Type":
          MIME_TYPES[path.extname(filename).toLowerCase()] || "application/octet-stream",
        "X-Frameshift-Version": "2.5.0-e2e",
      });
      if (request.method === "HEAD") {
        response.end();
        return;
      }
      fs.createReadStream(filename).pipe(response);
    });
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  return {
    baseURL: `http://127.0.0.1:${address.port}/`,
    close: () =>
      new Promise((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      }),
  };
}

function parsePostData(request) {
  const raw = request.postData();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (_error) {
    return raw;
  }
}

async function installMockApi(page, options = {}) {
  const requests = [];
  const objectives = [];
  const settings = clone(API_FIXTURES.settings);
  let objectiveSequence = 0;
  let stateCalls = 0;
  let paired = options.pairingRequired !== true;

  // Match only the server's root API namespace. A broad **/api/** glob also
  // captures native ESM files under /src/api/, serving fixture JSON as a
  // JavaScript module and preventing the application from booting.
  await page.route(/^https?:\/\/[^/]+\/api\/.*$/u, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const postData = parsePostData(request);
    const record = {
      method,
      path: url.pathname,
      search: url.search,
      postData,
    };
    requests.push(record);

    let response;
    switch (url.pathname) {
      case "/api/security/status":
        response = jsonResponse(
          !paired
            ? {
                local: false,
                authenticated: false,
                pairing_required: true,
                scopes: [],
              }
            : clone(API_FIXTURES.securityStatus),
        );
        break;
      case "/api/security/pair":
        if (method !== "POST" || !postData || typeof postData !== "object") {
          response = jsonResponse({ error: "A pairing request is required." }, 400);
        } else if (options.pairingCode && postData.code !== options.pairingCode) {
          response = jsonResponse({ error: "That pairing link is invalid." }, 400);
        } else {
          paired = true;
          response = jsonResponse({
            ok: true,
            device: { id: "device-e2e", name: postData.device_name },
            scopes: ["read", "control", "admin"],
          });
        }
        break;
      case "/api/security/devices":
        response = jsonResponse({ devices: [] });
        break;
      case "/api/state":
        stateCalls += 1;
        if (
          options.state401 === true ||
          (Number.isInteger(options.state401After) && stateCalls > options.state401After)
        ) {
          response = jsonResponse(
            {
              error: "This test device was revoked.",
              pairing_required: true,
            },
            401,
          );
        } else {
          response = jsonResponse({
            ...clone(API_FIXTURES.state),
            ...(options.statePatch || {}),
          });
        }
        break;
      case "/api/settings":
        if (method === "POST") {
          if (postData && typeof postData === "object") {
            Object.assign(settings.settings, postData);
          }
          response = jsonResponse({ ok: true });
        } else {
          response = jsonResponse(clone(settings));
        }
        break;
      case "/api/journal-dir/validate":
        response = jsonResponse({
          path: "C:\\Users\\Test\\Saved Games\\Frontier Developments\\Elite Dangerous",
          auto: true,
          exists: true,
          files: 3,
          unchecked: false,
        });
        break;
      case "/api/tts/status":
        response = jsonResponse({
          supported: false,
          ready: false,
          downloading: false,
          voice: null,
          voices: [],
        });
        break;
      case "/api/marketdb/status":
        response = jsonResponse(clone(API_FIXTURES.marketDbStatus));
        break;
      case "/api/update/check":
        response = jsonResponse({
          current: "2.5.0",
          latest: "2.5.0",
          available: false,
          supported: false,
        });
        break;
      case "/api/alerts":
        response = jsonResponse({
          commander_id: "cmdr-e2e",
          watches: [],
          alerts: [],
        });
        break;
      case "/api/commodities":
        response = jsonResponse({
          commodities: [{ name: "Gold" }, { name: "Silver" }],
        });
        break;
      case "/api/commodity-search":
        response = jsonResponse(clone(API_FIXTURES.commoditySearch));
        break;
      case "/api/analytics":
        response = jsonResponse(clone(API_FIXTURES.analytics));
        break;
      case "/api/engineering":
        response = jsonResponse(clone(API_FIXTURES.engineering));
        break;
      case "/api/material-traders":
        response = jsonResponse({
          kind: "Raw",
          reference: "Shinrarta Dezhra",
          traders: [
            {
              station: "Jameson Memorial",
              system: "Shinrarta Dezhra",
              distance: 0,
              dist_ls: 325,
              large_pad: true,
            },
          ],
        });
        break;
      case "/api/material-bodies":
        response = jsonResponse({
          material: "Phosphorus",
          reference: "Shinrarta Dezhra",
          min_share: 10,
          bodies: [
            {
              system: "Sol",
              body: "Enceladus",
              subtype: "Icy body",
              share: 11.5,
              distance: 65.2,
              dist_ls: 4900,
            },
          ],
        });
        break;
      case "/api/specialists":
        response = jsonResponse(clone(API_FIXTURES.specialists));
        break;
      case "/api/profiles":
        response = jsonResponse({
          profiles: [
            {
              id: "cmdr-e2e",
              name: "Test Pilot",
              active: true,
              rows: 24,
              galaxy_mode: "live",
              last_seen_at: "2026-07-18T12:00:00Z",
            },
          ],
          unattributed: { rows: 0 },
        });
        break;
      case "/api/extensions":
        response = jsonResponse({ loaded: [], errors: [] });
        break;
      case "/api/diagnostics/health":
        response = jsonResponse({
          version: "2.5.0",
          sqlite_integrity: "ok",
          market_database: { markets: 128 },
        });
        break;
      case "/api/exobio-genera":
        response = jsonResponse({ genera: ["Aleoida", "Bacterium"] });
        break;
      case "/api/objectives":
        if (method === "POST" && postData && typeof postData === "object") {
          const objective = {
            id: `objective-e2e-${++objectiveSequence}`,
            ...postData,
          };
          objectives.push(objective);
          response = jsonResponse({ objective: clone(objective) }, 201);
        } else {
          response = jsonResponse({ objectives: clone(objectives) });
        }
        break;
      case "/api/timings":
        response = jsonResponse({ timings: {} });
        break;
      case "/api/operations":
        response = jsonResponse({ boards: [] });
        break;
      default:
        // All API traffic remains intercepted even when a pane adds a
        // non-critical boot probe. Important contracts above stay explicit.
        response = jsonResponse({ ok: true, commander_id: "cmdr-e2e" });
        break;
    }

    await route.fulfill({
      status: response.status,
      contentType: "application/json",
      headers: { "X-Frameshift-Version": "2.5.0-e2e" },
      body: JSON.stringify(response.body),
    });
  });

  return {
    requests,
    matching(method, pathname) {
      return requests.filter((request) => request.method === method && request.path === pathname);
    },
  };
}

async function seedStorage(page, values = { panelMode: "0" }) {
  await page.addInitScript((entries) => {
    for (const [key, value] of Object.entries(entries)) {
      localStorage.setItem(key, String(value));
    }
  }, values);
}

function capturePageErrors(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.stack || error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`[console] ${message.text()}`);
  });
  return errors;
}

module.exports = {
  capturePageErrors,
  installMockApi,
  seedStorage,
  startUiServer,
};
