"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from .browser import Browser, StalePage
from .model import MissingValue, action_space, choose, field_context, field_text, warm_connections
from .questions import MAX_STEPS


def initial_state(browser, goal, page, *, record=False):
    return dict(
        browser=browser,
        goal=goal,
        page=page,
        decision=None,
        history=[],
        status="ready",
        plan=[goal],
        plan_index=0,
        decisions=[],
        text_calls=[],
        elapsed_ms=0,
        started_at=None,
        record=record,
        reason=None,
    )


def summarize(agent):
    """The compact result a skill returns: outcome, page, and cost of the run."""
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


class Agent:
    max_steps = MAX_STEPS

    def __init__(self, url, goals, *, record_dir=None, screenshots=False, max_steps=MAX_STEPS):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        self.pending_text = None
        self.max_steps = max_steps
        warm_connections()  # overlaps TLS setup with page load
        self.browser = Browser(url)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = initial_state(self.browser, task, page, record=bool(self.record_dir))
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    @classmethod
    def attach(cls, browser, goal, *, screenshots=False, max_steps=MAX_STEPS):
        """Drive an already-attached tab. The caller owns the browser session and detaches it."""
        task = goal.strip()
        if not task:
            raise ValueError("Supply a task")
        agent = cls.__new__(cls)
        warm_connections()
        agent.pending_text = None
        agent.max_steps = max_steps
        agent.browser = browser
        agent.record_dir = None
        agent.screenshots = screenshots
        agent.state = initial_state(browser, task, browser.observe(screenshot=screenshots))
        return agent

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def command(self, name, body=None):
        if name == "tick":
            self._tick()
        elif name == "predict":
            self._predict()
        elif name == "act":
            self._act(body or {})
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def _tick(self):
        state = self.state
        try:
            self._predict()
            self._act({"fingerprint": state["page"]["fingerprint"]})
        except StalePage:
            state["decision"] = None
            state["status"] = "ready"
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)

    def _predict(self):
        state = self.state
        if not state["browser"]:
            raise ValueError("Start a demo first")
        if state["started_at"] is None:
            state["started_at"] = time.perf_counter()
        if not state["browser"].fresh(state["page"]):
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
        state["page"] = state["browser"].settle(state["page"], screenshot=self.screenshots)
        state["decision"] = None
        if state["status"] in {"done", "blocked"}:
            raise ValueError("This run has stopped. Start a fresh demo.")
        if len(state["decisions"]) >= self.max_steps * 2:
            raise ValueError("Reached the demo's model-call budget")
        state["decision"] = choose(state["page"], state["goal"], state["history"])
        state["decisions"].append(
            {
                **state["decision"],
                "fingerprint": state["page"]["fingerprint"],
                "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
            }
        )
        state["status"] = "predicted"

    def _act(self, body):
        state = self.state
        decision, page = state["decision"], state["page"]
        if not decision or body.get("fingerprint") != page["fingerprint"]:
            raise ValueError("Observe and choose before acting")
        # Consume once, before any mutation or model call. A retry cannot double-click.
        state["decision"] = None
        selected = decision["choice"]
        if selected in {"DONE", "BLOCKED"}:
            if not state["browser"].fresh(page):
                state["status"] = "ready"
                raise StalePage("Page changed since the decision. Choose again.")
            state["status"] = "done" if selected == "DONE" else "blocked"
            state["plan_index"] = int(selected == "DONE")
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            return
        action = next(a for a in page["actions"] if a["id"] == selected)
        if len(state["history"]) >= self.max_steps:
            state["status"] = "blocked"
            raise ValueError(f"Stopped at the {self.max_steps}-action demo budget")
        text, helper = None, None
        if action["kind"] == "fill":
            if not state["browser"].fresh(page):
                raise StalePage("Page changed before text generation. Choose again.")
            context = field_context(state["goal"], action, page, state["history"])
            if self.pending_text and self.pending_text[0] == context:
                _, text, helper = self.pending_text
            else:
                try:
                    text, helper = field_text(context)
                except MissingValue as missing:
                    # The goal lacks this value. Stop cleanly and say which field needs it.
                    state["status"] = "blocked"
                    state["reason"] = str(missing)
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    return
                self.pending_text = (context, text, helper)
                state["text_calls"].append({**helper, "field": action["label"], "value": text})

        def record(**extra):
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                    **extra,
                }
            )

        # Browser.act checks freshness immediately before input, including after text generation.
        try:
            state["browser"].act(action, page, text=text)
        except StalePage:
            raise  # Rejected before any input; a fresh decision is safe.
        except Exception:
            # The input may already have reached the page. Log it and stop: never choose again blindly.
            self.pending_text = None
            record(outcome="unknown")
            state["status"] = "blocked"
            state["reason"] = f"{action['label']}: outcome unknown after a browser error; inspect the page first."
            raise
        self.pending_text = None
        # Record execution before observing. A stale post-action observation must not erase the action.
        record()
        state["page"] = state["browser"].observe(screenshot=self.screenshots)
        state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
        state["history"][-1].update(
            page_changed=state["page"]["fingerprint"] != page["fingerprint"],
            url=state["page"]["url"],
            elapsed_ms=state["elapsed_ms"],
        )
        if state["record"]:
            (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                base64.b64decode(state["page"]["screenshot"])
            )
        repeated = state["history"][-3:]
        state["status"] = (
            "blocked"
            if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
            else "ready"
        )

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
