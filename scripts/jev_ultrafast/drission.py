"""DrissionPage backend: the same loop over a DrissionPage tab instead of Browser Harness.

Only the transport changes. DrissionPage carries each CDP call (`tab.run_cdp`), and the snapshot, guards,
hit-testing, and execution stay in `browser.py`. DrissionPage connects to Chrome over its debugging port and
launches Chrome on that port when nothing is listening.

DrissionPage is a separate package with its own license (personal, learning, and non-profit use; commercial
use needs the author's authorization). It is an optional dependency and is imported only here.
"""

import time

from .browser import Browser, StalePage, browser_operation

# DrissionPage errors raised when the page navigates away or detaches during a call.
STALE_ERRORS = {"ContextLostError", "PageDisconnectedError"}


def connect(port=9222, address=None):
    """A DrissionPage `Chromium` on `port` (or `address` such as '127.0.0.1:9222')."""
    from DrissionPage import Chromium

    return Chromium(address or f"127.0.0.1:{port}")


class DrissionBrowser(Browser):
    """A `Browser` whose CDP calls go through a DrissionPage tab.

    `DrissionBrowser.open(browser, url)` owns a new background tab and closes it on `close()`.
    `DrissionBrowser.from_tab(tab)` wraps a tab the caller keeps; `close()` leaves it alone.
    """

    def __init__(self, tab, *, owned=False, metrics=False):
        self.tab = tab
        self.target = tab.tab_id if owned else None
        self.session = tab.tab_id  # a label only; DrissionPage owns the real CDP session
        self.after_input = None
        if metrics:
            self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in a background tab without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)

    @classmethod
    def open(cls, browser, url):
        """Load `url` in a new background tab that this object owns."""
        tab = browser.new_tab("about:blank", background=True)
        page = cls(tab, owned=True, metrics=True)
        page.call("Page.navigate", url=url)
        page._wait_ready()
        return page

    @classmethod
    def from_tab(cls, tab):
        return cls(tab)

    def call(self, method, **params):
        try:
            return self.tab.run_cdp(method, **params)
        except Exception as error:
            if type(error).__name__ in STALE_ERRORS:
                # The document went away mid-call, the same condition Browser reports as a stale page.
                raise StalePage("Document changed during the call") from error
            raise

    def _operate(self, request):
        return browser_operation({**request, "call": self.call})

    def _wait_ready(self, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.evaluate("document.readyState") == "complete":
                    return
            except StalePage:
                pass
            time.sleep(0.02)

    def close(self):
        if self.target:
            self.tab.close()
            self.target = None


def wait_for_response(tab, targets=True, timeout=15):
    """The next network response DrissionPage catches on `tab`, or None.

    Call `tab.listen.start(targets)` (for example a URL fragment) before the action under test. If it is not
    listening yet this starts now and sees only later traffic; unlike the Browser Harness listener it is not
    retroactive. Returns the same shape as
    `listener.wait_for_response`: {url, status, method, mimeType, body}.
    """
    if not tab.listen.listening:
        tab.listen.start(targets)
    packet = tab.listen.wait(timeout=timeout, raise_err=False)
    if not packet:
        return None
    response = packet.response
    return {
        "url": packet.url,
        "status": response.status,
        "method": packet.method,
        "mimeType": response.headers.get("content-type", ""),
        "body": response.body,  # DrissionPage parses JSON bodies into Python objects
    }
