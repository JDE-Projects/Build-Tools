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
// Usage: node cdp.mjs < request.json
// The request (the same JSON object drive.py used to write to
// cdp_request.json) is read from stdin instead of a file, so nothing this
// process needs ever has to sit on disk where another process could read
// or race it.

import { writeFile } from "node:fs/promises";
import { join, dirname, basename, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { tmpdir } from "node:os";

function fail(message) {
  console.error(`ERROR: ${message}`);
  process.exit(1);
}

// Matches drive.py's RUN_DIR_NAME_RE: the run folder name format is this
// tool's own, not something to take on trust from a request.
const RUN_DIR_NAME_RE = /^ui-drive-[0-9a-f]{32}$/;

// A scenario picks its own screenshot name; keep it to a small safe
// character set so it can never become a path (no slashes, dots, or drive
// letters) before it is joined onto outDir.
const SCREENSHOT_NAME_RE = /^[A-Za-z0-9_-]{1,64}$/;

export function isValidScreenshotName(name) {
  return typeof name === "string" && SCREENSHOT_NAME_RE.test(name);
}

// Independently checks that outDir really is this run's folder rather than
// trusting whatever path the request carries: drive.py launches this process
// with TEMP set to <run folder>\tmp, so outDir must be the folder holding
// that private temp folder, and must be named the way drive.py names one.
function validateOutDir(outDir) {
  if (typeof outDir !== "string" || !outDir) fail("request is missing outDir");
  const resolved = resolve(outDir);
  const name = basename(resolved);
  const privateTemp = resolve(tmpdir());
  if (basename(privateTemp).toLowerCase() !== "tmp" || dirname(privateTemp).toLowerCase() !== resolved.toLowerCase()) {
    fail(`outDir is not the run folder holding this process's private temp folder: ${resolved}`);
  }
  if (!RUN_DIR_NAME_RE.test(name)) {
    fail(`outDir does not look like a ui_drive run folder: ${name}`);
  }
  return resolved;
}

function readStdin() {
  return new Promise((resolvePromise, rejectPromise) => {
    const chunks = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolvePromise(Buffer.concat(chunks).toString("utf-8")));
    process.stdin.on("error", rejectPromise);
  });
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
  Home: { key: "Home", code: "Home", windowsVirtualKeyCode: 36 },
};

// CDP Input.dispatchKeyEvent modifier bits (Alt/Ctrl/Meta/Shift).
const MODIFIER_CTRL = 2;
const MODIFIER_SHIFT = 8;

// key/code/windowsVirtualKeyCode for a single printable character, used by
// keys() and by type()'s per-character fallback for native segmented inputs.
// Covers what scenarios are likely to type: digits, letters, and a handful
// of punctuation marks. A character not in this table still carries through
// as plain text (most editable fields accept that), but a native control
// that insists on a real key code for it won't respond; add it here if a
// scenario needs one.
const PUNCTUATION_KEYS = {
  " ": { code: "Space", vk: 32 },
  "-": { code: "Minus", vk: 189 },
  "_": { code: "Minus", vk: 189, shift: true },
  "/": { code: "Slash", vk: 191 },
  "?": { code: "Slash", vk: 191, shift: true },
  ".": { code: "Period", vk: 190 },
  ",": { code: "Comma", vk: 188 },
  "@": { code: "Digit2", vk: 50, shift: true },
  "'": { code: "Quote", vk: 222 },
  ":": { code: "Semicolon", vk: 186, shift: true },
  ";": { code: "Semicolon", vk: 186 },
};

function charKeySpec(ch) {
  if (ch >= "0" && ch <= "9") {
    return { key: ch, code: `Digit${ch}`, windowsVirtualKeyCode: 0x30 + Number(ch) };
  }
  const lower = ch.toLowerCase();
  if (lower >= "a" && lower <= "z") {
    const isUpper = ch !== lower;
    return {
      key: ch,
      code: `Key${lower.toUpperCase()}`,
      windowsVirtualKeyCode: lower.toUpperCase().charCodeAt(0),
      modifiers: isUpper ? MODIFIER_SHIFT : 0,
    };
  }
  const p = PUNCTUATION_KEYS[ch];
  if (p) {
    return { key: ch, code: p.code, windowsVirtualKeyCode: p.vk, modifiers: p.shift ? MODIFIER_SHIFT : 0 };
  }
  return { key: ch, code: "", windowsVirtualKeyCode: 0 };
}

