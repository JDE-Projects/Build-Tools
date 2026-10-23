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

// A problem the user can act on. Thrown rather than exiting on the spot, so
// the browser is always shut down and its profile folder removed first.
class CaptureError extends Error {}

function fail(message) {
  throw new CaptureError(message);
}

// Shuts the browser down so its profile folder is no longer in use when it is
// deleted. Asks over the DevTools protocol first: on Windows the process this
// script started can hand over to another and exit, so killing it alone can
// leave the real browser running. Killing is the fallback.
async function stopBrowser(browser, browserWsUrl, timeoutMs) {
  if (browserWsUrl) {
    try {
      const control = await Devtools.connect(browserWsUrl);
      await Promise.race([control.send("Browser.close"), sleep(timeoutMs)]);
      control.close();
    } catch {
      // browser already gone or not answering, so fall back to killing it
    }
  }
  if (browser.exitCode !== null || browser.signalCode !== null) return;
  await new Promise((done) => {
    const timer = setTimeout(done, timeoutMs);
    browser.once("exit", () => {
      clearTimeout(timer);
      done();
    });
    browser.kill();
  });
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

  // Runs a sign-in expression without echoing it in an error, because the
  // expression can contain the configured throwaway password.
  async evaluateSignIn(expression, errorMessage) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) throw new Error(errorMessage);
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

async function waitForSignIn(devtools, doneWhen, password, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    // The page navigates away after a successful sign-in, and a check that
    // lands mid-navigation fails without meaning anything, so try again.
    try {
      if (await devtools.evaluate(doneWhen)) return;
    } catch {
      // page was between documents
    }
    await sleep(200);
  }
  const alertText = (await devtools.evaluate(
    "(() => document.querySelector('[role=alert], .error, #error')?.textContent?.trim() || '')()"
  )).split(password).join("[redacted]");
  fail(
    `sign-in did not complete within ${timeoutMs}ms` +
      (alertText ? `: ${alertText}` : "")
  );
}

async function signIn(devtools, config, timeoutMs) {
  const { url, passwordSelector, password, submitSelector, doneWhen } =
    config.signIn;
  const fieldSelector = JSON.stringify(passwordSelector);
  const passwordValue = JSON.stringify(password);

  const loaded = devtools.waitForEvent("Page.loadEventFired", timeoutMs);
  await devtools.send("Page.navigate", { url });
  await loaded;

  // Polled here rather than through pollUntilTrue, whose failure message
  // quotes the expression: nothing from the signIn block is ever printed.
  const deadline = Date.now() + timeoutMs;
  while (!(await devtools.evaluate(`!!document.querySelector(${fieldSelector})`))) {
    if (Date.now() >= deadline) {
      fail(
        `the sign-in page showed no element matching signIn.passwordSelector ` +
          `within ${timeoutMs}ms`
      );
    }
    await sleep(200);
  }
  await devtools.evaluateSignIn(
    `(() => {
      const field = document.querySelector(${fieldSelector});
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, "value"
      )?.set;
      if (!field || !setter) throw new Error("password field is unavailable");
      setter.call(field, ${passwordValue});
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
    })()`,
    "could not enter the sign-in password"
  );

  if (submitSelector) {
    await devtools.evaluateSignIn(
      `(() => {
        const submitter = document.querySelector(${JSON.stringify(submitSelector)});
        if (!submitter) throw new Error("submit element is unavailable");
        submitter.click();
      })()`,
      "could not submit the sign-in form"
    );
  } else {
    await devtools.evaluateSignIn(
      `(() => {
        const form = document.querySelector(${fieldSelector})?.closest("form");
        if (!form) throw new Error("sign-in form is unavailable");
        form.requestSubmit();
      })()`,
      "could not submit the sign-in form"
    );
  }

  const defaultDoneWhen = `location.pathname !== ${JSON.stringify(
    new URL(url).pathname
  )}`;
  await waitForSignIn(
    devtools,
    doneWhen ?? defaultDoneWhen,
    password,
    timeoutMs
  );
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
  if (config.signIn !== undefined) {
    if (
      !config.signIn ||
      typeof config.signIn !== "object" ||
      Array.isArray(config.signIn)
    ) {
      fail("config \"signIn\" must be an object");
    }
    if (typeof config.signIn.url !== "string" || !config.signIn.url) {
      fail("config signIn needs a \"url\"");
    }
    if (
      typeof config.signIn.passwordSelector !== "string" ||
      !config.signIn.passwordSelector
    ) {
      fail("config signIn needs a \"passwordSelector\"");
    }
    if (typeof config.signIn.password !== "string" || !config.signIn.password) {
      fail("config signIn needs a \"password\"");
    }
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
  let browserWsUrl = null;
  const written = [];
  try {
    const port = await waitForDebugPort(profileDir, timeoutMs);
    browserWsUrl = (await waitForEndpoint(port, timeoutMs)).webSocketDebuggerUrl;
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

    if (config.signIn) await signIn(devtools, config, timeoutMs);

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
    await stopBrowser(browser, browserWsUrl, 10000);
    // The browser's helper processes can hold files for a moment after it
    // exits, so the delete retries before giving up.
    await rm(profileDir, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 300,
    }).catch((err) => {
      console.error(
        `WARNING: could not remove the browser profile folder ${profileDir} ` +
          `(${err.code || err.message}). Delete it by hand.`
      );
    });
  }

  if (written.length !== config.shots.length) {
    fail(
      `expected ${config.shots.length} images, wrote ${written.length}. ` +
        "Nothing was composed."
    );
  }
}

main().catch((err) => {
  console.error(
    `ERROR: ${err instanceof CaptureError ? err.message : err.stack || err.message}`
  );
  process.exit(1);
});
