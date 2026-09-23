"""The DrissionPage backend, offline: a fake tab stands in for DrissionPage."""

from unittest.mock import Mock

import pytest
from jev_ultrafast.drission import DrissionBrowser, wait_for_response

from jev_ultrafast import browser


class ContextLostError(Exception):
    pass


def make_tab(**overrides):
    tab = Mock(tab_id="tab-1", run_cdp=Mock(return_value={}))
    tab.configure_mock(**overrides)
    return tab


def test_from_tab_enables_focus_and_leaves_the_tab_open():
    tab = make_tab()
    b = DrissionBrowser.from_tab(tab)
    assert (b.target, b.after_input) == (None, None)
    assert [c.args[0] for c in tab.run_cdp.call_args_list] == ["Emulation.setFocusEmulationEnabled"]
    b.close()
    tab.close.assert_not_called()


def test_owned_tab_is_closed_once():
    tab = make_tab()
    b = DrissionBrowser(tab, owned=True, metrics=True)
    assert tab.run_cdp.call_args_list[0].args[0] == "Emulation.setDeviceMetricsOverride"
    b.close()
    b.close()
    tab.close.assert_called_once()


def test_context_loss_is_a_stale_page():
    tab = make_tab()
    b = DrissionBrowser.from_tab(tab)
    tab.run_cdp.side_effect = ContextLostError("gone")
    with pytest.raises(browser.StalePage):
        b.call("Runtime.evaluate", expression="1")


def test_other_errors_propagate():
    tab = make_tab()
    b = DrissionBrowser.from_tab(tab)
    tab.run_cdp.side_effect = KeyError("boom")
    with pytest.raises(KeyError):
        b.call("Runtime.evaluate", expression="1")


def test_observe_and_act_run_over_the_tab_not_browser_harness(monkeypatch):
    monkeypatch.setattr(browser, "cdp", Mock(side_effect=AssertionError("Browser Harness must not be used")))
    tab = make_tab()
    b = DrissionBrowser.from_tab(tab)
    tab.run_cdp.return_value = {"result": {"value": {"url": "u", "text": "", "actions": [], "scroll": 0}}}
    page = b.observe(screenshot=False)
    assert page["url"] == "u"
    assert tab.run_cdp.call_args.args[0] == "Runtime.evaluate"


def test_wait_for_response_shapes_the_packet():
    packet = Mock(url="http://x/api", method="POST")
    packet.response = Mock(status=200, headers={"content-type": "application/json"}, body={"ok": True})
    tab = make_tab()
    tab.listen.listening = True
    tab.listen.wait.return_value = packet
    assert wait_for_response(tab, timeout=1) == {
        "url": "http://x/api",
        "status": 200,
        "method": "POST",
        "mimeType": "application/json",
        "body": {"ok": True},
    }
    tab.listen.start.assert_not_called()


def test_wait_for_response_returns_none_on_timeout_and_starts_listening_when_idle():
    tab = make_tab()
    tab.listen.listening = False
    tab.listen.wait.return_value = False
    assert wait_for_response(tab, "checkout", timeout=1) is None
    tab.listen.start.assert_called_once_with("checkout")
