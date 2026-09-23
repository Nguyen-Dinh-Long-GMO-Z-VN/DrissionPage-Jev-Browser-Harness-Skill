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

## Requirements

The skill folder is self-contained: `scripts/jev_helpers.py`, the `scripts/jev_ultrafast/` package, and
`scripts/requirements.txt`. Nothing depends on a repo checkout or a project venv.

- Run with `uv run --with-requirements <skill base dir>/scripts/requirements.txt browser-harness`, which installs
  `browser-harness` and `httpx` on demand.
- Put `TYPESAFE_API_KEY` and `TEXT_MODEL_*` in a `.env` in the working directory (or its parents, or the skill
  folder), or export them. `scripts/jev_helpers.py` loads `.env` itself; variables already exported win. The
  names are in `.env.example`.
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
  history}`. `max_steps` caps executed actions and model calls (twice that), so a stuck run cannot drain a small
  quota such as Gemini's free tier (20 requests per model per day).
- `jev_choose(goal)` does one observation and one TypeSafe decision and executes nothing. Read `operation`,
  `choice` and `confidence` before deciding what to do.
- `jev_act()` executes the pending `jev_choose` decision once. The decision is consumed before any input, so a
  second call raises instead of clicking again; call `jev_choose` again for a new decision. `TYPE_TEXT` still
  calls the small text model, so nothing is typed from a guess.

When the goal lacks a value a field needs (a phone number, a date), the run stops with `status: "blocked"` and
`reason` naming the field. Ask the user for the value and run again rather than inventing one.

Write the goal as an outcome, not a script: "Find one-way flights from Zurich to London on 20 Sept, one adult,
economy", not "click the From box, type Zurich". Jev picks the steps; scripted steps defeat the point and break
when the page changes.

## Verify the outcome

A `DONE` choice is a claim, not proof. Confirm with a real network packet (`jev_ultrafast.listener`) or with
page state you read yourself:

```python
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

## Gotchas

- Background tabs throttle focus-gated widgets. Jev enables `Emulation.setFocusEmulationEnabled` on its own
  session; do the same when driving manually (Wikipedia suggestions never fire without it).
- `browser-harness` shares its daemon with Jev, so the same `BU_NAME` rules apply.
- Jev attaches its own CDP session to the harness tab and detaches when the call returns. Do not reuse a
  `Browser` from an earlier call.
- To reproduce the benchmark: `uv run --with-requirements <skill base dir>/scripts/requirements.txt --env-file .env python <skill base dir>/scripts/bench.py`. It makes paid
  API calls.
