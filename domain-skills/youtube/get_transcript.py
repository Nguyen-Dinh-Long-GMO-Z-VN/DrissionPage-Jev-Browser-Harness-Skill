import argparse
import json
import time
from pathlib import Path

from browser_harness.helpers import cdp, close_tab, drain_events, js, list_tabs, new_tab

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


TRANSCRIPT_ENDPOINTS = ("/youtubei/v1/get_transcript", "/youtubei/v1/get_panel")


def find_segments(obj, out):
    if isinstance(obj, dict):
        for key in ("transcriptSegmentRenderer", "transcriptSegmentViewModel"):
            if key in obj:
                out.append((key, obj[key]))
        for v in obj.values():
            find_segments(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_segments(v, out)


def timestamp_to_ms(timestamp):
    ms = 0
    for part in timestamp.split(":"):
        ms = ms * 60 + int(part)
    return ms * 1000


def parse_segments(body):
    """Read both response shapes: get_transcript (transcriptSegmentRenderer, startMs, snippet.runs) and the
    modern get_panel view (transcriptSegmentViewModel, "m:ss" timestamp, simpleText)."""
    segments = []
    find_segments(json.loads(body), segments)
    rows = []
    for key, s in segments:
        if key == "transcriptSegmentRenderer":
            runs = s.get("snippet", {}).get("runs", [])
            rows.append({"start_ms": int(s.get("startMs", 0)), "text": "".join(r.get("text", "") for r in runs)})
        else:
            rows.append({"start_ms": timestamp_to_ms(s.get("timestamp", "0:00")), "text": s.get("simpleText", "")})
    return rows


def wait_for_transcript(timeout):
    """Rows from the first transcript-bearing response. get_panel also serves other panels, so a response
    without segments is skipped rather than treated as the answer."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for ev in drain_events():
            if ev.get("method") != "Network.responseReceived":
                continue
            url = ev.get("params", {}).get("response", {}).get("url", "")
            if not any(endpoint in url for endpoint in TRANSCRIPT_ENDPOINTS):
                continue
            body = cdp("Network.getResponseBody", requestId=ev["params"]["requestId"]).get("body", "")
            rows = parse_segments(body) if body else []
            if rows:
                return rows
        time.sleep(0.2)
    return None


def fetch_transcript(url, timeout=20):
    """Return (title, rows). Closes the tab it opened when done, on success and on failure."""
    # A fresh tab is required: once the panel is open, clicking again sends no new request.
    before = {t["targetId"] for t in list_tabs()}
    tab = new_tab(url)
    # new_tab reuses an attached blank tab instead of creating one; that tab is not ours to close.
    opened = tab not in before
    try:
        cdp("Network.enable")
        # new_tab opens in the background; a hidden page never fetches the transcript panel.
        cdp("Emulation.setFocusEmulationEnabled", enabled=True)
        time.sleep(6)
        drain_events()
        js(EXPAND_DESCRIPTION)
        time.sleep(1)
        if not js(OPEN_TRANSCRIPT):
            raise SystemExit("No transcript button on this page: the video has no transcript, or the page is not a watch page.")
        rows = wait_for_transcript(timeout)
        if rows is None:
            raise SystemExit("No transcript response in time. Reload the page and rerun.")
        return js("document.title").removesuffix(" - YouTube"), rows
    finally:
        if opened:
            close_tab(tab)


def format_text(rows):
    return "\n".join(f"[{r['start_ms'] // 60000}:{r['start_ms'] // 1000 % 60:02d}] {r['text']}" for r in rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", default="transcript.txt")
    parser.add_argument("--format", choices=["txt", "json"], default="txt")
    args = parser.parse_args()

    title, rows = fetch_transcript(args.url)
    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False) if args.format == "json" else format_text(rows))
    print(f"{title}: {len(rows)} segments -> {out}")


if __name__ == "__main__":
    main()
