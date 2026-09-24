"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(
            fresh=Mock(return_value=True), observe=Mock(return_value=p), settle=Mock(side_effect=lambda page, **_: page)
        ),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


@pytest.mark.parametrize("error", [RuntimeError("Dropdown execution was interrupted"), TimeoutError("CDP")])
def test_browser_error_after_possible_mutation_is_logged_and_blocks_next_choice(runner, error):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].act.side_effect = error
    with pytest.raises(type(error)):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked"
    assert "outcome unknown" in runner.state["reason"]
    assert runner.state["history"][-1]["action"] == "Go"
    assert runner.state["history"][-1]["outcome"] == "unknown"
    with pytest.raises(ValueError, match="run has stopped"):
        runner.command("predict")
    runner.state["browser"].act.assert_called_once()


def _fill_request(actual):
    """A fill through browser_operation whose read-back returns `actual` (or fails when it is an Exception)."""
    def call(method, **params):
        if method == "Runtime.evaluate" and "action.node" in params["expression"]:
            return {"result": {"value": {"x": 1, "y": 1}}}
        if method == "Runtime.evaluate" and "activeElement" in params["expression"]:
            return {"result": {"value": True}}
        if method == "Runtime.evaluate":
            if isinstance(actual, Exception):
                raise actual
            return {"result": {"value": actual}}
        return {}

    return {"operation": "act", "session": "s", "call": call, "text": "Jev  is\nfast",
            "action": {"id": "e2", "kind": "fill", "node": 2}}


@pytest.mark.parametrize(
    ("actual", "expected"),
    [("Jev is fast", "verified"), ("", "unverified"), (None, "unverified"), (RuntimeError("gone"), "unverified")],
)
def test_fill_reports_whether_the_text_was_read_back(monkeypatch, actual, expected):
    import jev_ultrafast.browser as browser

    monkeypatch.setattr(browser.time, "sleep", Mock())
    assert browser_operation(_fill_request(actual)) == {"executed": "e2", "typed": expected}


def test_unverified_text_is_recorded_and_summarized(runner, monkeypatch):
    monkeypatch.setattr(loop, "field_text", Mock(return_value=("book", {"model": "test", "latency_ms": 10})))
    runner.state["decision"] = decision("e1")
    runner.state["browser"].act.return_value = {"executed": "e1", "typed": "unverified"}
    runner.state["reason"] = None
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["typed"] == "unverified"
    assert loop.summarize(runner)["unverified_text"] == ["Search"]


@pytest.fixture
def prefetching(runner, monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("JEV_TEXT_PREFETCH", "1")
    runner.state["decisions"] = [{"text_node": 10}]  # The previous decision's type_text_target.
    return runner


def test_likely_text_node_reads_the_unexecuted_text_head():
    d = {"raw_answers": {"type_text_target": choice(["1"], "1")}}
    assert loop.likely_text_node(d, page()) == 10
    for answer in [None, {"choice": "999"}, {"choice": ["1"]}, "1"]:
        assert loop.likely_text_node({"raw_answers": {"type_text_target": answer}}, page()) is None


def test_prefetched_text_is_used_only_for_the_identical_helper_input(prefetching, monkeypatch):
    runner = prefetching
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner._prefetch_text(runner.state["page"])
    assert helper.call_count == 1
    assert runner.state["text_prefetches"] == 1
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["history"][-1]["text"] == "book"
    assert runner.state["text_calls"][-1]["prefetched"] is True


def test_prefetched_text_for_a_different_input_is_not_used(prefetching, monkeypatch):
    runner = prefetching
    helper = Mock(side_effect=[("stale", {"model": "test", "latency_ms": 10}),
                               ("book", {"model": "test", "latency_ms": 10})])
    monkeypatch.setattr(loop, "field_text", helper)
    runner._prefetch_text(runner.state["page"])
    for future in runner.prefetched.values():
        assert future.result()[0] == "stale"
    runner.state["page"]["text"] = "Different page context"
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2
    assert runner.state["history"][-1]["text"] == "book"


def test_prefetched_missing_value_blocks_without_typing(prefetching, monkeypatch):
    runner = prefetching
    monkeypatch.setattr(loop, "field_text", Mock(side_effect=model.MissingValue("no value for 'Search'")))
    runner._prefetch_text(runner.state["page"])
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked"
    runner.state["browser"].act.assert_not_called()


@pytest.mark.parametrize("change", ["default_off", "no_key", "no_prediction", "not_editable"])
def test_text_prefetch_needs_opt_in_and_an_editable_prediction(prefetching, monkeypatch, change):
    runner = prefetching
    if change == "default_off":
        monkeypatch.delenv("JEV_TEXT_PREFETCH")
    elif change == "no_key":
        monkeypatch.delenv("TEXT_MODEL_API_KEY")
    elif change == "no_prediction":
        runner.state["decisions"] = []
    else:
        runner.state["decisions"] = [{"text_node": 20}]  # The "Go" button.
    helper = Mock()
    monkeypatch.setattr(loop, "field_text", helper)
    runner._prefetch_text(runner.state["page"])
    assert runner.prefetched == {}
    helper.assert_not_called()


def test_a_just_observed_page_skips_the_freshness_read(runner, monkeypatch):
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision()))
    runner.state["status"] = "ready"
    runner._observe()
    runner.command("predict")
    runner.state["browser"].fresh.assert_not_called()
    runner.observed_at -= 1
    runner.command("predict")
    runner.state["browser"].fresh.assert_called_once()


