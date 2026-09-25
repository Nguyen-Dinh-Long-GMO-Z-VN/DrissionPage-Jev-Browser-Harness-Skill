import argparse
import json
import time
from pathlib import Path

from browser_harness.helpers import cdp, drain_events, js, new_tab

EXPAND_DESCRIPTION = """(() => {
  const m = document.querySelector('#description-inline-expander #expand, tp-yt-paper-button#expand');
  if (m) m.click();
  return !!m;
})()"""

# Several "Show transcript" buttons exist on a watch page; only the one inside the description section
# opens the panel that calls get_transcript.
OPEN_TRANSCRIPT = """(() => {
  const b = document.querySelector('ytd-video-description-transcript-section-renderer button');
  if (b) b.click();
  return !!b;
})()"""


def find_segments(obj, out):
    if isinstance(obj, dict):
        if "transcriptSegmentRenderer" in obj:
            out.append(obj["transcriptSegmentRenderer"])
        for v in obj.values():
            find_segments(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_segments(v, out)


def parse_segments(body):
    segments = []
    find_segments(json.loads(body), segments)
    rows = []
    for s in segments:
        runs = s.get("snippet", {}).get("runs", [])
        rows.append({"start_ms": int(s.get("startMs", 0)), "text": "".join(r.get("text", "") for r in runs)})
    return rows


def wait_for_transcript(timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for ev in drain_events():
            if ev.get("method") != "Network.responseReceived":
                continue
            response = ev.get("params", {}).get("response", {})
            if "/youtubei/v1/get_transcript" in response.get("url", ""):
                request_id = ev["params"]["requestId"]
                return cdp("Network.getResponseBody", requestId=request_id).get("body", "")
        time.sleep(0.2)
    return None


def fetch_transcript(url, timeout=20):
    # A fresh tab is required: once the panel is open, clicking again sends no new request.
    new_tab(url)
    cdp("Network.enable")
    # new_tab opens in the background; a hidden page never fetches the transcript panel.
    cdp("Emulation.setFocusEmulationEnabled", enabled=True)
    time.sleep(6)
    drain_events()
    js(EXPAND_DESCRIPTION)
    time.sleep(1)
    if not js(OPEN_TRANSCRIPT):
        raise SystemExit("No transcript button on this page: the video has no transcript, or the page is not a watch page.")
    body = wait_for_transcript(timeout)
    if body is None:
        raise SystemExit("get_transcript did not respond in time. Reload the page and rerun.")
    rows = parse_segments(body)
    if not rows:
        raise SystemExit("get_transcript returned no segments. YouTube may have changed the response shape.")
    return rows


def format_text(rows):
    return "\n".join(f"[{r['start_ms'] // 60000}:{r['start_ms'] // 1000 % 60:02d}] {r['text']}" for r in rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", default="transcript.txt")
    parser.add_argument("--format", choices=["txt", "json"], default="txt")
    args = parser.parse_args()

    rows = fetch_transcript(args.url)
    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False) if args.format == "json" else format_text(rows))
    print(f"{len(rows)} segments -> {out}")


if __name__ == "__main__":
    main()
