"""The browser-jev-harness skill helpers. Offline; the harness and models are mocked."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from jev_ultrafast.browser import StalePage

HELPERS = Path(__file__).parents[1] / "skills" / "browser-jev-harness" / "scripts" / "jev_helpers.py"


@pytest.fixture
def helpers(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # the helpers load .env from cwd upward; keep real keys out of tests
    namespace = {"__name__": "jev_helpers"}
    exec(compile(HELPERS.read_text(), str(HELPERS), "exec"), namespace)
    page = {"url": "u", "title": "t", "actions": [
        {"id": "e1", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 1},
    ]}
    browser = Mock(observe=Mock(return_value=page), settle=Mock(side_effect=lambda p: p))
    namespace["_tab_browser"] = Mock(return_value=browser)
    namespace["_detach"] = Mock()
    namespace["choose"] = Mock(return_value={
        "choice": "e1", "operation": "CLICK", "target": "1", "confidence": 1.0,
        "operation_probabilities": {"CLICK": 1.0}, "latency_ms": 1,
    })
    namespace["browser"] = browser
    return namespace


def test_act_executes_a_decision_at_most_once(helpers):
    helpers["jev_choose"]("Press Go")
    assert helpers["jev_act"]()["executed"] == "e1"
    with pytest.raises(ValueError, match="jev_choose"):
        helpers["jev_act"]()
    assert helpers["browser"].act.call_count == 1


def test_sessions_are_released_even_when_the_page_went_stale(helpers):
    helpers["jev_choose"]("Press Go")
    helpers["browser"].act.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        helpers["jev_act"]()
    # one attach for choose, one for act; both released
    assert helpers["_tab_browser"].call_count == 2
    assert helpers["_detach"].call_count == 2
    with pytest.raises(ValueError, match="jev_choose"):
        helpers["jev_act"]()  # a stale decision is not kept for a retry


def test_choose_releases_its_session_when_the_model_fails(helpers):
    helpers["choose"].side_effect = RuntimeError("Model unavailable")
    with pytest.raises(RuntimeError):
        helpers["jev_choose"]("Press Go")
    helpers["_detach"].assert_called_once()
