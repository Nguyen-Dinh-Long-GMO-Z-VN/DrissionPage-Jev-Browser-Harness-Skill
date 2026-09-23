"""The skill's three operations, independent of the browser backend.

A backend supplies `open_browser(url, **target)` returning a `Browser`, and `release(browser)` to close a
tab it opened or detach from one it borrowed. `jev_helpers.py` (Browser Harness) and `jev_drission.py`
(DrissionPage) each build one `Skill`.
"""

from . import agent as loop
from .browser import StalePage
from .model import MissingValue, choose, field_context, field_text


class Skill:
    def __init__(self, open_browser, release):
        self.open_browser = open_browser
        self.release = release
        self.pending = None

    def run(self, goal, url=None, max_steps=20, **target):
        """Run the full loop until done or blocked. `url` opens (and later closes) a dedicated tab.
        `max_steps` caps executed actions; model calls are capped at twice that."""
        loop.warm_connections()  # overlap TLS setup with the page load
        browser = self.open_browser(url, **target)
        try:
            agent = loop.Agent.attach(browser, goal, max_steps=max_steps)
            for _ in agent.run():
                pass
            return loop.summarize(agent)
        finally:
            self.release(browser)

    def choose(self, goal, **target):
        """Observe the current tab and return Jev's choice without executing it."""
        browser = self.open_browser(None, **target)
        try:
            page = browser.settle(browser.observe(screenshot=False))
            decision = choose(page, goal, [])
        finally:
            self.release(browser)
        self.pending = dict(page=page, decision=decision, goal=goal, target=target)
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

    def act(self):
        """Execute the pending decision at most once. It is consumed before any input, so a second call
        cannot repeat a click. TYPE_TEXT still goes through the small text model; nothing is typed by guess."""
        if self.pending is None:
            raise ValueError("No pending decision. Call jev_choose(goal) first.")
        pending, self.pending = self.pending, None
        page, decision, goal = pending["page"], pending["decision"], pending["goal"]
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
        browser = self.open_browser(None, **pending["target"])
        try:
            result = browser.act(action, page, text=text)
        except StalePage:
            raise  # Rejected before any input, so choosing again is safe.
        except Exception as error:
            # Input may already have reached the page and the decision is consumed: never blindly act again.
            raise RuntimeError(
                f"{action['label']}: outcome unknown after a browser error; inspect the page before continuing."
            ) from error
        finally:
            self.release(browser)
        return {"executed": action["id"], "label": action["label"], "text": text, "typed": (result or {}).get("typed")}
