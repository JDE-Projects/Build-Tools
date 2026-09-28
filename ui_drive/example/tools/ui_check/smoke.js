// Scenario for ui_drive/example. Exercises every helper drive.py's cdp.mjs
// exposes. See ui_drive/README.md for what each helper does.
//
// A scenario file's default export is an async function that receives the
// helpers object.

export default async function smoke({ click, type, keys, press, evaluate, waitFor, check, screenshot, fixture }) {
  // fixture: the JSON the fixture printed before the app launched.
  check(
    "fixture JSON reached the scenario",
    !!fixture && fixture.greeting === "hello from fixture",
    `got ${JSON.stringify(fixture)}`
  );

  // evaluate(): read the page's starting state.
  const startCount = await evaluate("document.getElementById('count').textContent");
  check("page starts at count 0", startCount === "0", `got ${startCount}`);

  // click(): press the button, which calls the real js_api bridge.
  await click("#incrementBtn");
  await waitFor("document.getElementById('count').textContent === '1'", 3000);
  const afterOneClick = await evaluate("document.getElementById('count').textContent");
  check("increment button calls the real js_api bridge", afterOneClick === "1", `got ${afterOneClick}`);

  await click("#incrementBtn");
  await waitFor("document.getElementById('count').textContent === '2'", 3000);

  // type(): fill in the text box with real keyboard input.
  await type("#nameBox", "Ada");
  const nameValue = await evaluate("document.getElementById('nameBox').value");
  check("type() enters real text into the name box", nameValue === "Ada", `got ${nameValue}`);

  // press(): clear the box with Backspace, one real key event per character.
  for (let i = 0; i < nameValue.length; i++) {
    await press("Backspace");
  }
  const nameAfterBackspace = await evaluate("document.getElementById('nameBox').value");
  check("press() sends real key events", nameAfterBackspace === "", `got ${JSON.stringify(nameAfterBackspace)}`);

  // type(): on a field that already has content, replaces it rather than
  // appending to it (a real Ctrl+A select-all, then the new text).
  const prefilledBefore = await evaluate("document.getElementById('prefilledBox').value");
  check("prefilled box starts with its own value", prefilledBefore === "replace me", `got ${prefilledBefore}`);
  await type("#prefilledBox", "Grace");
  const prefilledAfter = await evaluate("document.getElementById('prefilledBox').value");
  check("type() replaces a prefilled field's contents", prefilledAfter === "Grace", `got ${prefilledAfter}`);

  // keys(): the same real per-character key events type() uses internally,
  // available directly for a plain field.
  await keys("#nameBox", "Bell");
  const nameAfterKeys = await evaluate("document.getElementById('nameBox').value");
  check("keys() sends real per-character key events", nameAfterKeys === "Bell", `got ${nameAfterKeys}`);

  // type() on a native date input: Input.insertText can't reach a
  // segmented control's sub-fields, so type() falls back to real
  // per-character key events, starting from the first segment.
  const dateBefore = await evaluate("document.getElementById('dateBox').value");
  check("date box starts at its own value", dateBefore === "2020-01-01", `got ${dateBefore}`);
  await type("#dateBox", "03152027");
  const dateAfter = await evaluate("document.getElementById('dateBox').value");
  check("type() fills a native date input via real key events", dateAfter === "2027-03-15", `got ${dateAfter}`);

  // theme toggle, then a screenshot of each theme.
  await screenshot("theme-light");
  await click("#themeBtn");
  await waitFor("document.getElementById('themeLabel').textContent === 'dark'", 3000);
  const theme = await evaluate("document.getElementById('themeLabel').textContent");
  check("theme toggle switches to dark", theme === "dark", `got ${theme}`);
  await screenshot("theme-dark");
}
