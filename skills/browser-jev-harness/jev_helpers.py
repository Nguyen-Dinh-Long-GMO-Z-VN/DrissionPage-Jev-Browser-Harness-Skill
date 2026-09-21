"""Jev fast-path helpers for a browser-harness session.

Load inside a `browser-harness` heredoc (run from the jev-ultrafast repo so the
venv provides `jev_ultrafast` and `.env` is found):

    uv run browser-harness <<'PY'
    exec(open(".devin/skills/browser-jev-harness/jev_helpers.py").read())
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
from jev_ultrafast.model import choose, field_context, field_text  # noqa: E402

_LAST = {}


def _tab_browser():
    """A jev Browser bound to the harness's attached tab instead of a new one."""
    session = cdp("Target.attachToTarget", targetId=current_tab()["targetId"], flatten=True)["sessionId"]
    browser = Browser.__new__(Browser)
    browser.target, browser.session, browser.after_input = None, session, None
    browser.call("Emulation.setFocusEmulationEnabled", enabled=True)
    browser.call("Network.enable")  # daemon only auto-enables its own sessions
    return browser


def _detach(browser):
    if browser and browser.session:
        cdp("Target.detachFromTarget", sessionId=browser.session)
        browser.session = None


def _result(agent):
    s = agent.snapshot()
    return {
        "status": s["status"],
        "url": s["page"]["url"],
        "title": s["page"]["title"],
        "steps": len(s["history"]),
        "model_calls": len(s["decisions"]),
        "elapsed_ms": s["elapsed_ms"],
        "history": [h["action"] for h in s["history"]],
    }


def jev_run(goal, url=None):
    """Run the full Jev loop. With url=, uses a dedicated background tab;
    without, drives the harness's current tab. Returns a result dict."""
    if url is not None:
        with Agent(url, goal) as agent:
            for _ in agent.run():
                pass
            return _result(agent)
    agent = Agent.__new__(Agent)
    agent.pending_text = None
    agent.browser = _tab_browser()
    agent.record_dir = None
    agent.screenshots = False
    agent.state = dict(
        browser=agent.browser,
        goal=goal,
        page=agent.browser.observe(screenshot=False),
        decision=None,
        history=[],
        status="ready",
        plan=[goal],
        plan_index=0,
        decisions=[],
        text_calls=[],
        elapsed_ms=0,
        started_at=None,
        record=False,
    )
    try:
        for _ in agent.run():
            pass
        return _result(agent)
    finally:
        _detach(agent.browser)


def jev_choose(goal):
    """Observe the current tab and return Jev's choice without executing it.
    Pair with jev_act() to execute, or ignore and drive the tab manually."""
    browser = _tab_browser()
    page = browser.observe(screenshot=False)
    decision = choose(page, goal, [])
    _LAST.update(browser=browser, page=page, decision=decision, goal=goal)
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
    """Execute the pending jev_choose decision on the current tab. TYPE_TEXT
    still goes through the small text model; nothing is typed without it."""
    browser, page, decision, goal = (_LAST[k] for k in ("browser", "page", "decision", "goal"))
    choice = decision["choice"]
    if choice in {"DONE", "BLOCKED"}:
        _detach(browser)
        return {"executed": choice}
    action = next(a for a in page["actions"] if a["id"] == choice)
    text = None
    if action["kind"] == "fill":
        text, _ = field_text(field_context(goal, action, page, []))
    browser.act(action, page, text=text)
    _detach(browser)
    return {"executed": action["id"], "label": action["label"], "text": text}
