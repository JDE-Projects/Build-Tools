#!/usr/bin/env node
// Runs one scenario against an already-running page over the Chrome
// DevTools Protocol (CDP), then writes results.json into the output folder.
//
// Not meant to be run by hand: drive.py launches the app, waits for the
// debug port, finds the page target, then calls this with a small JSON
// request file describing what to do. See README.md for the scenario
// format and the helpers a scenario can use.
//
// Zero npm dependencies: Node 22+ provides fetch and WebSocket built in.
//
// Usage: node cdp.mjs <request.json>

import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

function fail(message) {
  console.error(`ERROR: ${message}`);
  process.exit(1);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Minimal DevTools protocol client over the built-in WebSocket. Same shape
// as screenshot/capture.mjs's Devtools class.
class Devtools {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
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
    }
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((ok, no) => {
      this.pending.set(id, { resolve: ok, reject: no });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

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

// windowsVirtualKeyCode / code for the key names scenarios are likely to
// need. Extend this table rather than accepting raw codes from a scenario,
// so a scenario file stays plain and typo-safe.
const KEY_TABLE = {
  Enter: { key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 },
  Tab: { key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 },
  Escape: { key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 },
  Backspace: { key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 },
  ArrowUp: { key: "ArrowUp", code: "ArrowUp", windowsVirtualKeyCode: 38 },
  ArrowDown: { key: "ArrowDown", code: "ArrowDown", windowsVirtualKeyCode: 40 },
};

// Returns the element's on-screen centre and whether it is a safe click
// target: present, visible, not disabled, and not covered by something
// else at that point. Runs entirely inside the page so it sees real layout,
// including anything scrolling did just before.
const PROBE_JS = (selector) => `
(function() {
  const el = document.querySelector(${JSON.stringify(selector)});
  if (!el) return { found: false };
  el.scrollIntoView({ block: "center", inline: "center" });
  const r = el.getBoundingClientRect();
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  const style = window.getComputedStyle(el);
  const hidden = style.visibility === "hidden" || style.display === "none" || r.width === 0 || r.height === 0;
  const disabled = !!el.disabled;
  const top = document.elementFromPoint(cx, cy);
  const covered = !(top && (top === el || el.contains(top) || top.contains(el)));
  return { found: true, x: cx, y: cy, hidden, disabled, covered };
})()
`;

class Runner {
  constructor(devtools, outDir, fixture) {
    this.devtools = devtools;
    this.outDir = outDir;
    this.fixture = fixture;
    this.checks = [];
  }

  async _probe(selector) {
    const probe = await this.devtools.evaluate(PROBE_JS(selector));
    if (!probe.found) throw new Error(`element not found: ${selector}`);
    if (probe.hidden) throw new Error(`element is hidden: ${selector}`);
    if (probe.disabled) throw new Error(`element is disabled: ${selector}`);
    if (probe.covered) throw new Error(`another element is on top of: ${selector}`);
    return probe;
  }

  async _clickAt(x, y) {
    const opts = { x, y, button: "left", clickCount: 1 };
    await this.devtools.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await this.devtools.send("Input.dispatchMouseEvent", { type: "mousePressed", ...opts });
    await this.devtools.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...opts });
  }

  async click(selector) {
    const probe = await this._probe(selector);
    await this._clickAt(probe.x, probe.y);
  }

  async type(selector, text) {
    const probe = await this._probe(selector);
    await this._clickAt(probe.x, probe.y);
    await this.devtools.send("Input.insertText", { text });
  }

  async press(key) {
    const spec = KEY_TABLE[key];
    if (!spec) throw new Error(`press(): unknown key ${key}. Add it to KEY_TABLE in cdp.mjs.`);
    await this.devtools.send("Input.dispatchKeyEvent", { type: "keyDown", ...spec });
    await this.devtools.send("Input.dispatchKeyEvent", { type: "keyUp", ...spec });
  }

  async evaluate(js) {
    return this.devtools.evaluate(js);
  }

  async waitFor(js, timeoutMs = 5000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await this.devtools.evaluate(`!!(${js})`)) return true;
      await sleep(150);
    }
    throw new Error(`waitFor timed out after ${timeoutMs}ms: ${js}`);
  }

  check(name, pass, detail) {
    this.checks.push({ name, pass: !!pass, detail: detail || null });
  }

  // Waits settleMs first so a CSS transition (a theme fade, for example) has
  // finished; a shot taken mid-fade looks like a styling bug that isn't there.
  async screenshot(name, settleMs = 600) {
    if (settleMs > 0) await sleep(settleMs);
    const { data } = await this.devtools.send("Page.captureScreenshot", { format: "png" });
    const path = join(this.outDir, `${name}.png`);
    await writeFile(path, Buffer.from(data, "base64"));
    return path;
  }
}

async function main() {
  const requestPath = process.argv[2];
  if (!requestPath) fail("usage: node cdp.mjs <request.json>");

  const request = JSON.parse(await (await import("node:fs/promises")).readFile(requestPath, "utf-8"));
  const { webSocketDebuggerUrl, scenarioPath, outDir, fixture, timeoutMs } = request;

  const results = { checks: [], error: null };
  const effectiveTimeoutMs = timeoutMs ?? 30000;
  let devtools = null;
  let runner = null;
  let timeoutHandle = null;

  const run = async () => {
    devtools = await Devtools.connect(webSocketDebuggerUrl);
    await devtools.send("Page.enable");
    await devtools.send("Runtime.enable");
    await devtools.send("DOM.enable");

    runner = new Runner(devtools, outDir, fixture ?? null);
    const helpers = {
      click: (s) => runner.click(s),
      type: (s, t) => runner.type(s, t),
      press: (k) => runner.press(k),
      evaluate: (js) => runner.evaluate(js),
      waitFor: (js, t) => runner.waitFor(js, t),
      check: (n, p, d) => runner.check(n, p, d),
      screenshot: (n, ms) => runner.screenshot(n, ms),
      fixture: runner.fixture,
    };

    const mod = await import(pathToFileURL(scenarioPath).href);
    const scenarioFn = mod.default;
    if (typeof scenarioFn !== "function") {
      throw new Error(`${scenarioPath} must have a default export that is an async function`);
    }
    await scenarioFn(helpers);
  };

  // A timer left running after run() already won or lost the race keeps
  // Node's event loop alive until it fires, so every run (including a fast,
  // successful one) would otherwise sit here for the full timeout. Clearing
  // it as soon as either side settles, in the finally block, is what lets a
  // passing run exit right away instead of always waiting out the ceiling.
  const timeoutPromise = new Promise((_resolve, reject) => {
    timeoutHandle = setTimeout(() => {
      reject(new Error(`scenario timed out after ${effectiveTimeoutMs}ms`));
    }, effectiveTimeoutMs);
  });

  try {
    await Promise.race([run(), timeoutPromise]);
  } catch (err) {
    results.error = err.stack || err.message || String(err);
  } finally {
    clearTimeout(timeoutHandle);
    if (devtools) devtools.close();
  }

  // Whatever checks the scenario recorded before it threw or timed out are
  // real results and worth keeping, not just the ones from a clean finish.
  if (runner) results.checks = runner.checks;

  await writeFile(join(outDir, "results.json"), JSON.stringify(results, null, 2));
  if (results.error) {
    console.error(results.error);
    process.exit(1);
  }
  process.exit(0);
}

main().catch((err) => fail(err.stack || err.message));
