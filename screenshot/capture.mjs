#!/usr/bin/env node
// Capture screenshots of a running local web app.
//
// Drives an already-installed Edge or Chrome in headless mode over the
// DevTools protocol. Deliberately has no npm dependencies: Node 18 and later
// provide fetch, Node 22 and later provide WebSocket.
//
// Usage:  node capture.mjs <config.json>
//
// The config file is documented in README.md next to this script.

import { spawn } from "node:child_process";
import { mkdtemp, rm, mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

const BROWSER_CANDIDATES = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "/usr/bin/microsoft-edge",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
];

function fail(message) {
  console.error(`ERROR: ${message}`);
  process.exit(1);
}

function findBrowser(configured) {
  if (configured) {
    if (!existsSync(configured)) fail(`browser not found at ${configured}`);
    return configured;
  }
  const found = BROWSER_CANDIDATES.find((p) => existsSync(p));
  if (!found) {
    fail(
      "no Edge or Chrome found. Install one, or set \"browser\" in the config " +
        "to its full path."
    );
  }
  return found;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Reads the port the browser actually chose, from the file it writes into its
// own profile folder. Asking for a specific port instead would risk landing on
// one another browser already holds, in which case this script would silently
// attach to that browser and photograph one of its real tabs.
async function waitForDebugPort(profileDir, timeoutMs) {
  const portFile = join(profileDir, "DevToolsActivePort");
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const [first] = (await readFile(portFile, "utf-8")).split("\n");
      const port = Number(first.trim());
      if (Number.isInteger(port) && port > 0) return port;
    } catch {
      // browser has not written the file yet
    }
    await sleep(100);
  }
  fail(`browser did not open a debug port within ${timeoutMs}ms`);
}

async function waitForEndpoint(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (resp.ok) return await resp.json();
    } catch {
      // browser is not listening yet
    }
    await sleep(100);
  }
  fail(`browser did not answer on debug port ${port} within ${timeoutMs}ms`);
}

async function findPageTarget(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const resp = await fetch(`http://127.0.0.1:${port}/json/list`);
    const targets = await resp.json();
    const page = targets.find(
      (t) => t.type === "page" && t.webSocketDebuggerUrl
    );
    if (page) return page.webSocketDebuggerUrl;
    await sleep(100);
  }
  fail("browser never opened a page to attach to");
}

// Minimal DevTools protocol client over the built-in WebSocket.
class Devtools {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    this.eventWaiters = [];
    ws.addEventListener("message", (ev) => this.onMessage(ev.data));
  }

  static connect(url) {
    return new Promise((resolvePromise, rejectPromise) => {
      const ws = new WebSocket(url);
      ws.addEventListener("open", () => resolvePromise(new Devtools(ws)));
      ws.addEventListener("error", () =>
        rejectPromise(new Error(`could not connect to ${url}`))
      );
    });
  }

  onMessage(raw) {
    const msg = JSON.parse(raw);
    if (msg.id && this.pending.has(msg.id)) {
      const { resolve: ok, reject: no } = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (msg.error) no(new Error(msg.error.message));
      else ok(msg.result);
      return;
    }
    if (msg.method) {
      this.eventWaiters = this.eventWaiters.filter((w) => {
        if (w.method !== msg.method) return true;
        w.resolve(msg.params);
        return false;
      });
    }
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((ok, no) => {
      this.pending.set(id, { resolve: ok, reject: no });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  waitForEvent(method, timeoutMs) {
    return new Promise((ok, no) => {
      const waiter = { method, resolve: ok };
      this.eventWaiters.push(waiter);
      setTimeout(() => {
        this.eventWaiters = this.eventWaiters.filter((w) => w !== waiter);
        no(new Error(`timed out waiting for ${method}`));
      }, timeoutMs);
    });
  }

  // Runs an expression in the page and returns its value. Throws when the
  // page itself throws, so a broken selector is loud rather than silent.
  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      const text =
        result.exceptionDetails.exception?.description ||
        result.exceptionDetails.text;
      throw new Error(`page threw while running: ${expression}\n${text}`);
    }
    return result.result?.value;
  }

  close() {
    try {
      this.ws.close();
    } catch {
      // already gone
    }
  }
}

async function pollUntilTrue(devtools, expression, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await devtools.evaluate(`!!(${expression})`)) return;
    await sleep(200);
  }
  fail(`${label} never became true within ${timeoutMs}ms: ${expression}`);
}

async function main() {
  const configPath = process.argv[2];
  if (!configPath) fail("usage: node capture.mjs <config.json>");

  const configDir = dirname(resolve(configPath));
  const config = JSON.parse(await readFile(configPath, "utf-8"));

  if (!config.url) fail("config needs a \"url\"");
  if (!Array.isArray(config.shots) || config.shots.length === 0) {
    fail("config needs a non-empty \"shots\" array");
  }

  const width = config.width ?? 1280;
  const height = config.height ?? 800;
  const settleMs = config.settleMs ?? 400;
  const timeoutMs = config.timeoutMs ?? 30000;
  const outDir = resolve(configDir, config.outDir ?? "shots");

  const browserPath = findBrowser(config.browser);
  const profileDir = await mkdtemp(join(tmpdir(), "capture-profile-"));
  await mkdir(outDir, { recursive: true });

  const browser = spawn(
    browserPath,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-extensions",
      "--disable-background-networking",
      "--hide-scrollbars",
      `--user-data-dir=${profileDir}`,
      "--remote-debugging-port=0",
      `--window-size=${width},${height}`,
      "about:blank",
    ],
    { stdio: "ignore" }
  );

  let devtools = null;
  const written = [];
  try {
    const port = await waitForDebugPort(profileDir, timeoutMs);
    await waitForEndpoint(port, timeoutMs);
    const wsUrl = await findPageTarget(port, timeoutMs);
    devtools = await Devtools.connect(wsUrl);

    await devtools.send("Page.enable");
    await devtools.send("Runtime.enable");
    // Pin the viewport so the image is the same size on any display, whatever
    // the window manager did with the window.
    await devtools.send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: config.scale ?? 1,
      mobile: false,
    });

    const loaded = devtools.waitForEvent("Page.loadEventFired", timeoutMs);
    await devtools.send("Page.navigate", { url: config.url });
    await loaded;

    if (config.waitFor) {
      await pollUntilTrue(devtools, config.waitFor, timeoutMs, "waitFor");
    }
    if (config.setup) {
      await devtools.evaluate(config.setup);
    }
    if (config.waitForData) {
      await pollUntilTrue(
        devtools,
        config.waitForData,
        timeoutMs,
        "waitForData"
      );
    }

    for (const shot of config.shots) {
      if (!shot.name) fail("every shot needs a \"name\"");
      if (shot.script) await devtools.evaluate(shot.script);
      await sleep(shot.settleMs ?? settleMs);
      const { data } = await devtools.send("Page.captureScreenshot", {
        format: "png",
        captureBeyondViewport: false,
      });
      const outPath = join(outDir, `${shot.name}.png`);
      await writeFile(outPath, Buffer.from(data, "base64"));
      written.push(outPath);
      console.log(`captured ${outPath}`);
    }
  } finally {
    if (devtools) devtools.close();
    browser.kill();
    await rm(profileDir, { recursive: true, force: true }).catch(() => {});
  }

  if (written.length !== config.shots.length) {
    fail(
      `expected ${config.shots.length} images, wrote ${written.length}. ` +
        "Nothing was composed."
    );
  }
}

main().catch((err) => fail(err.stack || err.message));
