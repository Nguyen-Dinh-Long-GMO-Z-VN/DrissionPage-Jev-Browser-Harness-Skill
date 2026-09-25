# YouTube: Video Transcript via the get_transcript Network Response

Source: verified on two videos, one per response shape: `cyIWQHYoUg8` (`get_transcript`, 387 segments) and
`U2hZFMVNSE0` (`get_panel`, 249 segments). The script in this directory was run against both, and its parsing
is covered by offline tests in `tests/test_youtube_transcript.py`.

To make this note available to the agent, copy `domain-skills/youtube/` to
`$BH_AGENT_WORKSPACE/domain-skills/youtube/` (by default,
`~/.config/browser-harness/agent-workspace/domain-skills/youtube/`) and set `BH_DOMAIN_SKILLS=1`.

## When to use

- Get the timestamped transcript of a YouTube video without a YouTube API key, `yt-dlp`, or Whisper.
- Use plain browser-harness CDP for this. It is a read of a network response, not a page operation, so Jev's
  model loop adds nothing and costs model calls.

## Requirements

- Chrome is running and attached to browser-harness. Signing in is not required for public videos.
- The video must have a transcript. Videos with transcripts disabled show no "Show transcript" button.
- No extra dependencies beyond browser-harness.

## Run

```bash
# From the repository root, after uv sync.
uv run python domain-skills/youtube/get_transcript.py \
  --url "https://www.youtube.com/watch?v=<id>" --output /path/to/transcript.txt
# Add --format json for [{"start_ms": ..., "text": ...}].
```

- `--url` is required. `--output` defaults to `transcript.txt`. Text output is one `[m:ss] text` line per segment.
- On success it prints `<video title>: <n> segments -> <path>`.
- When done, on success or failure, the script closes the tab it opened. It leaves alone an attached blank tab
  that `new_tab` reused, since that tab was not opened for this run.

## How it works

1. Record the open tab ids, `new_tab(url)`, then `cdp("Network.enable")` and `cdp("Emulation.setFocusEmulationEnabled", enabled=True)`.
   Wait 6 seconds for the watch page to load, then `drain_events()` to discard earlier events.
2. Expand the description (`#description-inline-expander #expand`).
3. Click the button inside `ytd-video-description-transcript-section-renderer`. The page has several buttons
   labelled "Show transcript"; only this one opens the panel that fetches the data. Clicking a different one
   leaves you waiting on a request that never fires.
4. Poll `drain_events()` for a `Network.responseReceived` whose URL contains `/youtubei/v1/get_transcript` or
   `/youtubei/v1/get_panel`, then read its body with `Network.getResponseBody`. A response with no segments is
   skipped, because `get_panel` also serves other panels.
5. Recursively collect segments. YouTube serves two shapes, and the same page can switch between them:
   - `get_transcript`: `transcriptSegmentRenderer` with `startMs` and `snippet.runs[].text`.
   - `get_panel` (modern transcript view, target `PAmodern_transcript_view`): `transcriptSegmentViewModel` with a
     `timestamp` string such as `"1:07"` and `simpleText`. There is no `startMs`; the script converts the string.

## Notes and risks

- `new_tab` opens a background tab (`document.visibilityState == "hidden"`). Without focus emulation the page
  clicks succeed but YouTube never sends `get_transcript`, and the wait times out.
- `get_panel` responses are large (about 330 KB for a 32-minute video) and also carry chapter headings.
  Chapters are not `transcriptSegmentViewModel` nodes, so they are not in the output.
- The request fires once per page load. If the panel is already open, or `drain_events()` already consumed the
  response, clicking again sends nothing. Reload the page (`new_tab` does this) and capture again.
- `drain_events()` empties the daemon's shared buffer. Do not run two waits at once.
- For videos with captions, turning on CC calls `/api/timedtext` instead. That is a different response shape and
  this script does not parse it.
- Auto-generated transcripts contain recognition errors, especially in product and proper names.
- YouTube's markup and response shape can change. If the button selector or `transcriptSegmentRenderer` stops
  matching, inspect the live page and the response body and update `EXPAND_DESCRIPTION`, `OPEN_TRANSCRIPT`,
  or `find_segments`. To find where the text sits in a new shape, search the saved body for a phrase you can
  see in the panel.
- A transcript is the video creator's content. Use it for the user's stated purpose and do not republish it.

## Troubleshooting

| Symptom | Action |
| :--- | :--- |
| "No transcript button on this page" | The video has no transcript, or the URL is not a watch page. Check the page in Chrome. |
| "No transcript response in time" | The page loaded slowly, or YouTube uses a third shape. Rerun; if it repeats, list the `youtubei` responses after the click and inspect the body. |
| Response found but empty body | Chrome dropped the body. Rerun to capture it again. |
