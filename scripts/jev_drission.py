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

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from jev_ultrafast.drission import DrissionBrowser, connect  # noqa: E402
from jev_ultrafast.env import load_env  # noqa: E402
from jev_ultrafast.skill import Skill  # noqa: E402

load_env(HERE.parent)  # Jev's own variables only; anything already exported wins


def _open(url=None, port=9222):
    """A new background tab at `url` (owned, closed on release), or the latest tab (borrowed)."""
    chromium = connect(port)
    if url is not None:
        return DrissionBrowser.open(chromium, url)
    return DrissionBrowser.from_tab(chromium.latest_tab)


_skill = Skill(_open, DrissionBrowser.close)


def jev_run(goal, url=None, max_steps=20, port=9222):
    """Run the full Jev loop and return a result dict. `max_steps` caps executed actions
    (model calls are capped at twice that). With url= the run uses its own tab and closes it."""
    return _skill.run(goal, url, max_steps, port=port)


def jev_choose(goal, port=9222):
    """Observe the current tab and return Jev's choice without executing it."""
    return _skill.choose(goal, port=port)


def jev_act():
    """Execute the pending jev_choose decision, at most once."""
    return _skill.act()
