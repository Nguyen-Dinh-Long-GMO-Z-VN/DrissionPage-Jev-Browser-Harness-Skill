"""Observed actions over one CDP session, no per-step subprocess.

`Browser` talks CDP through Browser Harness. `drission.DrissionBrowser` swaps only the transport; the
snapshot, guards, and execution below are shared. Neither backend is imported until it is used.
"""

import hashlib
import json
import sys
import time
from pathlib import Path

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"

# Counts the page's own fetch/XHR requests in flight and marks a navigation that has started (a form submit, a
# redirect), so the waits below can tell "loading" from "still". A fetch counts until its response headers arrive.
# Installed on every new document of an observed tab and on the current one when attaching (from upstream #124).
TRACK_REQUESTS = """(() => {
  if (window.__jevInflight !== undefined) return;
  window.__jevInflight = 0;
  const done = () => { window.__jevInflight = Math.max(0, window.__jevInflight - 1); };
  const fetch = window.fetch;
  if (fetch) window.fetch = function (...args) {
    window.__jevInflight++;
    try { return fetch.apply(this, args).finally(done); } catch (error) { done(); throw error; }
  };
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    window.__jevInflight++;
    try { this.addEventListener('loadend', done, {once: true}); return send.apply(this, args); }
    catch (error) { done(); throw error; }
  };
  addEventListener('beforeunload', () => { window.__jevNavigating = Date.now(); });
})()"""

# Read-only wait after input, so the next decision sees the page the input produced rather than a frame of
# its transition (a decision on a moving page goes stale and costs a whole model call). Autocomplete fields
# wait for visible options. Other input waits at least 32 ms, then until the DOM has been quiet for 50 ms, no
# finite animation runs, and no request is in flight, capped at 400 ms. WAIT returns once the page has changed
# and then been quiet for 100 ms with the network idle for 200 ms; after 500 ms if nothing changes and the network
# is idle; and while requests are in flight it keeps waiting, up to 10 s.
SETTLE_AFTER_INPUT = """(action => new Promise(resolve => {
  const cache=window.__jevFast, start=performance.now();
  const field=cache?.nodes.get(action.node);
  const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
  const waiting=action.kind==='wait';
  // Poll on timers, not requestAnimationFrame: a background tab can stop producing frames entirely.
  let ticks=0, stopped=false;
  const finish=()=>{stopped=true;resolve()};
  setTimeout(finish,autocomplete ? 200 : waiting ? 10000 : 400);
  const quiet=ms=>performance.now()-(cache?.changed ?? 0)>=ms;
  let idleSince=start;
  const busy=()=>(window.__jevInflight||0)>0 ||
    (!!window.__jevNavigating && Date.now()-window.__jevNavigating<15000);
  // A pending animation (no start time) may never start in a background tab; only started, finite ones count.
  const animating=()=>document.getAnimations().some(a=>a.playState==='running' && a.startTime!==null &&
    a.effect?.getComputedTiming().endTime!==Infinity);
  const suggestions=()=>{
    const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
      .split(/\\s+/).filter(Boolean);
    const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
    return roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]).some(e=>{
      const r=e.getBoundingClientRect();
      return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
        e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
    });
  };
  const ready=()=>{
    if (stopped) return;
    const now=performance.now();
    if (busy()) idleSince=Infinity; else if (idleSince===Infinity) idleSince=now;
    const idle=ms=>now-idleSince>=ms;
    const settled=autocomplete ? suggestions() :
      waiting ? !animating() && (cache?.changed>start ? now-start>=100 && quiet(100) && idle(200) :
        idle(500) && now-start>=500) :
      quiet(50) && !animating() && !busy();
    if (++ticks>=2 && settled) finish();
    else setTimeout(ready,16);
  };
  setTimeout(ready,16);
}))"""


def cdp(method, **params):
    """Browser Harness CDP call, imported on first use so other backends do not need it."""
    from browser_harness.helpers import cdp as harness_cdp

    return harness_cdp(method, **params)


