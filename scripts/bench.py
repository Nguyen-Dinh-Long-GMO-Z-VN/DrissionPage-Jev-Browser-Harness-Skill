"""Before/after benchmark for the browser-jev-harness skill.

    uv run --env-file .env python scripts/bench.py

Same task both ways: open the Gödel article from Wikipedia's main page.

- "manual" follows the base browser-harness skill: read the full accessibility
  tree, find the target, click coordinates, type, repeat. This measures the
  mechanical floor and the bytes an agent's model must ingest per step — it
  does not include the LLM reasoning time a real agent adds per step.
- "jev" runs the skill's fast path on the harness's tab.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from browser_harness.helpers import cdp, click_at_xy, new_tab, page_info, type_text  # noqa: E402
from jev_helpers import _detach, _result, _tab_browser  # noqa: E402
from jev_ultrafast.agent import Agent  # noqa: E402

URL = "https://en.wikipedia.org/wiki/Main_Page"
GOAL = "Find and open the Wikipedia article about Godel's incompleteness theorems."
QUERY = "Gödel's incompleteness theorems"


def ax_nodes():
    return cdp("Accessibility.getFullAXTree")["nodes"]


def find(nodes, roles=None, needle=""):
    for n in nodes:
        if not n.get("backendDOMNodeId"):
            continue
        name = (n.get("name") or {}).get("value", "")
        r = (n.get("role") or {}).get("value", "")
        if (roles is None or r in roles) and needle.lower() in name.lower():
            return n
    return None


def center(node):
    q = cdp("DOM.getBoxModel", backendNodeId=node["backendDOMNodeId"])["model"]["content"]
    return sum(q[0::2]) / 4, sum(q[1::2]) / 4


def focus(node):
    obj = cdp("DOM.resolveNode", backendNodeId=node["backendDOMNodeId"])["object"]
    cdp(
        "Runtime.callFunctionOn",
        objectId=obj["objectId"],
        functionDeclaration="function(){this.focus();this.scrollIntoViewIfNeeded&&this.scrollIntoViewIfNeeded();}",
    )
    return obj["objectId"]


def fire_input(object_id):
    cdp(
        "Runtime.callFunctionOn",
        objectId=object_id,
        functionDeclaration="function(){this.dispatchEvent(new Event('input',{bubbles:true}));}",
    )


def wait_loaded():
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            r = cdp("Runtime.evaluate", expression="document.readyState", returnByValue=True)
            if r["result"]["value"] == "complete":
                return
        except Exception:
            pass
        time.sleep(0.05)


def manual_run():
    calls, ax_bytes = 0, 0
    started = time.perf_counter()
    new_tab(URL)
    wait_loaded()
    # The base skill prescribes this for focus-gated pages: Wikipedia's search
    # suggestions never fire in a throttled background tab.
    cdp("Emulation.setFocusEmulationEnabled", enabled=True)
    calls += 1

    nodes = ax_nodes()
    calls += 1
    ax_bytes += len(json.dumps(nodes))
    box = find(nodes, roles={"searchbox", "textbox", "combobox"}, needle="search")
    x, y = center(box)
    click_at_xy(x, y)
    calls += 1
    box_obj = focus(box)  # a background tab does not always take click focus
    calls += 2
    type_text(QUERY)
    calls += 1
    fire_input(box_obj)  # Codex search needs a real input event to open suggestions
    calls += 1

    link = None
    for _ in range(20):  # suggestions need a beat; poll instead of a fixed sleep
        time.sleep(0.2)
        nodes = ax_nodes()
        calls += 1
        link = find(nodes, roles={"option", "link"}, needle="incompleteness")
        if link:
            break
    ax_bytes += len(json.dumps(nodes))
    if link is None:
        raise RuntimeError("search suggestions never appeared")
    x, y = center(link)
    click_at_xy(x, y)
    calls += 1
    wait_loaded()
    info = page_info()
    return {
        "ok": "incompleteness" in info["title"].lower(),
        "url": info["url"],
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "cdp_calls": calls,
        "ax_tree_bytes": ax_bytes,
    }


def jev_run():
    started = time.perf_counter()
    new_tab(URL)
    wait_loaded()
    with Agent.attach(_tab_browser(), GOAL) as agent:  # reuse the harness tab, not a new one
        try:
            for _ in agent.run():
                pass
        finally:
            _detach(agent.browser)
    result = _result(agent)
    result["ok"] = "incompleteness" in result["title"].lower()
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    result["request_bytes"] = sum(len(json.dumps(d.get("request", {}))) for d in agent.state["decisions"])
    return result


if __name__ == "__main__":
    ensure = sys.argv[1] if len(sys.argv) > 1 else "both"
    if ensure in ("manual", "both"):
        print("MANUAL", json.dumps(manual_run(), ensure_ascii=False))
    if ensure in ("jev", "both"):
        print("JEV   ", json.dumps(jev_run(), ensure_ascii=False))
