"""Jev fast-path helpers for a browser-harness session.

The skill is self-contained: this file, the `jev_ultrafast` package next to it, and
`requirements.txt` are everything it needs. Load it inside a `browser-harness` heredoc:

    uv run --with-requirements <skill base dir>/scripts/requirements.txt browser-harness <<'PY'
    import sys; sys.path.insert(0, "<skill base dir>/scripts")
    from jev_helpers import jev_run
    new_tab("https://en.wikipedia.org/wiki/Main_Page")
    print(jev_run("Open the article about Godel's incompleteness theorems."))
    PY

Three levels of use:
- jev_run(goal)          full loop on the current harness tab (or own tab via url=)
- jev_choose(goal)       observe + one TypeSafe decision, nothing executed
- jev_act()              execute the decision returned by jev_choose
"""

import sys
from pathlib import Path

# Self-contained: make the bundled jev_ultrafast package importable without installing it.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from browser_harness.helpers import cdp, current_tab  # noqa: E402

from jev_ultrafast.browser import Browser  # noqa: E402
from jev_ultrafast.env import load_env  # noqa: E402
from jev_ultrafast.skill import Skill  # noqa: E402

load_env(HERE.parent)  # Jev's own variables only; anything already exported wins


def _tab_browser():
    """A jev Browser bound to the harness's attached tab instead of a new one."""
    session = cdp("Target.attachToTarget", targetId=current_tab()["targetId"], flatten=True)["sessionId"]
    return Browser.from_session(session)


def _open(url=None):
    return Browser(url) if url is not None else _tab_browser()


def _detach(browser):
    if browser and browser.session:
        cdp("Target.detachFromTarget", sessionId=browser.session)
        browser.session = None


def _release(browser):
    # A tab we created is closed; the harness's own tab is only detached from, never closed.
    browser.close() if browser.target else _detach(browser)


_skill = Skill(_open, _release)


def jev_run(goal, url=None, max_steps=20, done_when=None):
    """Run the full Jev loop and return a result dict. With url=, uses a dedicated window;
    without, drives the harness's current tab. max_steps caps executed actions (model calls are
    capped at twice that), so a stuck run cannot burn a small API quota. done_when(page) -> bool
    ends the run as done without a model call (page has url, title, text, actions)."""
    return _skill.run(goal, url, max_steps, done_when)


def jev_choose(goal):
    """Observe the current tab and return Jev's choice without executing it.
    Pair with jev_act() to execute, or ignore and drive the tab manually.
    The CDP session is released before returning; jev_act() attaches its own."""
    return _skill.choose(goal)


def jev_act():
    """Execute the pending jev_choose decision on the current tab, at most once.
    The decision is consumed before any input, so calling jev_act() again cannot repeat a click.
    Raises StalePage if the page changed since jev_choose."""
    return _skill.act()
