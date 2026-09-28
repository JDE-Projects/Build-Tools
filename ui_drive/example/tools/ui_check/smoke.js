// Scenario for ui_drive/example. Exercises every helper drive.py's cdp.mjs
// exposes. See ui_drive/README.md for what each helper does.
//
// A scenario file's default export is an async function that receives the
// helpers object.

export default async function smoke({ click, type, press, evaluate, waitFor, check, screenshot, fixture }) {
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

  // theme toggle, then a screenshot of each theme.
  await screenshot("theme-light");
  await click("#themeBtn");
  await waitFor("document.getElementById('themeLabel').textContent === 'dark'", 3000);
  const theme = await evaluate("document.getElementById('themeLabel').textContent");
  check("theme toggle switches to dark", theme === "dark", `got ${theme}`);
  await screenshot("theme-dark");
}
