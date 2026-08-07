import { spawn } from "node:child_process";
import fs from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { expect, test } from "@playwright/test";

const PROJECT_ROOT = process.cwd();
const TEST_ROOT = path.join(PROJECT_ROOT, "tests", "e2e");
const HARNESS = path.join(TEST_ROOT, "real-flask-harness.py");
const READY_PREFIX = "FRAMESHIFT_E2E_READY ";
const TEMP_PREFIX = "frameshift-real-flask-";

function pythonExecutable() {
  const configured = process.env.FRAMESHIFT_E2E_PYTHON;
  if (configured) return configured;
  const candidates = [
    path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe"),
    path.join(PROJECT_ROOT, ".venv", "bin", "python"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || "python";
}

/** @param {Promise<unknown>} promise @param {number} milliseconds */
async function settlesWithin(promise, milliseconds) {
  let timer;
  const timedOut = new Promise((resolve) => {
    timer = setTimeout(() => resolve(false), milliseconds);
  });
  try {
    return await Promise.race([promise.then(() => true), timedOut]);
  } finally {
    clearTimeout(timer);
  }
}

async function startRealFlaskServer() {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), TEMP_PREFIX));
  const dataDir = path.join(tempRoot, "data");
  const child = spawn(pythonExecutable(), [HARNESS], {
    cwd: PROJECT_ROOT,
    env: {
      ...process.env,
      ET_DATA_DIR: dataDir,
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONUNBUFFERED: "1",
    },
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
  });
  child.stdin.on("error", () => {}); // An already-exiting child may close the pipe first.

  let stdout = "";
  let stderr = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    stdout += chunk;
  });
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });

  const exit = new Promise((resolve) => {
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });

  try {
    const ready = await new Promise((resolve, reject) => {
      const timeout = setTimeout(
        () => reject(new Error(`Flask harness did not start.\n${stdout}\n${stderr}`)),
        15_000,
      );
      const inspect = () => {
        const line = stdout.split(/\r?\n/u).find((candidate) => candidate.startsWith(READY_PREFIX));
        if (!line) return;
        clearTimeout(timeout);
        try {
          resolve(JSON.parse(line.slice(READY_PREFIX.length)));
        } catch (error) {
          reject(error);
        }
      };
      child.stdout.on("data", inspect);
      child.once("error", (error) => {
        clearTimeout(timeout);
        reject(error);
      });
      child.once("exit", (code, signal) => {
        clearTimeout(timeout);
        reject(
          new Error(
            `Flask harness exited before readiness (${code ?? signal}).\n${stdout}\n${stderr}`,
          ),
        );
      });
      inspect();
    });

    return {
      baseURL: ready.baseURL,
      async stop() {
        let stoppedGracefully = child.exitCode !== null || child.signalCode !== null;
        try {
          if (!stoppedGracefully) {
            child.stdin.end("stop\n");
            stoppedGracefully = await settlesWithin(exit, 5_000);
          }
          if (!stoppedGracefully) {
            child.kill();
            await settlesWithin(exit, 5_000);
            throw new Error(`Flask harness required forced termination.\n${stdout}\n${stderr}`);
          }
          const result = await exit;
          if (result.code !== 0) {
            throw new Error(
              `Flask harness exited with ${result.code ?? result.signal}.\n${stdout}\n${stderr}`,
            );
          }
        } finally {
          if (!path.basename(tempRoot).startsWith(TEMP_PREFIX)) {
            throw new Error(`Refusing to remove unexpected temporary path: ${tempRoot}`);
          }
          await rm(tempRoot, { recursive: true, force: true });
        }
      },
    };
  } catch (error) {
    if (child.exitCode === null && child.signalCode === null) child.kill();
    await settlesWithin(exit, 5_000);
    if (path.basename(tempRoot).startsWith(TEMP_PREFIX)) {
      await rm(tempRoot, { recursive: true, force: true });
    }
    throw error;
  }
}

let flaskServer;

test.beforeAll(async () => {
  flaskServer = await startRealFlaskServer();
});

test.afterAll(async () => {
  await flaskServer?.stop();
});

// Intentionally lacks @tablet: that project selects only tagged tablet
// journeys, so this process-level integration smoke runs once in Chromium.
test("real Flask serves its API and native-ESM UI @real-flask", async ({ page, request }) => {
  const directState = await request.get(`${flaskServer.baseURL}api/state`);
  expect(directState.status()).toBe(200);
  expect(directState.headers()["x-frameshift-product"]).toBe("Frameshift");
  expect(directState.headers()["x-frameshift-version"]).toMatch(/^\d+\.\d+\.\d+/u);
  expect(directState.headers()["cache-control"]).toContain("no-store");
  await expect(directState.json()).resolves.toMatchObject({
    commander: "Boundary Pilot",
    system: "Sol",
  });

  await page.addInitScript(() => localStorage.setItem("panelMode", "0"));
  const browserState = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/state" && response.request().method() === "GET",
  );
  const navigation = await page.goto(flaskServer.baseURL);
  expect(navigation?.headers()["x-frameshift-product"]).toBe("Frameshift");
  expect(navigation?.headers()["cache-control"]).toContain("no-cache");

  const stateResponse = await browserState;
  expect(stateResponse.status()).toBe(200);
  expect(stateResponse.headers()["x-frameshift-version"]).toBe(
    directState.headers()["x-frameshift-version"],
  );
  await expect(page.locator("#pairing-gate")).toHaveClass(/hidden/u);
  await expect(page.locator("#commander")).toHaveText("CMDR Boundary Pilot");
  await expect(page.locator("#system")).toHaveText("Sol");
  await expect(page.locator("#tab-trade")).toBeVisible();
});
