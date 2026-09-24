"""Offline contracts for the model transport and the attach entry points. No paid APIs."""

from unittest.mock import Mock

import httpx
import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import browser, model


def response(status=200, body=None):
    return httpx.Response(status, json=body or {}, request=httpx.Request("POST", "https://x.test"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(model.time, "sleep", lambda _s: None)


def test_transient_status_is_retried_then_succeeds(monkeypatch):
    post = Mock(side_effect=[response(429), response(503), response(200, {"ok": True})])
    monkeypatch.setattr(model.CLIENT, "post", post)
    assert model.post_json("https://x.test", "k", {}) == {"ok": True}
    assert post.call_count == 3


def test_connection_error_is_retried_then_reported(monkeypatch):
    post = Mock(side_effect=httpx.ConnectError("down"))
    monkeypatch.setattr(model.CLIENT, "post", post)
    with pytest.raises(RuntimeError, match="no action executed"):
        model.post_json("https://x.test", "k", {})
    assert post.call_count == 3


def test_read_timeout_is_not_retried(monkeypatch):
    post = Mock(side_effect=httpx.ReadTimeout("slow"))
    monkeypatch.setattr(model.CLIENT, "post", post)
    with pytest.raises(RuntimeError, match="no action executed"):
        model.post_json("https://x.test", "k", {})
    assert post.call_count == 1


def test_provider_error_reports_status_without_retry(monkeypatch):
    post = Mock(return_value=response(401, {"error": "bad key"}))
    monkeypatch.setattr(model.CLIENT, "post", post)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        model.post_json("https://x.test", "k", {})
    assert post.call_count == 1


def test_action_space_groups_targets_by_operation():
    actions = [
        {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 1},
        {"id": "e2", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 2},
        {"id": "wait", "kind": "wait", "label": "Wait"},
    ]
    elements, targets, controls = model.action_space(actions)
    assert [e["index"] for e in elements] == ["1", "2"]
    assert set(targets) == {"TYPE_TEXT", "CLICK"}
    assert set(controls) == {"WAIT"}
    assert targets["CLICK"]["2"]["id"] == "e2"


def test_text_defaults_match_documented_configuration(monkeypatch):
    for name in ("TEXT_MODEL_BASE_URL", "TEXT_MODEL", "TEXT_MODEL_REASONING"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text": "x"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    model.field_text({"goal": "g"})
    url, _key, body = post.call_args.args
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert body["model"] == "inception/mercury-2.5"


def test_browser_from_session_enables_focus_and_network(monkeypatch):
    cdp = Mock(return_value={})
    monkeypatch.setattr(browser, "cdp", cdp)
    b = browser.Browser.from_session("session-1")
    assert (b.session, b.target, b.after_input) == ("session-1", None, None)
    methods = [c.args[0] for c in cdp.call_args_list]
    # Request tracking is installed for later documents and for the current one.
    assert methods == ["Emulation.setFocusEmulationEnabled", "Network.enable", "Page.enable",
                       "Page.addScriptToEvaluateOnNewDocument", "Runtime.evaluate"]
    assert all(c.kwargs["session_id"] == "session-1" for c in cdp.call_args_list)
    b.close()  # attached tabs are not owned; nothing to close
    assert cdp.call_count == 5


def test_agent_attach_observes_once_and_reuses_the_browser():
    page = {"url": "u", "title": "t", "text": "", "actions": [], "fingerprint": "f"}
    b = Mock(observe=Mock(return_value=page))
    agent = loop.Agent.attach(b, "  Find a book  ")
    b.observe.assert_called_once_with(screenshot=False)
    assert agent.state["goal"] == "Find a book"
    assert agent.state["status"] == "ready"
    assert agent.state["browser"] is b
    assert agent.record_dir is None


def test_agent_attach_rejects_an_empty_goal():
    with pytest.raises(ValueError, match="Supply a task"):
        loop.Agent.attach(Mock(), "   ")


def test_quota_reason_at_the_end_of_a_long_error_is_kept(monkeypatch):
    message = "You exceeded your current quota. " + "See docs. " * 40 + "Quota exceeded: free_tier, limit: 20"
    post = Mock(return_value=response(400, [{"error": {"message": message}}]))
    monkeypatch.setattr(model.CLIENT, "post", post)
    with pytest.raises(RuntimeError, match="limit: 20"):
        model.post_json("https://x.test", "k", {})


def test_null_text_is_a_missing_value_not_a_guess(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": '{"text":null}'}}]}))
    with pytest.raises(model.MissingValue, match="'Phone'.*nothing typed"):
        model.field_text({"goal": "Book it", "field": {"label": "Phone"}})


def test_missing_typesafe_key_is_reported_before_any_request(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    page = {"url": "u", "title": "t", "text": "", "actions": []}
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        model.choose(page, "goal", [])
    post.assert_not_called()


def test_missing_value_blocks_the_run_with_a_reason(monkeypatch):
    page = {
        "url": "u", "title": "t", "text": "", "fingerprint": "f",
        "actions": [{"id": "e1", "kind": "fill", "label": "Phone", "role": "textbox", "value": "", "node": 1}],
    }
    b = Mock(observe=Mock(return_value=page), fresh=Mock(return_value=True),
             settle=Mock(side_effect=lambda p, **_: p))
    agent = loop.Agent.attach(b, "Book a table")
    decision = {"choice": "e1", "operation": "TYPE_TEXT", "target": "1", "confidence": 1.0,
                "probabilities": {"e1": 1.0}, "latency_ms": 1, "usage": {}}
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision))
    monkeypatch.setattr(loop, "field_text", Mock(side_effect=model.MissingValue("no value for 'Phone'")))
    states = list(agent.run())
    assert states[-1]["status"] == "blocked"
    assert "Phone" in states[-1]["reason"]
    b.act.assert_not_called()


def test_agent_max_steps_caps_model_calls():
    b = Mock(observe=Mock(return_value={"actions": [], "fingerprint": "f"}))
    agent = loop.Agent.attach(b, "goal", max_steps=2)
    agent.state["decisions"] = [{}] * 4
    agent.state["started_at"] = 0
    with pytest.raises(ValueError, match="model-call budget"):
        agent.command("predict")


class Settling:
    """A browser whose page keeps changing for the first `churn` freshness checks."""

    def __init__(self, churn):
        self.churn, self.observed = churn, 0

    def fresh(self, _page):
        self.churn -= 1
        return self.churn < 0

    def observe(self, screenshot=False):
        self.observed += 1
        return {"age": 100, "n": self.observed}


def test_settle_skips_documents_that_are_not_young():
    b = Settling(churn=99)
    page = {"age": 10_000}
    assert browser.Browser.settle(b, page) is page
    assert b.observed == 0


def test_settle_reobserves_while_a_young_page_churns():
    b = Settling(churn=2)
    page = browser.Browser.settle(b, {"age": 100})
    assert page == {"age": 100, "n": 2}


def test_settle_gives_up_at_the_cap():
    b = Settling(churn=10_000)
    started = model.time.perf_counter()
    browser.Browser.settle(b, {"age": 100}, cap=0.1)
    assert model.time.perf_counter() - started < 0.3
