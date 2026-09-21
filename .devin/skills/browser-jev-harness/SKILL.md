---
name: browser-jev-harness
description: "Fast-path browser actions inside a browser-harness session: TypeSafe Jev picks the operation and element in one model call, so the agent skips reading the full accessibility tree. Fall back to plain browser-harness CDP when Jev reports BLOCKED or the page is too large."
---

# browser-jev-harness

Adds the [jev-ultrafast](https://github.com/browser-use/jev-ultrafast) decision
loop as a fast path on top of `browser-harness`. Jev reads an indexed element
table (~10 KB) instead of the full AX tree (~1 MB), and one TypeSafe request
returns both the operation and its target.

## Requirements

- Run from the jev-ultrafast repo (`uv run` there): `jev_ultrafast` is an
  editable install in its venv and `.env` at the repo root holds
  `TYPESAFE_API_KEY` and `TEXT_MODEL_*`. `jev_helpers.py` loads `.env` itself;
  exported environment variables win.
- A harness tab must already be attached (`new_tab()` / `switch_tab()`).
  Jev drives that tab; it never touches Chrome's visible tab.

## Usage

```bash
uv run browser-harness <<'PY'
exec(open(".devin/skills/browser-jev-harness/jev_helpers.py").read())
new_tab("https://en.wikipedia.org/wiki/Main_Page")
print(jev_run("Open the article about Godel's incompleteness theorems."))
PY
```

Three levels:

- `jev_run(goal)` — full loop on the current tab until `done`/`blocked`.
  Pass `url=` to run on a dedicated background tab instead. Returns
  `{status, url, title, steps, model_calls, elapsed_ms, history}`.
- `jev_choose(goal)` — one observation + one TypeSafe decision, nothing
  executed. Inspect `operation`, `choice`, `confidence` first.
- `jev_act()` — execute the pending `jev_choose` decision. `TYPE_TEXT` still
  calls the small text model; nothing is typed from a guess.

Verifying outcomes by network packet (`jev_ultrafast.listener`):

```python
from jev_ultrafast.listener import wait_for_response, url_contains
b = _tab_browser()                      # attaches; Network.enable already on
# ... jev_run / jev_act / manual clicks ...
hit = wait_for_response(b.call, b.session, url_contains("/checkout"), timeout=10)
# hit -> {url, status, method, mimeType, requestId, body} or None
```

Matching is retroactive — the daemon buffers Network events from attach, so a
response that fired before the wait is still found. Use it to confirm a real
packet (e.g. an order POST returned 200) instead of trusting a `DONE` choice
or DOM text.

## When to use which

- **Jev first** for short goals on ordinary pages (search, navigate, fill a
  form). One call per step, no AX tree parsing.
- **Manual CDP** when `jev_choose`/`jev_run` returns `BLOCKED`, raises
  `max_tokens_exceeded` (heavy pages — Gmail renders ~130 controls and does
  not fit), or when you need something outside Jev's operations
  (file upload, drag, multi-step keyboard widgets, frames, canvas).
- Verify outcomes yourself: a `DONE` choice is a claim, not proof.

## Measured

Same task — open the Gödel article from Wikipedia's main page, one run each,
local Chrome, `jev-1.13.0` (2026-09-21):

| Path | Result | Wall time | Model input |
| --- | --- | --- | --- |
| Manual harness (AX tree + click_at_xy) | ok | 2,043 ms | ~2.1 MB AX data |
| `jev_run` | done | 5,365 ms | ~120 KB requests, 5 calls |

The manual row is a scripted mechanical floor — it does not include the LLM
reasoning a real agent adds per step. Jev trades some latency on small tasks
for autonomy and ~18x less model input; the gap grows with page complexity.

## Gotchas

- Background tabs throttle focus-gated widgets. Jev enables
  `Emulation.setFocusEmulationEnabled` on its own session; do the same when
  driving manually (Wikipedia suggestions never fire without it).
- `browser-harness` here means this repo's venv copy (`uv run browser-harness`),
  which shares the daemon with Jev — same `BU_NAME` rules apply.
- Reproduce numbers with
  `uv run --env-file .env python .devin/skills/browser-jev-harness/bench.py`.