// Native input types with their own segmented editing UI (date/time
// pickers), where a person types digit by digit into the currently
// highlighted segment rather than replacing the whole field's text at once.
// Input.insertText does not drive these at all: it lands nowhere, silently.
const SEGMENTED_INPUT_TYPES = new Set(["date", "time", "month", "week", "datetime-local"]);

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
  const inputType = el.tagName === "INPUT" ? (el.getAttribute("type") || "text").toLowerCase() : null;
  // A few px in from the left edge, not the centre: for a segmented input
  // (date/time), this is what reliably lands the caret in the first
  // segment regardless of which locale format the segments are displayed
  // in, left to right.
  const leftX = r.left + Math.min(8, r.width / 4);
  return { found: true, x: cx, y: cy, leftX, hidden, disabled, covered, inputType };
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

  // Selects the field's entire current contents with a real Ctrl+A key
  // event (the "selectAll" editor command, not just the key's default
  // browser handling), the same way a person clears a field before typing
  // over it. Verified against QtWebEngine: after this, an insertText call
  // replaces the selection rather than inserting alongside it.
  async _selectAll() {
    const spec = { key: "a", code: "KeyA", windowsVirtualKeyCode: 65, modifiers: MODIFIER_CTRL };
    await this.devtools.send("Input.dispatchKeyEvent", {
      type: "keyDown",
      commands: ["selectAll"],
      ...spec,
    });
    await this.devtools.send("Input.dispatchKeyEvent", { type: "keyUp", ...spec });
  }

  // Sends one real keyDown/keyUp pair per character, the same shape as
  // press() but with the character attached so the page (and any native
  // control underneath it) sees an actual keystroke instead of a block of
  // inserted text. This is what a native segmented control like
  // <input type="date"> needs: Input.insertText does not reach its
  // sub-fields at all.
  async _typeChars(text) {
    for (const ch of text) {
      const spec = charKeySpec(ch);
      await this.devtools.send("Input.dispatchKeyEvent", {
        type: "keyDown",
        text: ch,
        unmodifiedText: ch,
        ...spec,
      });
      await this.devtools.send("Input.dispatchKeyEvent", { type: "keyUp", ...spec });
    }
  }

  // Replaces the field's contents the way a person does: click it, select
  // everything already there, then type over it. For a native segmented
  // input (date/time/month/week/datetime-local), Input.insertText never
  // reaches the sub-fields at all, so this instead presses Home (jumps to
  // the first segment) and sends text as real per-character key events,
  // which is also how a person fills one of these in: each segment
  // highlights itself as it gains focus and the next keystroke overwrites
  // it, auto-advancing to the next segment.
  async type(selector, text) {
    const probe = await this._probe(selector);
    if (probe.inputType && SEGMENTED_INPUT_TYPES.has(probe.inputType)) {
      // Click near the left edge, not the centre: that's what reliably
      // lands the caret in the first segment, whichever segment that is
      // for the browser's current locale (month, day, or year could all
      // come first). Home as a belt-and-braces nudge in case the click
      // alone leaves it elsewhere.
      await this._clickAt(probe.leftX, probe.y);
      await this.press("Home");
      await this._typeChars(text);
      return;
    }
    await this._clickAt(probe.x, probe.y);
    await this._selectAll();
    await this.devtools.send("Input.insertText", { text });
  }

  // Sends text as real per-character key events on any element, regardless
  // of input type. type() already does this automatically for a native
  // segmented input; use this directly for a plain field when a scenario
  // needs to watch something react to individual keystrokes rather than one
  // bulk text-insertion event.
  async keys(selector, text) {
    const probe = await this._probe(selector);
    await this._clickAt(probe.x, probe.y);
    await this._typeChars(text);
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
    if (!isValidScreenshotName(name)) {
      throw new Error(`screenshot(): invalid name ${JSON.stringify(name)}`);
    }
    if (settleMs > 0) await sleep(settleMs);
    const { data } = await this.devtools.send("Page.captureScreenshot", { format: "png" });
    const path = join(this.outDir, `${name}.png`);
    await writeFile(path, Buffer.from(data, "base64"), { flag: "wx" });
    return path;
  }
}

async function main() {
  const raw = await readStdin();
  let request;
  try {
    request = JSON.parse(raw);
  } catch (err) {
    fail(`request on stdin was not valid JSON: ${err.message}`);
  }
  const { webSocketDebuggerUrl, scenarioPath, fixture, timeoutMs } = request;
  const outDir = validateOutDir(request.outDir);

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
      keys: (s, t) => runner.keys(s, t),
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

  await writeFile(join(outDir, "results.json"), JSON.stringify(results, null, 2), { flag: "wx" });
  if (results.error) {
    console.error(results.error);
    process.exit(1);
  }
  process.exit(0);
}

// Only runs the scenario when this file is executed directly (`node
// cdp.mjs`), not when a test imports it just to reach isValidScreenshotName.
if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((err) => fail(err.stack || err.message));
}