# Chrome renders these as segmented spinners; they ignore inserted text and take an ISO value.
SEGMENTED_INPUTS = {"time", "date", "datetime-local", "month", "week"}


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Browser:
    def __init__(self, url):
        from browser_harness.admin import ensure_daemon

        ensure_daemon()
        # A separate, unfocused window. A background tab in the user's window gets no rendered frames on some
        # platforms (Linux Chrome here): its CSS animations stay pending, so an opening menu keeps opacity 0 and
        # never appears in a snapshot. Focus emulation does not change that; a window of its own does.
        self.target = cdp("Target.createTarget", url="about:blank", newWindow=True, background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        # Start network recording at attach, before any traffic: sessions attached
        # directly get no daemon auto-enable, and undrained events buffer bounded.
        self.call("Network.enable")
        self._track_requests()
        self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)

    @classmethod
    def from_session(cls, session):
        """Wrap a tab someone else attached. close() leaves the tab alone; the caller detaches the session."""
        browser = cls.__new__(cls)
        browser.target, browser.session, browser.after_input = None, session, None
        browser.call("Emulation.setFocusEmulationEnabled", enabled=True)
        browser.call("Network.enable")  # the daemon only auto-enables its own sessions
        browser._track_requests()
        return browser

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression=SETTLE_AFTER_INPUT + "(" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except (RuntimeError, StalePage):
                pass
        deadline = time.monotonic() + 15
        while True:
            try:
                return self._operate({"operation": "observe", "session": self.session, "screenshot": screenshot})
            except StalePage:
                if time.monotonic() > deadline:
                    raise
                self._wait_ready()
                time.sleep(0.02)

    def _track_requests(self):
        """Count in-flight requests in this document and every later one, for the waits. Best effort."""
        try:
            self.call("Page.enable")
            self.call("Page.addScriptToEvaluateOnNewDocument", source=TRACK_REQUESTS)
            self.call("Runtime.evaluate", expression=TRACK_REQUESTS)
        except Exception:
            pass

    def expect_change(self):
        """Make the next observe() wait as after WAIT: for the page to change and go quiet. Read-only."""
        self.after_input = {"id": "wait", "kind": "wait"}

    def settle(self, page, screenshot=False, *, young_ms=2500, quiet=0.1, cap=0.5):
        """Return a page that has held still for `quiet` seconds, re-observing while it changes.

        Freshly loaded documents keep mutating for ~100 ms as their scripts initialise. A decision made on
        that state goes stale and costs a whole model call (0.3-1.2 s), so wait out the churn locally first.
        Older documents skip this entirely; their changes come from our own input, which observe() waits for.
        """
        if page.get("age", young_ms) >= young_ms:
            return page
        deadline = time.monotonic() + cap
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(0.03)
            if not self.fresh(page):
                page = self.observe(screenshot=screenshot)
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= quiet:
                break
        return page

    def _wait_ready(self):
        # Transient mutations return immediately; real navigations wait out the load.
        try:
            if self.evaluate("document.readyState") == "complete":
                return
        except StalePage:
            pass
        time.sleep(0.05)

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        result = self._operate({"operation": "act", "session": self.session, "action": action, "text": text})
        # The next observe() waits for the page to settle, including after WAIT.
        self.after_input = action
        return result

    def _operate(self, request):
        return browser_operation(request)

    def close(self):
        if self.target:
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        if "call" in request:
            return request["call"](method, **params)
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    def read_back(node, text):
        """Read-only check that a fill landed. The input already happened, so never raise or retry here."""
        expected = " ".join(text.split())
        for attempt in range(4):
            if attempt:
                time.sleep(0.05)
            try:
                result = call("Runtime.evaluate", returnByValue=True, expression=(
                    f"(() => {{ const e=window.__jevFast?.nodes.get({node}); "
                    "return e ? ('value' in e ? String(e.value) : e.innerText) : null; })()"
                ))
                actual = result.get("result", {}).get("value")
            except Exception:
                return "unverified"
            if isinstance(actual, str) and " ".join(actual.split()) == expected:
                return "verified"
        return "unverified"

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""(action => {
              const cache=window.__jevFast, e=cache?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]')) return null;
              // A styled-away checkbox or radio is clicked through its visible label (see snapshot.js).
              const shown=e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
              const box=shown ? e : action.kind==='click' && cache.proxy(e);
              if (!box) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              if (box.scrollIntoViewIfNeeded) box.scrollIntoViewIfNeeded(true);
              else box.scrollIntoView({block:'center',inline:'center'});
              // The snapshot's own hit test, plus random interior points for a partly covered control.
              const target=action.kind==='click' ? cache.point(e,12) : cache.hit(e,12);
              if (!target) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {...target,itype:e.tagName==='INPUT' ? e.type : ''};
            })(""" + json.dumps(action) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill" and target.get("itype") in SEGMENTED_INPUTS:
                    # Assign the observed node's value like a dropdown; the text stays a JSON argument.
                    assigned = evaluate(
                        "(request => { const e=window.__jevFast?.nodes.get(request.node);"
                        " if (!e?.isConnected) return null; e.value=request.text;"
                        " e.dispatchEvent(new Event('input',{bubbles:true}));"
                        " e.dispatchEvent(new Event('change',{bubbles:true})); return e.value; })("
                        + json.dumps({"node": action["node"], "text": request["text"]})
                        + ")"
                    )
                    if assigned != request["text"]:
                        # The control keeps only a valid ISO value; anything else leaves it empty.
                        raise RuntimeError(f"The {target['itype']} field rejected {request['text']!r}; inspect it.")
                    return {"executed": action["id"], "typed": "verified"}
                if kind == "fill":

                    def require_focus():
                        # A page's click or key handler can move focus; typing would then land in another field.
                        try:
                            focused = evaluate(
                                "(node => { const e=window.__jevFast?.nodes.get(node);"
                                " return !!(e?.isConnected && e.contains(document.activeElement) &&"
                                " !e.matches(':disabled') && !e.readOnly); })(" + str(action["node"]) + ")"
                            )
                        except StalePage:
                            focused = False
                        if not focused:
                            # The click already ran, so this is not a stale decision to retry (upstream #80).
                            raise RuntimeError("The text field lost focus before typing; inspect before retrying.")

                    require_focus()
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    require_focus()  # key handlers can redirect focus during Select All as well
                    call("Input.insertText", text=request["text"])
                    return {"executed": action["id"], "typed": read_back(action["node"], request["text"])}
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
