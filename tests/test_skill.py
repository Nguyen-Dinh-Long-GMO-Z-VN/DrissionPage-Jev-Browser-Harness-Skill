"""The backend-independent skill operations. Offline; the browser and models are mocked."""

from unittest.mock import Mock

import pytest
from jev_ultrafast import skill as skill_module
from jev_ultrafast.browser import StalePage
from jev_ultrafast.skill import Skill

PAGE = {
    "url": "u",
    "title": "t",
    "actions": [{"id": "e1", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 1}],
}
DECISION = {
    "choice": "e1",
    "operation": "CLICK",
    "target": "1",
    "confidence": 1.0,
    "operation_probabilities": {"CLICK": 1.0},
    "latency_ms": 1,
}


@pytest.fixture
def skill(monkeypatch):
    browser = Mock(observe=Mock(return_value=PAGE), settle=Mock(side_effect=lambda p: p))
    monkeypatch.setattr(skill_module, "choose", Mock(return_value=dict(DECISION)))
    s = Skill(Mock(return_value=browser), Mock())
    s.browser = browser
    return s


def test_act_executes_a_decision_at_most_once(skill):
    skill.choose("Press Go")
    assert skill.act()["executed"] == "e1"
    with pytest.raises(ValueError, match="jev_choose"):
        skill.act()
    assert skill.browser.act.call_count == 1


def test_sessions_are_released_even_when_the_page_went_stale(skill):
    skill.choose("Press Go")
    skill.browser.act.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        skill.act()
    # one attach for choose, one for act; both released
    assert skill.open_browser.call_count == 2
    assert skill.release.call_count == 2
    with pytest.raises(ValueError, match="jev_choose"):
        skill.act()  # a stale decision is not kept for a retry


def test_choose_releases_its_session_when_the_model_fails(skill, monkeypatch):
    monkeypatch.setattr(skill_module, "choose", Mock(side_effect=RuntimeError("Model unavailable")))
    with pytest.raises(RuntimeError):
        skill.choose("Press Go")
    skill.release.assert_called_once()


def test_done_and_blocked_execute_nothing(skill, monkeypatch):
    monkeypatch.setattr(skill_module, "choose", Mock(return_value={**DECISION, "choice": "DONE"}))
    skill.choose("Press Go")
    assert skill.act() == {"executed": "DONE"}
    skill.browser.act.assert_not_called()
    assert skill.open_browser.call_count == 1  # act() did not reattach


def test_missing_text_value_blocks_without_typing(skill, monkeypatch):
    fill = {"id": "e2", "kind": "fill", "label": "Phone", "role": "textbox", "value": "", "node": 2}
    page = {**PAGE, "text": "", "actions": [fill]}
    skill.browser.observe.return_value = page
    monkeypatch.setattr(skill_module, "choose", Mock(return_value={**DECISION, "choice": "e2"}))
    monkeypatch.setattr(skill_module, "field_text", Mock(side_effect=skill_module.MissingValue("no phone")))
    skill.choose("Sign up")
    assert skill.act() == {"executed": None, "blocked": "no phone"}
    skill.browser.act.assert_not_called()


def test_run_forwards_target_and_releases(skill, monkeypatch):
    agent = Mock()
    agent.run.return_value = iter([{}, {}])
    attach = Mock(return_value=agent)
    monkeypatch.setattr(skill_module.loop.Agent, "attach", attach)
    monkeypatch.setattr(skill_module.loop, "summarize", Mock(return_value={"status": "done"}))
    assert skill.run("goal", "http://x", 5, port=9333) == {"status": "done"}
    skill.open_browser.assert_called_once_with("http://x", port=9333)
    attach.assert_called_once_with(skill.browser, "goal", max_steps=5)
    skill.release.assert_called_once_with(skill.browser)


def test_run_releases_when_the_loop_fails(skill, monkeypatch):
    monkeypatch.setattr(skill_module.loop.Agent, "attach", Mock(side_effect=StalePage("gone")))
    with pytest.raises(StalePage):
        skill.run("goal")
    skill.release.assert_called_once_with(skill.browser)


def test_browser_error_after_possible_input_reports_unknown_outcome_once(skill):
    skill.choose("Press Go")
    skill.browser.act.side_effect = TimeoutError("CDP")
    with pytest.raises(RuntimeError, match="outcome unknown"):
        skill.act()
    skill.release.assert_called()
    with pytest.raises(ValueError, match="jev_choose"):
        skill.act()
    assert skill.browser.act.call_count == 1


def test_act_reports_whether_typed_text_was_read_back(skill, monkeypatch):
    fill = {"id": "e2", "kind": "fill", "label": "Post", "role": "textbox", "value": "", "node": 2}
    skill.browser.observe.return_value = {**PAGE, "text": "", "actions": [fill]}
    monkeypatch.setattr(skill_module, "choose", Mock(return_value={**DECISION, "choice": "e2"}))
    monkeypatch.setattr(skill_module, "field_text", Mock(return_value=("hello", {})))
    skill.browser.act.return_value = {"executed": "e2", "typed": "unverified"}
    skill.choose("Write a post")
    assert skill.act()["typed"] == "unverified"
