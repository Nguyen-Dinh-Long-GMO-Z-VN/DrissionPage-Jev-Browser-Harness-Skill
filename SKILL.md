---
name: browser-jev-harness
description: "Drive a web page from one natural-language goal using TypeSafe Jev inside a browser-harness session: search a site, fill and submit a form, open an article, click through a short flow. Jev reads a compact indexed element table (~10 KB) instead of the full accessibility tree (~1 MB) and picks the operation and element in one model call, so the agent spends far fewer tokens per step. Use this whenever the user asks to operate a page in the browser (\"search X on this site\", \"fill this form\", \"open the article about…\", \"find flights\") and browser-harness is available, even if they never mention Jev. Skip it for file uploads, drag and drop, frames, canvas, or heavy pages like Gmail; use plain browser-harness CDP there, or after Jev reports BLOCKED."
---

# browser-jev-harness

Runs the [jev-ultrafast](https://github.com/browser-use/jev-ultrafast) decision loop on a `browser-harness` tab.
Each step is one TypeSafe request that returns both the operation (`CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_*`,
`WAIT`, `DONE`, `BLOCKED`) and its target, chosen from an indexed table of the page's visible controls. The
win is autonomy and less model input per step, not raw wall time on tiny tasks (see
[references/benchmark.md](references/benchmark.md)).

## When not to use

If a plain HTTP request can read the answer (a public page, an API, docs), use `curl` or a fetch tool and leave the
browser alone. Use Jev when the task needs clicks, typing, navigation, a logged-in session, or JS rendering.

## Requirements

The skill folder is self-contained: `scripts/jev_helpers.py`, the `scripts/jev_ultrafast/` package, and
`scripts/requirements.txt`. Nothing depends on a repo checkout or a project venv.

- Run with `uv run --with-requirements <skill base dir>/scripts/requirements.txt browser-harness`, which installs
  `browser-harness` and `httpx` on demand.
- Put `TYPESAFE_API_KEY` and `TEXT_MODEL_*` in a `.env` in the working directory (or its parents, or the skill
  folder), or export them. The helpers load `.env` themselves and read only `TYPESAFE_*` and `TEXT_MODEL*`;
  variables already exported win. The names are in `.env.example`.
- Attach a harness tab first (`new_tab()` / `switch_tab()`). Jev drives that tab and never touches Chrome's
  visible tab.

## Usage

`<skill base dir>` is the "Base directory for this skill" shown when the skill loads. Use that absolute path so
the command works wherever the skill is installed.

```bash
uv run --with-requirements <skill base dir>/scripts/requirements.txt browser-harness <<'PY'
import sys; sys.path.insert(0, "<skill base dir>/scripts")
from jev_helpers import jev_run
new_tab("https://en.wikipedia.org/wiki/Main_Page")
print(jev_run("Open the article about Godel's incompleteness theorems."))
PY
```

Three levels, from coarse to fine:

- `jev_run(goal, max_steps=20)` runs the full loop on the current tab until `done` or `blocked`. Pass `url=` to use
  a dedicated background tab instead. Returns `{status, reason, url, title, steps, model_calls, elapsed_ms,
  history, unverified_text}`. `max_steps` caps executed actions and model calls (twice that), so a stuck run cannot drain a small
  quota such as Gemini's free tier (20 requests per model per day).
- `jev_choose(goal)` does one observation and one TypeSafe decision and executes nothing. Read `operation`,
  `choice` and `confidence` before deciding what to do.
- `jev_act()` executes the pending `jev_choose` decision once. The decision is consumed before any input, so a
  second call raises instead of clicking again; call `jev_choose` again for a new decision. `TYPE_TEXT` still
  calls the small text model, so nothing is typed from a guess. It returns `typed: "verified"` when the field's
  content matched the text after typing, or `"unverified"` when it did not (see "Long text and rich editors").
  If the browser errors after input may have reached the page, it raises "outcome unknown"; inspect the page
  before choosing again, because the decision is already consumed.

When the goal lacks a value a field needs (a phone number, a date), the run stops with `status: "blocked"` and
`reason` naming the field. Ask the user for the value and run again rather than inventing one.

Write the goal as an outcome, not a script: "Find one-way flights from Zurich to London on 20 Sept, one adult,
economy", not "click the From box, type Zurich". Jev picks the steps; scripted steps defeat the point and break
when the page changes.

## Login walls and sensitive steps

- A login wall: stop and ask the user. Only continue on its own where Chrome is already signed in.
- Stop for passwords, MFA, consent screens, and ambiguous account choices. Jev never reads or types password
  fields; do not work around that with raw CDP.
- Posting, paying, or deleting is the user's call: confirm the exact content and audience first, and check the
  result afterwards.

## Long text and rich editors

`TYPE_TEXT` reports `typed`. `"unverified"` (also listed in `jev_run`'s `unverified_text`) means the field did not
contain the text afterwards, which happens with rich editors such as Facebook's composer. It is a read, not a
retry, so it is safe to check. Never assume the text landed on `executed` alone.

1. Read the field yourself (`js(...)`) to see what it actually holds.
2. Focus it and insert the text with `cdp("Input.insertText", text=...)`, then read it back again.
3. Click the submit control with a full `mousePressed` then `mouseReleased`; a press alone does nothing.

## Domain skills

Optional, from browser-harness and off by default. Set `BH_DOMAIN_SKILLS=1`. Notes live in
`$BH_AGENT_WORKSPACE/domain-skills/<site>/*.md`, and `goto_url(url)` returns up to 10 filenames for that host.
`new_tab` and `jev_run` do not, so list the folder yourself.

- Before a site-specific task, read every file in the site's folder, then pass what matters into the goal.
- After you finish something by hand because Jev could not (a rich editor, a widget), write the working steps to
  that folder for next time. Keep them out of Jev's policy; it stays free of site-specific plans.

## Alternative backend: DrissionPage

`scripts/jev_drission.py` runs the same loop on [DrissionPage](https://github.com/g1879/DrissionPage) with no
browser-harness. It has the same `jev_run` / `jev_choose` / `jev_act`; only the transport differs.

```bash
uv run --with-requirements <skill base dir>/scripts/requirements-drission.txt python - <<'PY'
import sys; sys.path.insert(0, "<skill base dir>/scripts")
from jev_drission import jev_run
print(jev_run("Open the article about Godel's incompleteness theorems.",
              url="https://en.wikipedia.org/wiki/Main_Page"))
PY
```

- DrissionPage connects to Chrome on a debugging port (`port=9222` by default) and starts Chrome there if nothing
  is listening. With `url=` the run owns a background tab and closes it; without it, the most recently active tab
  is driven and left open. A Chrome that is already listening is attached to as it is, headless or not.
- Outcome checks use DrissionPage's own listener. Call `tab.listen.start("<url fragment>")` **before** the action,
  then `jev_ultrafast.drission.wait_for_response(tab)`. It is not retroactive, unlike the harness listener below.
- **License:** DrissionPage allows personal, learning, and non-profit use; commercial use needs its author's
  authorization. It is an optional dependency and is not bundled. Read its LICENSE before using this backend for
  commercial work; the default harness path does not involve it.

## Verify the outcome

A `DONE` choice is a claim, not proof. Confirm with a real network packet (`jev_ultrafast.listener`) or with
page state you read yourself:

```python
from jev_helpers import _tab_browser
from jev_ultrafast.listener import wait_for_response, url_contains
b = _tab_browser()                      # attaches; Network.enable is already on
# ... jev_run / jev_act / manual clicks ...
hit = wait_for_response(b.call, b.session, url_contains("/checkout"), timeout=10)
# hit -> {url, status, method, mimeType, requestId, body} or None
```

Matching is retroactive: the daemon buffers Network events from attach, so a response that fired before the wait
is still found. Use it to confirm, for example, that an order POST returned 200, rather than trusting DOM text.

## When to fall back to manual CDP

- `jev_choose` / `jev_run` returns `BLOCKED`. No supported operation can make progress.
- It raises `max_tokens_exceeded`. Heavy pages do not fit (Gmail renders ~130 controls).
- The task needs something outside Jev's operations: file upload, drag, multi-step keyboard widgets, frames,
  canvas, shadow DOM, pop-up tabs.

For those mechanics, read the matching file in browser-harness's
[interaction-skills](https://github.com/browser-use/browser-harness/tree/main/interaction-skills): `uploads.md`,
`drag-and-drop.md`, `iframes.md`, `cross-origin-iframes.md`, `shadow-dom.md`, `dropdowns.md`, `dialogs.md`,
`tabs.md`, `scrolling.md`.

## Gotchas

- Background tabs throttle focus-gated widgets. Jev enables `Emulation.setFocusEmulationEnabled` on its own
  session; do the same when driving manually (Wikipedia suggestions never fire without it).
- `browser-harness` shares its daemon with Jev, so the same `BU_NAME` rules apply.
- Jev attaches its own CDP session to the harness tab and detaches when the call returns. Do not reuse a
  `Browser` from an earlier call.
- To reproduce the benchmark: `uv run --with-requirements <skill base dir>/scripts/requirements.txt --env-file .env python <skill base dir>/scripts/bench.py`. It makes paid
  API calls.
