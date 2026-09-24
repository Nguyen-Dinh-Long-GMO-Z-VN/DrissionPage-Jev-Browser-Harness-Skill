"""Local-browser freshness/execution regressions. No model calls or external websites."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from jev_ultrafast.browser import Browser, StalePage

HTML = """<!doctype html><title>Guard checks</title>
<style>body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<p id="outside">Unrelated offscreen text</p>"""


class Slow(BaseHTTPRequestHandler):
    """A local endpoint that answers after ?ms= milliseconds, so a request stays in flight."""

    def do_GET(self):
        if "ms=" in self.path:
            time.sleep(int(self.path.split("ms=")[1]) / 1000)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<!doctype html><title>Network checks</title><p>ok</p>")

    def log_message(self, *_args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    slow = f"http://127.0.0.1:{server.server_port}/slow?ms="
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
        assert browser.fresh(page), "Movement should use fresh geometry, not another model call"
        browser.act(action, page)
        assert browser.evaluate("window.clicks") == 1
        passed.append("moving target clicked at its current location")

        browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'")
        assert browser.fresh(page)
        passed.append("unrelated offscreen text does not invalidate")

        mutations = {
            "visible context": "document.querySelector('#context').textContent='Cart total: $100'",
            "accessible label": "document.querySelector('#target').setAttribute('aria-label','Delete account')",
            "field property": "document.querySelector('#field').value='London'",
            "checkbox property": "document.querySelector('#toggle').checked=true",
            "disabled target": "document.querySelector('#target').disabled=true",
            "read-only field": "document.querySelector('#field').readOnly=true",
            "hidden target": "document.querySelector('#target').style.display='none'",
            "replaced node": "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
            "dropdown option": "document.querySelector('select').options[1].text='Coastal'",
        }
        for label, expression in mutations.items():
            browser.evaluate("document.querySelector('#target').style.display='block'; "
                             "document.querySelector('#target').disabled=false")
            page = browser.observe(screenshot=False)
            browser.evaluate(expression)
            assert not browser.fresh(page), label
            passed.append(label + " invalidates")

        browser.evaluate("document.querySelector('#target').disabled=false; "
                         "document.querySelector('#target').style.display='block'")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Delete account")
        # A textless overlay does not alter the model's semantic state, but must block a click.
        browser.evaluate("const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                         "document.body.append(cover)")
        assert browser.fresh(page)
        try:
            browser.act(action, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered target was clicked")
        assert browser.evaluate("window.clicks") == 1
        passed.append("overlay blocked before input")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form><p id="price">Total $10</p>
          <button type="button" id="buy">Buy</button>
          <label>Search <input id="query" role="combobox" aria-controls="suggestions"></label>
          <div role="listbox" id="suggestions"></div>
          <label><input id="check" type="checkbox">Enabled</label>
          <label><input id="radio" type="radio">Choice</label>
          <input id="readonly" aria-label="Read only" readonly>
          <input id="secret" type="password" value="never expose this">
          <button id="off" disabled>Disabled</button>
          <select id="category" aria-label="Category">
            <option>All</option><option>Design</option><option disabled>Unavailable</option>
          </select></form><aside id="unrelated">News</aside>
        """))
        page = browser.observe(screenshot=False)
        buy = next(a for a in page["actions"] if a["label"] == "Buy")
        browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'")
        assert browser.fresh(page, buy)
        assert not browser.fresh(page)
        passed.append("click guard accepts unrelated visible updates; terminal guard rejects them")
        for label, expression in {
            "nearby price": "document.querySelector('#price').textContent='Total $100'",
            "form value": "document.querySelector('#query').value='changed'",
            "form toggle": "document.querySelector('#check').checked=true",
            "target replacement": "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
        }.items():
            page = browser.observe(screenshot=False)
            buy = next(a for a in page["actions"] if a["label"] == "Buy")
            browser.evaluate(expression)
            assert not browser.fresh(page, buy), label
            passed.append(label + " invalidates action-specific guard")

        page = browser.observe(screenshot=False)
        actions = page["actions"]
        for role in ("checkbox", "radio"):
            assert {a["kind"] for a in actions if a.get("role") == role} == {"click"}
        assert {a["kind"] for a in actions if a["label"] == "Read only"} == {"click"}
        assert not any(a["label"] == "Disabled" or a.get("value") == "never expose this" for a in actions)
        assert [a["value"] for a in actions if a["kind"] == "select"] == ["Design"]
        passed.append("native controls expose only supported operations and safe values")

        select = next(a for a in actions if a["kind"] == "select")
        browser.act(select, page)
        assert browser.evaluate("document.querySelector('#category').value") == "Design"
        passed.append("native dropdown selects an observed option")


        browser.evaluate("document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{"
                         "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'"
                         "},60))")
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["kind"] == "fill")
        assert browser.act(field, page, text="Generated")["typed"] == "verified"
        page = browser.observe(screenshot=False)
        value = browser.evaluate("document.querySelector('#query').value")
        assert value == "Generated", repr(value)
        assert any(a.get("role") == "option" for a in page["actions"])
        passed.append("real text input waits for asynchronous combobox suggestions")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <div id="post" contenteditable="true" role="textbox" aria-label="Post" style="height:60px"></div>
          <input id="dead" aria-label="Dead" value="">"""))
        browser.evaluate("document.querySelector('#dead').addEventListener('input',e=>{e.target.value=''})")
        page = browser.observe(screenshot=False)
        post = next(a for a in page["actions"] if a["label"] == "Post" and a["kind"] == "fill")
        assert browser.act(post, page, text="Long text for a rich editor")["typed"] == "verified"
        assert browser.evaluate("document.querySelector('#post').innerText") == "Long text for a rich editor"
        page = browser.observe(screenshot=False)
        dead = next(a for a in page["actions"] if a["label"] == "Dead" and a["kind"] == "fill")
        assert browser.act(dead, page, text="Vanishes")["typed"] == "unverified"
        passed.append("typed text is read back; a field that drops it is reported unverified")

        # The select sits after more scope text than the guard keeps, so only its own options can catch this.
        filler = '<p style="height:20px;overflow:hidden">' + "filler " * 1500 + "</p>"
        browser.evaluate("document.body.innerHTML=" + repr(filler + """
          <select aria-label="Plan"><option value="a">Basic</option><option value="b">Pro</option></select>"""))
        page = browser.observe(screenshot=False)
        plan = next(a for a in page["actions"] if a["kind"] == "select")
        browser.evaluate("document.querySelector('option[value=b]').textContent='Free'")
        assert not browser.fresh(page, plan)
        passed.append("dropdown option relabel invalidates even past the scope text limit")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <div style="height:600px"></div>
          <button id="low" onclick="window.lowHit=(window.lowHit||0)+1">Low</button>
          <div style="height:400px"></div>"""))
        page = browser.observe(screenshot=False)
        low = next(a for a in page["actions"] if a["label"] == "Low")
        browser.evaluate("document.body.insertAdjacentHTML('afterbegin','<div style=height:200px></div>')")
        browser.act(low, page)
        assert browser.evaluate("window.lowHit") == 1
        passed.append("scroll-into-view rescues a target pushed out of the viewport")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <button id="wide" style="width:500px;height:60px"
            onclick="window.wideHit=(window.wideHit||0)+1">Wide</button>"""))
        page = browser.observe(screenshot=False)
        wide = next(a for a in page["actions"] if a["label"] == "Wide")
        browser.evaluate("""const r=document.querySelector('#wide').getBoundingClientRect();
          const d=document.createElement('div');
          d.style.cssText=`position:fixed;left:${r.left+r.width/2-60}px;top:${r.top-5}px;
            width:120px;height:70px;z-index:5;background:red`;
          document.body.append(d)""")
        browser.act(wide, page)
        assert browser.evaluate("window.wideHit") == 1
        passed.append("click escapes a midpoint cover via a clear interior point")

        # A dialog that fades out in script steps, then leaves the DOM: the next observation must see it closed.
        browser.evaluate("document.body.innerHTML=" + repr("""
          <div id="dialog" role="dialog"><button id="close">Close dialog</button></div>
          <button>Search</button>"""))
        browser.evaluate("""document.querySelector('#close').onclick=()=>{
          const d=document.querySelector('#dialog'); let step=0;
          const fade=setInterval(()=>{d.style.opacity=String(1-++step/5);
            if (step===5) {clearInterval(fade); d.remove()}},30)}""")
        page = browser.observe(screenshot=False)
        close = next(a for a in page["actions"] if a["label"] == "Close dialog")
        browser.act(close, page)
        page = browser.observe(screenshot=False)
        assert [a["label"] for a in page["actions"] if a["kind"] == "click"] == ["Search"], page["actions"]
        assert browser.fresh(page)
        passed.append("observation after a click waits out a closing transition")

        browser.evaluate("document.body.innerHTML='<p id=status>Loading results</p>'")
        page = browser.observe(screenshot=False)
        browser.evaluate("setTimeout(()=>{document.querySelector('#status').textContent='3 results'},300)")
        wait = next(a for a in page["actions"] if a["kind"] == "wait")
        started = time.monotonic()
        browser.act(wait, page)
        page = browser.observe(screenshot=False)
        waited = time.monotonic() - started
        assert "3 results" in page["text"] and waited < 1.2, (page["text"], waited)
        passed.append(f"WAIT returns when loading content arrives ({waited * 1000:.0f} ms)")

        page = browser.observe(screenshot=False)
        started = time.monotonic()
        browser.act(wait, page)
        browser.observe(screenshot=False)
        waited = time.monotonic() - started
        assert waited < 0.8, waited
        passed.append(f"WAIT on a page that stays still returns early ({waited * 1000:.0f} ms)")

        # The page changes at once ("Loading") but its data arrives 1.2 s later (upstream PR #124).
        # Served from the local server: a data: page may not fetch from 127.0.0.1.
        browser.call("Page.navigate", url=slow.split("slow?")[0])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if browser.evaluate("location.protocol+document.readyState") == "http:complete":
                    break
            except StalePage:
                pass
            time.sleep(0.02)
        assert browser.evaluate("typeof window.__jevInflight") == "number"
        browser.evaluate("document.body.innerHTML='<p id=status>Idle</p>'")
        page = browser.observe(screenshot=False)
        browser.evaluate("setTimeout(()=>{document.querySelector('#status').textContent='Loading';"
                         f"fetch('{slow}1200').then(()=>{{document.querySelector('#status').textContent='5 results'}})"
                         "},30)")
        started = time.monotonic()
        browser.act(wait, page)
        page = browser.observe(screenshot=False)
        waited = time.monotonic() - started
        assert "5 results" in page["text"] and 1.1 < waited < 3, (page["text"], waited)
        passed.append(f"WAIT outlasts a request in flight after an early change ({waited * 1000:.0f} ms)")

        browser.evaluate("document.body.innerHTML=" + repr(
            "<button id=load>Load</button><p id=out>Nothing yet</p>"))
        browser.evaluate("document.querySelector('#load').onclick=()=>fetch('" + slow + "250')"
                         ".then(()=>{document.querySelector('#out').textContent='Loaded'})")
        page = browser.observe(screenshot=False)
        browser.act(next(a for a in page["actions"] if a["label"] == "Load"), page)
        page = browser.observe(screenshot=False)
        assert "Loaded" in page["text"], page["text"]
        passed.append("the observation after a click waits for a short request")

        browser.evaluate("document.body.innerHTML='<button type=button id=nonstop aria-pressed=false>Nonstop</button>'")
        page = browser.observe(screenshot=False)
        toggle = next(a for a in page["actions"] if a["label"] == "Nonstop")
        assert toggle["pressed"] == "false", toggle
        browser.evaluate("document.querySelector('#nonstop').setAttribute('aria-pressed','true')")
        assert not browser.fresh(page, toggle)
        assert browser.observe(screenshot=False)["fingerprint"] != page["fingerprint"]
        passed.append("toggle button pressed state is observed and guarded")

        # Offer only what the act guard can hit (upstream PR #137).
        browser.evaluate("document.body.innerHTML=" + repr("""
          <style>body{margin:0;font:20px/1.6 serif}
          #side{position:absolute;left:0;top:0;width:220px;height:200px;overflow-y:auto}
          #side a{display:block;height:60px}#main{position:absolute;left:0;top:210px;width:100%}
          #banner{position:fixed;left:0;right:0;top:520px;height:120px;background:#333}
          #under{position:absolute;left:40px;top:560px}#para{width:300px;margin:0 0 0 400px}</style>
          <nav id="side"><a href="#a">Overview</a><a href="#b">Models</a><a href="#c">Limits</a>
          <a href="#q" id="quotas" onclick="window.quotasHit=1;return false">Quotas</a>
          <a href="#p" onclick="return false">Pricing</a></nav>
          <div id="main"><p id="para">Python is a widely used, very popular
          <a href="#gp" id="wrapped" onclick="window.wrappedHit=(window.wrappedHit||0)+1;return false">general-purpose
          programming language</a> with a large standard library.</p></div>
          <button id="under">Accept all</button><div id="banner"></div>"""))
        assert browser.evaluate("(() => {const w=document.querySelector('#wrapped'), r=w.getBoundingClientRect();"
                                "return w.getClientRects().length===2 && "
                                "!w.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))})()")
        page = browser.observe(screenshot=False)
        labels = [a["label"] for a in page["actions"]]
        assert "Pricing" not in labels and "Accept all" not in labels, labels
        assert {"Overview", "Models"} <= set(labels), labels
        passed.append("controls clipped by a scroll container or under a banner are not offered")
        quotas = next(a for a in page["actions"] if a["label"] == "Quotas")  # 20 px of it is visible
        browser.act(quotas, page)
        assert browser.evaluate("window.quotasHit") == 1
        page = browser.observe(screenshot=False)
        passed.append("a partly clipped control is clicked on its visible part")
        browser.evaluate("document.querySelector('#banner').style.top='200px'")
        assert browser.fresh(page)
        browser.evaluate("document.querySelector('#banner').style.top='520px'")
        passed.append("covering a control does not invalidate the observation")
        wrapped = next(a for a in page["actions"] if a["label"].startswith("general-purpose"))
        browser.act(wrapped, page)
        assert browser.evaluate("window.wrappedHit") == 1
        passed.append("a link wrapped over two lines is clicked on a visible fragment")

        # Styled-away checkboxes and radios are operated through their labels (upstream PR #113).
        browser.evaluate("document.body.innerHTML=" + repr("""
          <style>.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
          .pill{display:inline-block;padding:10px 20px;border:1px solid}</style>
          <label class="pill"><input type="radio" name="size" class="sr" value="s">Small</label>
          <input type="radio" name="size" id="large" value="l" style="opacity:0;position:absolute">
          <label for="large" class="pill">Large</label>
          <input type="checkbox" id="gift" style="position:absolute;left:-9999px">
          <label for="gift" class="pill">Gift wrap</label>"""))
        page = browser.observe(screenshot=False)
        for label, check in [("Small", "input[value=s]"), ("Large", "#large"), ("Gift wrap", "#gift")]:
            action = next(a for a in page["actions"] if a["label"] == label)
            assert action["checked"] == "false", action
            browser.act(action, page)
            assert browser.evaluate(f"document.querySelector('{check}').checked"), label
            page = browser.observe(screenshot=False)
            assert next(a for a in page["actions"] if a["label"] == label)["checked"] == "true", label
        passed.append("hidden radios and checkboxes (clipped, transparent, off-screen) are clicked via their labels")

        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, field)
        passed.append("navigation invalidates the old document")
    finally:
        browser.close()
        server.shutdown()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
