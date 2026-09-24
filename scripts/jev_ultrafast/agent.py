"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .browser import Browser, StalePage
from .model import MissingValue, action_space, choose, field_context, field_text, warm_connections
from .questions import MAX_STEPS

# JEV_TEXT_PREFETCH=1 generates text for one likely field while TypeSafe chooses (see _prefetch_text).
PREFETCH = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jev-text")
# An observation this recent needs no freshness read before choosing; act() still checks before input.
RECENT_OBSERVATION_S = 0.05
# A DONE below this operation confidence is accepted only when chosen twice in a row.
DONE_MIN_CONFIDENCE = 0.7


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
        text_prefetches=0,
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
        "done_by": s.get("done_by"),
        "model_calls": len(s["decisions"]),
        "text_calls": len(s["text_calls"]),
        "text_prefetches": s.get("text_prefetches", 0),
        "elapsed_ms": s["elapsed_ms"],
        "history": [h["action"] for h in s["history"]],
        # A DONE choice is not proof: name every field whose text could not be read back.
        "unverified_text": [h["action"] for h in s["history"] if h.get("typed") == "unverified"],
    }


STALLED = "No progress after three actions; inspect the page before continuing."


def stalled(history):
    """The last three actions show no proven progress.

    Only an explicit page_changed=True counts. An unobserved outcome (None, after a failed post-action
    observation) must not reset the check, or a stuck run spends its whole model-call budget.
    """
    recent = history[-3:]
    return len(recent) == 3 and all(h.get("page_changed") is not True and h["kind"] != "wait" for h in recent)


def likely_text_node(decision, page):
    """The node the type_text_target head would fill. It only picks what to prefetch; it never executes."""
    answer = (decision.get("raw_answers") or {}).get("type_text_target")
    targets = action_space(page["actions"])[1].get("TYPE_TEXT", {})
    choice = answer.get("choice") if isinstance(answer, dict) else None
    action = targets.get(choice) if isinstance(choice, str) else None
    return action["node"] if action else None


class Agent:
    max_steps = MAX_STEPS
    done_when = None
    done_min_confidence = DONE_MIN_CONFIDENCE
    terminal_vote = None

    def __init__(self, url, goals, *, record_dir=None, screenshots=False, max_steps=MAX_STEPS, done_when=None):
        """`done_when(page)` lets code own completion: when it returns true the run ends as done, with no model
        call. Use it for an outcome the caller can check itself; the model's DONE is never evidence."""
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        self.pending_text = None
        self.max_steps = max_steps
        self.done_when = done_when
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
    def attach(cls, browser, goal, *, screenshots=False, max_steps=MAX_STEPS, done_when=None):
        """Drive an already-attached tab. The caller owns the browser session and detaches it."""
        task = goal.strip()
        if not task:
            raise ValueError("Supply a task")
        agent = cls.__new__(cls)
        warm_connections()
        agent.pending_text = None
        agent.max_steps = max_steps
        agent.done_when = done_when
        agent.browser = browser
        agent.record_dir = None
        agent.screenshots = screenshots
        agent.state = initial_state(browser, task, browser.observe(screenshot=screenshots))
        return agent

    def _observe(self):
        self.state["page"] = self.state["browser"].observe(screenshot=self.screenshots)
        self.observed_at = time.perf_counter()
        return self.state["page"]

    def _prefetch_text(self, page):
        """Opt-in (JEV_TEXT_PREFETCH=1): start the text helper while TypeSafe chooses, for one field.

        The field is the previous decision's type_text_target answer, when that node is still editable here.
        The helper gets the exact input _act would send, and only an identical input can consume the result, so
        a prefetched value is the value TYPE_TEXT would have generated. An unused result is dropped at the next
        decision. This costs at most one extra helper call per decision, so it stays off by default: a helper on
        a small rate limit (free tiers allow a few requests per minute) would spend its quota on guesses.
        """
        self.prefetched = {}
        state = self.state
        if not (os.environ.get("TEXT_MODEL_API_KEY") and os.environ.get("JEV_TEXT_PREFETCH") == "1"):
            return
        node = state["decisions"][-1].get("text_node") if state["decisions"] else None
        action = next((a for a in page["actions"] if a["kind"] == "fill" and a["node"] == node), None)
        if action is None:
            return
        context = field_context(state["goal"], action, page, state["history"])
        self.prefetched[json.dumps(context, sort_keys=True)] = PREFETCH.submit(field_text, context)
        state["text_prefetches"] = state.get("text_prefetches", 0) + 1

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
            if state["status"] == "done":
                return  # done_when accepted the page; nothing to execute
            self._act({"fingerprint": state["page"]["fingerprint"]})
        except StalePage:
            state["decision"] = None
            # Decide before recovering: a failed recovery observation must not revive a stalled run.
            if stalled(state["history"]):
                state["status"], state["reason"] = "blocked", STALLED
                return
            state["status"] = "ready"
            self._observe()
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)

    def _predict(self):
        state = self.state
        if not state["browser"]:
            raise ValueError("Start a demo first")
        if state["started_at"] is None:
            state["started_at"] = time.perf_counter()
        recent = time.perf_counter() - getattr(self, "observed_at", float("-inf")) < RECENT_OBSERVATION_S
        if not recent and not state["browser"].fresh(state["page"]):
            self._observe()
        state["page"] = state["browser"].settle(state["page"], screenshot=self.screenshots)
        state["decision"] = None
        if state["status"] in {"done", "blocked"}:
            raise ValueError("This run has stopped. Start a fresh demo.")
        if self.done_when and self.done_when(state["page"]):
            state["status"], state["done_by"] = "done", "code"
            state["plan_index"] = 1
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            return
        if len(state["decisions"]) >= self.max_steps * 2:
            raise ValueError("Reached the demo's model-call budget")
        self._prefetch_text(state["page"])
        state["decision"] = choose(state["page"], state["goal"], state["history"])
        state["decisions"].append(
            {
                **state["decision"],
                "text_node": likely_text_node(state["decision"], state["page"]),
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
            vote, self.terminal_vote = self.terminal_vote, selected
            if selected == "DONE" and decision["confidence"] < self.done_min_confidence and vote != "DONE":
                state["status"] = "ready"  # A doubtful DONE must be chosen again before it ends the run.
                return
            if selected == "BLOCKED" and vote != "BLOCKED":
                # A first BLOCKED is often a half-loaded page or a menu still opening: let the page change
                # (WAIT's settle: up to 1.5 s, 0.5 s if nothing moves), then decide again.
                state["browser"].expect_change()
                self._observe()
                state["status"] = "ready"
                return
            state["status"] = "done" if selected == "DONE" else "blocked"
            state["done_by"] = "model"
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
            prefetched = getattr(self, "prefetched", {}).pop(json.dumps(context, sort_keys=True), None)
            if self.pending_text and self.pending_text[0] == context:
                _, text, helper = self.pending_text
            else:
                try:
                    if prefetched:
                        text, helper = prefetched.result()
                        helper = {**helper, "prefetched": True}
                    else:
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
            result = state["browser"].act(action, page, text=text)
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
        self.terminal_vote = None
        # Record execution before observing. A stale post-action observation must not erase the action.
        record(typed=(result or {}).get("typed"))
        try:
            self._observe()
        except Exception:
            # The action stays logged with page_changed=None, which still counts toward the no-progress stop.
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            if stalled(state["history"]):
                state["status"], state["reason"] = "blocked", STALLED
            raise
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
        if stalled(state["history"]):
            state["status"], state["reason"] = "blocked", STALLED
        else:
            state["status"] = "ready"

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
