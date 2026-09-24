"""The DrissionPage backend, offline: a fake tab stands in for DrissionPage."""

from unittest.mock import Mock

import pytest

from jev_ultrafast import browser
from jev_ultrafast.drission import DrissionBrowser, wait_for_response


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
    assert [c.args[0] for c in tab.run_cdp.call_args_list] == [
        "Emulation.setFocusEmulationEnabled", "Page.enable", "Page.addScriptToEvaluateOnNewDocument",
        "Runtime.evaluate",
    ]
    assert tab.run_cdp.call_args_list[0].kwargs == {"enabled": True}
    b.close()
    b.close()
    tab.close.assert_not_called()
    # a borrowed tab gets focus emulation switched back off, once
    disables = [c for c in tab.run_cdp.call_args_list if c.kwargs == {"enabled": False}]
    assert len(disables) == 1


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


def fake_drissionpage(monkeypatch):
    import sys
    import types

    options = Mock()
    options.set_address.return_value = options
    options.headless.return_value = options
    options.existing_only.return_value = options
    module = types.SimpleNamespace(Chromium=Mock(return_value="chromium"), ChromiumOptions=Mock(return_value=options))
    monkeypatch.setitem(sys.modules, "DrissionPage", module)
    return module, options


def test_connect_launches_on_the_port_when_nothing_listens(monkeypatch):
    from jev_ultrafast import drission

    module, _ = fake_drissionpage(monkeypatch)
    monkeypatch.setattr(drission, "running_user_agent", lambda address: None)
    assert drission.connect(9555) == "chromium"
    module.Chromium.assert_called_once_with("127.0.0.1:9555")


@pytest.mark.parametrize(("agent", "headless"), [("Mozilla Chrome/153", False), ("Mozilla HeadlessChrome/153", True)])
def test_connect_attaches_to_a_running_chrome_matching_its_headless_mode(monkeypatch, agent, headless):
    from jev_ultrafast import drission

    module, options = fake_drissionpage(monkeypatch)
    monkeypatch.setattr(drission, "running_user_agent", lambda address: agent)
    drission.connect(address="127.0.0.1:9222")
    options.set_address.assert_called_once_with("127.0.0.1:9222")
    options.headless.assert_called_once_with(headless)
    options.existing_only.assert_called_once_with()
    module.Chromium.assert_called_once_with(options)


def test_running_user_agent_is_none_when_nothing_answers():
    from jev_ultrafast.drission import running_user_agent

    assert running_user_agent("127.0.0.1:1", timeout=0.2) is None
