"""Jev fast-path helpers on DrissionPage. No Browser Harness needed.

Load from any Python process (run from anywhere; the skill folder is self-contained):

    uv run --with-requirements <skill base dir>/scripts/requirements-drission.txt python - <<'PY'
    import sys; sys.path.insert(0, "<skill base dir>/scripts")
    from jev_drission import jev_run
    print(jev_run("Open the article about Godel's incompleteness theorems.",
                  url="https://en.wikipedia.org/wiki/Main_Page"))
    PY

DrissionPage connects to Chrome on a debugging port (default 9222) and starts Chrome there when nothing is
listening. Without `url=` the helpers drive the most recently active tab; the tab is left open.

Same three levels as jev_helpers.py: jev_run, jev_choose, jev_act. To confirm an outcome, start
`tab.listen.start("<url fragment>")` before the action, then `jev_ultrafast.drission.wait_for_response(tab)`.

DrissionPage is licensed for personal, learning, and non-profit use; commercial use needs the author's
authorization. Read its LICENSE before depending on it.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Credentials stay in .env (gitignored); existing environment wins.
for _p in (Path.cwd(), *Path.cwd().parents, HERE.parent):
    _env = _p / ".env"
    if _env.exists():
        for _line in _env.read_text().splitlines():
            if "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
        break

from jev_ultrafast.agent import Agent, summarize  # noqa: E402
from jev_ultrafast.drission import DrissionBrowser, connect  # noqa: E402
from jev_ultrafast.model import MissingValue, choose, field_context, field_text  # noqa: E402

_LAST = {}


def _attach(port, url=None):
    """A DrissionBrowser: a new background tab at `url`, or the latest tab when url is None."""
    chromium = connect(port)
    if url is not None:
        return DrissionBrowser.open(chromium, url)
    return DrissionBrowser.from_tab(chromium.latest_tab)


def jev_run(goal, url=None, max_steps=20, port=9222):
    """Run the full Jev loop and return a result dict. `max_steps` caps executed actions
    (model calls are capped at twice that). With url= the run uses its own tab and closes it."""
    browser = _attach(port, url)
    try:
        agent = Agent.attach(browser, goal, max_steps=max_steps)
        for _ in agent.run():
            pass
        return summarize(agent)
    finally:
        browser.close()


def jev_choose(goal, port=9222):
    """Observe the current tab and return Jev's choice without executing it."""
    browser = _attach(port)
    page = browser.settle(browser.observe(screenshot=False))
    decision = choose(page, goal, [])
    _LAST.clear()
    _LAST.update(page=page, decision=decision, goal=goal, port=port)
    return {
        "url": page["url"],
        "title": page["title"],
        "elements": len(page["actions"]),
        "operation": decision["operation"],
        "choice": decision["choice"],
        "target": decision["target"],
        "confidence": decision["confidence"],
        "operation_probabilities": decision["operation_probabilities"],
        "latency_ms": decision["latency_ms"],
    }


def jev_act():
    """Execute the pending jev_choose decision, at most once. The decision is consumed before any input,
    so a second call cannot repeat a click. TYPE_TEXT still goes through the small text model."""
    if "decision" not in _LAST:
        raise ValueError("No pending decision. Call jev_choose(goal) first.")
    page, decision, goal, port = (_LAST.pop(k) for k in ("page", "decision", "goal", "port"))
    choice = decision["choice"]
    if choice in {"DONE", "BLOCKED"}:
        return {"executed": choice}
    action = next(a for a in page["actions"] if a["id"] == choice)
    text = None
    if action["kind"] == "fill":
        try:
            text, _ = field_text(field_context(goal, action, page, []))
        except MissingValue as missing:
            return {"executed": None, "blocked": str(missing)}
    _attach(port).act(action, page, text=text)
    return {"executed": action["id"], "label": action["label"], "text": text}
