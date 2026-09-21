"""Offline tests for listener event processing. No browser or model calls."""

from jev_ultrafast.listener import collect, url_contains

SID = "session-1"


def ev(method, params, session_id=SID):
    return {"method": method, "params": params, "session_id": session_id}


def test_collect_matches_responses_to_their_methods():
    requests = {}
    events = [
        ev("Network.requestWillBeSent", {"requestId": "r1", "request": {"method": "POST"}}),
        ev("Network.requestWillBeSent", {"requestId": "r2", "request": {"method": "GET"}}),
        ev(
            "Network.responseReceived",
            {"requestId": "r1",
             "response": {"url": "https://x/checkout", "status": 200, "mimeType": "application/json"}},
        ),
        ev(
            "Network.responseReceived",
            {"requestId": "r2",
             "response": {"url": "https://x/logo.png", "status": 200, "mimeType": "image/png"}},
        ),
    ]
    out = collect(events, SID, requests)
    assert out[0]["method"] == "POST" and out[0]["url"].endswith("/checkout")
    assert out[1]["method"] == "GET"


def test_collect_ignores_other_sessions():
    events = [
        ev(
            "Network.responseReceived",
            {"requestId": "r1", "response": {"url": "https://x/a", "status": 200}},
            session_id="other",
        )
    ]
    assert collect(events, SID, {}) == []


def test_collect_ignores_non_response_events():
    events = [
        ev("Page.loadEventFired", {}),
        ev("Network.requestWillBeSent", {"requestId": "r1", "request": {"method": "GET"}}),
    ]
    assert collect(events, SID, {}) == []


def test_url_contains():
    match = url_contains("checkout")
    assert match({"url": "https://shop.vn/checkout/123"})
    assert not match({"url": "https://shop.vn/cart"})
