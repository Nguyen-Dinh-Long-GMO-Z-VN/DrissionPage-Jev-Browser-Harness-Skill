"""Independent outcome verification through real network packets.

A DONE choice or DOM text only claims success. The browser-harness daemon
records every CDP event and drain_events() hands them out tagged by session,
so a Network response proves what actually happened — e.g. the checkout POST
returned 200 — without trusting the page.
"""

import time


def collect(events, session_id, requests):
    """Fold drained events into a response list. `requests` maps requestId to
    its method and is shared across calls so requestWillBeSent correlates with
    responseReceived. Returns [{url, status, mimeType, method, requestId}]."""
    out = []
    for ev in events:
        if ev.get("session_id") != session_id:
            continue
        params = ev.get("params", {})
        if ev.get("method") == "Network.requestWillBeSent":
            requests[params.get("requestId")] = params.get("request", {}).get("method")
        elif ev.get("method") == "Network.responseReceived":
            r = params.get("response", {})
            out.append(
                {
                    "url": r.get("url", ""),
                    "status": r.get("status"),
                    "mimeType": r.get("mimeType", ""),
                    "method": requests.get(params.get("requestId")),
                    "requestId": params.get("requestId"),
                }
            )
    return out


def wait_for_response(call, session_id, match, timeout=15):
    """First Network response on `session_id` accepted by match(info) -> info + body.

    `call` is a session-bound CDP caller such as Browser.call; match receives
    {url, status, mimeType, method, requestId}. The response body is fetched on
    the same session and may be None when Chrome has already dropped it.
    Matching is retroactive: the daemon buffers Network events from the moment
    Network.enable ran on the session (Browser enables it at attach), so a
    response observed before this call is still found. Drains the daemon's
    shared event buffer: do not run two waits at once.
    """
    from browser_harness.helpers import drain_events

    call("Network.enable")
    requests = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for info in collect(drain_events(), session_id, requests):
            if match(info):
                try:
                    body = call("Network.getResponseBody", requestId=info["requestId"]).get("body")
                except Exception:
                    body = None
                return {**info, "body": body}
        time.sleep(0.05)
    return None


def url_contains(fragment):
    """Common matcher: any response whose URL contains the fragment."""
    return lambda info: fragment in info["url"]