def test_unverified_typing_is_shown_to_the_next_decision(monkeypatch):
    sent = []

    def post(_url, _key, body):
        sent.append(body)
        return {"model": "test", "answers": {"operation": choice(body["questions"]["operation"]["criteria"], "DONE")}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    history = [{"action": "Search", "kind": "fill", "text": "book", "page_changed": False, "typed": "unverified"}]
    model.choose(page(), "Find a book", history)
    assert sent[0]["state"]["recent_actions"] == history


def test_wait_settles_in_the_page_instead_of_sleeping(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.session, b.after_input = "s", None
    b.fresh = Mock(return_value=True)
    monkeypatch.setattr(browser, "browser_operation", Mock(return_value={"executed": "wait"}))
    monkeypatch.setattr(browser.time, "sleep", Mock(side_effect=AssertionError("slept")))
    wait = {"id": "wait", "kind": "wait", "label": "Wait"}
    b.act(wait, page())
    assert b.after_input == wait
    calls = []
    b.call = lambda method, **params: calls.append(params) or {}
    b.observe(screenshot=False)
    assert calls[0]["expression"].startswith(browser.SETTLE_AFTER_INPUT)
    assert calls[0]["awaitPromise"] is True


def test_owned_tab_opens_in_its_own_window(monkeypatch):
    import browser_harness.admin

    import jev_ultrafast.browser as browser

    calls = []

    def cdp(method, **params):
        calls.append((method, params))
        return {"targetId": "t", "sessionId": "s", "result": {"value": "complete"}}

    monkeypatch.setattr(browser_harness.admin, "ensure_daemon", lambda: None)
    monkeypatch.setattr(browser, "cdp", cdp)
    browser.Browser("https://example.test/")
    # A background tab in the user's window may render no frames, leaving opening menus invisible.
    assert calls[0] == ("Target.createTarget", {"url": "about:blank", "newWindow": True, "background": True})


def test_only_proven_progress_resets_the_no_progress_stop():
    """Only page_changed=True counts as progress; False and None do not."""
    assert loop.stalled(
        [
            {"page_changed": False, "kind": "click"},
            {"page_changed": False, "kind": "click"},
            {"page_changed": False, "kind": "click"},
        ]
    )
    assert not loop.stalled(
        [
            {"page_changed": False, "kind": "click"},
            {"page_changed": True, "kind": "click"},
            {"page_changed": False, "kind": "click"},
        ]
    )
    assert loop.stalled(
        [
            {"page_changed": False, "kind": "click"},
            {"page_changed": None, "kind": "click"},
            {"page_changed": False, "kind": "click"},
        ]
    )
    assert not loop.stalled(
        [
            {"page_changed": False, "kind": "click"},
            {"page_changed": False, "kind": "wait"},
            {"page_changed": False, "kind": "click"},
        ]
    )
    assert not loop.stalled([{"page_changed": False, "kind": "click"}])


def test_alternating_failed_observations_still_block_a_stalled_run(runner):
    """Regression test for https://github.com/browser-use/jev-ultrafast/issues/94.

    A failed post-action observation leaves page_changed=None. Alternating
    None with False must still trip the three-repeat no-progress guard
    instead of letting a stuck run spend the whole model-call budget.
    """
    current = runner.state["page"]
    runner.state["browser"].observe.side_effect = [current, StalePage("changed"), current]
    for _ in range(3):
        runner.state["decision"] = decision("e3")
        try:
            runner.command("act", {"fingerprint": current["fingerprint"]})
        except StalePage:
            pass
    assert [entry["page_changed"] for entry in runner.state["history"]] == [False, None, False]
    assert runner.state["status"] == "blocked"


def test_stale_recovery_applies_the_no_progress_stop(runner):
    """The tick StalePage recovery must not revive a stalled run as ready."""
    runner.state["history"] = [
        {"page_changed": False, "kind": "click"},
        {"page_changed": None, "kind": "click"},
        {"page_changed": False, "kind": "click"},
    ]
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "blocked"
    runner.state["browser"].act.assert_not_called()


def test_failed_third_observation_still_blocks_a_stalled_run(runner):
    """The act except-path guard: when the third post-action observation
    itself fails, the run must still stop instead of staying ready."""
    current = runner.state["page"]
    runner.state["browser"].observe.side_effect = [current, current, StalePage("changed")]
    for _ in range(3):
        runner.state["decision"] = decision("e3")
        try:
            runner.command("act", {"fingerprint": current["fingerprint"]})
        except StalePage:
            pass
    assert [entry["page_changed"] for entry in runner.state["history"]] == [False, False, None]
    assert runner.state["status"] == "blocked"


def test_tick_recovery_never_revives_a_stalled_run(runner):
    """A failed recovery observation must not erase the stalled result:
    tick returns blocked without re-enabling decisions."""
    runner.state["history"] = [
        {"page_changed": False, "kind": "click"},
        {"page_changed": None, "kind": "click"},
        {"page_changed": False, "kind": "click"},
    ]
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.state["browser"].observe.side_effect = StalePage("still navigating")
    runner.command("tick")
    assert runner.state["status"] == "blocked"
    runner.state["browser"].act.assert_not_called()


def test_toggle_button_pressed_state_reaches_the_model(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Nonstop only", "node": 30, "role": "button", "pressed": "true",
    })
    sent = []

    def post(_url, _key, body):
        sent.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "click_target": choice(body["questions"]["click_target"]["criteria"], "1"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    model.choose(p, "Show nonstop flights", [])
    assert sent[0]["state"]["elements"][0]["pressed"] == "true"
    assert sent[0]["questions"]["click_target"]["criteria"]["1"]["pressed"] == "true"


def terminal(choice_id, confidence=1.0):
    return {**decision(choice_id), "operation": choice_id, "target": None, "confidence": confidence}


def test_a_doubtful_done_needs_a_second_vote(runner):
    runner.state["decision"] = terminal("DONE", 0.4)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready"
    runner.state["decision"] = terminal("DONE", 0.4)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "done" and runner.state["done_by"] == "model"


def test_a_confident_done_ends_the_run_at_once(runner):
    runner.state["decision"] = terminal("DONE", 0.9)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "done"


def test_an_action_between_doubtful_dones_resets_the_vote(runner):
    runner.state["decision"] = terminal("DONE", 0.4)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision("e3")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = terminal("DONE", 0.4)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready"


def test_a_first_blocked_waits_for_the_page_and_decides_again(runner):
    runner.state["decision"] = terminal("BLOCKED")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready"
    runner.state["browser"].expect_change.assert_called_once()
    runner.state["browser"].observe.assert_called_once()
    runner.state["browser"].act.assert_not_called()
    runner.state["decision"] = terminal("BLOCKED")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked"


def test_done_when_ends_the_run_without_a_model_call(runner, monkeypatch):
    chooser = Mock()
    monkeypatch.setattr(loop, "choose", chooser)
    runner.done_when = lambda page: page["url"].endswith("example.test/")
    runner.state["status"] = "ready"
    runner.state["decision"] = None
    runner.command("tick")
    assert runner.state["status"] == "done" and runner.state["done_by"] == "code"
    chooser.assert_not_called()
    runner.state["browser"].act.assert_not_called()


def test_expect_change_makes_the_next_observation_wait_like_wait():
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.after_input = None
    b.expect_change()
    assert b.after_input["kind"] == "wait"


def segmented_cdp(itype, assigned):
    """Resolve the target as `itype`, then report what the control kept after assignment."""

    def respond(method, **params):
        if method != "Runtime.evaluate":
            return {}
        if "action.node" in params["expression"]:
            return {"result": {"value": {"x": 5, "y": 6, "itype": itype}}}
        return {"result": {"value": assigned}}

    return Mock(side_effect=respond)


def fill_request(text="2026-10-20"):
    return {"operation": "act", "session": "test", "text": text, "action": {"id": "e1", "kind": "fill", "node": 10}}


@pytest.mark.parametrize("itype", ["date", "time", "datetime-local", "month", "week"])
def test_segmented_control_is_assigned_instead_of_typed(monkeypatch, itype):
    import jev_ultrafast.browser as browser

    cdp = segmented_cdp(itype, "2026-10-20")
    monkeypatch.setattr(browser, "cdp", cdp)
    assert browser_operation(fill_request()) == {"executed": "e1", "typed": "verified"}
    methods = [call.args[0] for call in cdp.call_args_list]
    assert "Input.insertText" not in methods and methods.count("Runtime.evaluate") == 2
    assert "2026-10-20" in cdp.call_args_list[-1].kwargs["expression"]


def test_segmented_control_rejecting_the_value_stops_the_action(monkeypatch):
    import jev_ultrafast.browser as browser

    monkeypatch.setattr(browser, "cdp", segmented_cdp("date", ""))
    with pytest.raises(RuntimeError, match="date field rejected"):
        browser_operation(fill_request("October 20 2026"))


def test_plain_text_control_still_receives_real_keystrokes(monkeypatch):
    import jev_ultrafast.browser as browser

    monkeypatch.setattr(browser.time, "sleep", Mock())
    cdp = segmented_cdp("text", "Zurich")
    monkeypatch.setattr(browser, "cdp", cdp)
    assert browser_operation(fill_request("Zurich"))["executed"] == "e1"
    assert "Input.insertText" in [call.args[0] for call in cdp.call_args_list]


def test_text_helper_sees_the_iso_format_of_a_date_field():
    field = {"id": "e1", "kind": "fill", "label": "Departure", "role": "textbox", "value": "", "node": 5,
             "format": "YYYY-MM-DD"}
    assert model.field_context("Fly on 20 Oct 2026", field, page(), [])["field"]["format"] == "YYYY-MM-DD"
    assert "format" not in model.field_context("Find a book", page()["actions"][0], page(), [])["field"]


@pytest.mark.parametrize("lost", [1, 2])
def test_fill_stops_when_focus_moves_before_typing(lost):
    """Checked before Select All and again before insertion; a lost focus is not retried as stale."""

    checks = []

    def call(method, **params):
        if method == "Runtime.evaluate" and "action.node" in params["expression"]:
            return {"result": {"value": {"x": 1, "y": 1, "itype": "text"}}}
        if method == "Runtime.evaluate" and "activeElement" in params["expression"]:
            checks.append(1)
            return {"result": {"value": len(checks) != lost}}
        if method == "Input.insertText":
            raise AssertionError("typed without focus")
        return {}

    request = {"operation": "act", "session": "s", "call": call, "text": "Zurich",
               "action": {"id": "e2", "kind": "fill", "node": 2}}
    with pytest.raises(RuntimeError, match="lost focus"):
        browser_operation(request)
