"""Jev fast-path helpers for a browser-harness session.

Load inside a `browser-harness` heredoc (run from the jev-ultrafast repo so the
venv provides `jev_ultrafast` and `.env` is found):

    uv run browser-harness <<'PY'
    exec(open("<skill base dir>/scripts/jev_helpers.py").read())
    print(jev_run("Open the article about Godel's incompleteness theorems."))
    PY

Three levels of use:
- jev_run(goal)          full loop on the current harness tab (or own tab via url=)
- jev_choose(goal)       observe + one TypeSafe decision, nothing executed
- jev_act()              execute the decision returned by jev_choose
"""

import os
from pathlib import Path

# Credentials stay in .env (gitignored); existing environment wins.
for _p in (Path.cwd(), *Path.cwd().parents):
    _env = _p / ".env"
    if _env.exists():
        for _line in _env.read_text().splitlines():
            if "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
        break

from browser_harness.helpers import cdp, current_tab  # noqa: E402

from jev_ultrafast.agent import Agent  # noqa: E402
from jev_ultrafast.browser import Browser  # noqa: E402
from jev_ultrafast.model import MissingValue, choose, field_context, field_text  # noqa: E402

_LAST = {}


def _tab_browser():
    """A jev Browser bound to the harness's attached tab instead of a new one."""
    session = cdp("Target.attachToTarget", targetId=current_tab()["targetId"], flatten=True)["sessionId"]
    return Browser.from_session(session)


def _detach(browser):
    if browser and browser.session:
        cdp("Target.detachFromTarget", sessionId=browser.session)
        browser.session = None


def _result(agent):
    s = agent.snapshot()
    return {
        "status": s["status"],
        "reason": s["reason"],
        "url": s["page"]["url"],
        "title": s["page"]["title"],
        "steps": len(s["history"]),
        "model_calls": len(s["decisions"]),
        "elapsed_ms": s["elapsed_ms"],
        "history": [h["action"] for h in s["history"]],
    }


def jev_run(goal, url=None, max_steps=20):
    """Run the full Jev loop. With url=, uses a dedicated background tab;
    without, drives the harness's current tab. Returns a result dict.
    max_steps caps executed actions (model calls are capped at twice that), so a
    stuck run cannot burn a small API quota."""
    if url is not None:
        with Agent(url, goal, max_steps=max_steps) as agent:
            for _ in agent.run():
                pass
            return _result(agent)
    browser = _tab_browser()
    try:
        agent = Agent.attach(browser, goal, max_steps=max_steps)
        for _ in agent.run():
            pass
        return _result(agent)
    finally:
        _detach(browser)


def jev_choose(goal):
    """Observe the current tab and return Jev's choice without executing it.
    Pair with jev_act() to execute, or ignore and drive the tab manually.
    The CDP session is released before returning; jev_act() attaches its own."""
    browser = _tab_browser()
    try:
        page = browser.settle(browser.observe(screenshot=False))
        decision = choose(page, goal, [])
    finally:
        _detach(browser)
    _LAST.clear()
    _LAST.update(page=page, decision=decision, goal=goal)
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
    """Execute the pending jev_choose decision on the current tab, at most once.
    The decision is consumed before any input, so calling jev_act() again cannot
    repeat a click. TYPE_TEXT still goes through the small text model; nothing is
    typed without it. Raises StalePage if the page changed since jev_choose."""
    if "decision" not in _LAST:
        raise ValueError("No pending decision. Call jev_choose(goal) first.")
    page, decision, goal = _LAST.pop("page"), _LAST.pop("decision"), _LAST.pop("goal")
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
    browser = _tab_browser()
    try:
        browser.act(action, page, text=text)
    finally:
        _detach(browser)
    return {"executed": action["id"], "label": action["label"], "text": text}
