"""Offline contracts for the YouTube transcript domain skill. No browser, no network."""

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("browser_harness")

SCRIPT = Path(__file__).resolve().parents[1] / "domain-skills" / "youtube" / "get_transcript.py"
spec = importlib.util.spec_from_file_location("get_transcript", SCRIPT)
transcript = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transcript)


def test_parses_get_transcript_shape():
    segment = {"startMs": "1500", "snippet": {"runs": [{"text": "hel"}, {"text": "lo"}]}}
    body = json.dumps({"a": [{"transcriptSegmentRenderer": segment}]})
    assert transcript.parse_segments(body) == [{"start_ms": 1500, "text": "hello"}]


def test_parses_get_panel_shape_with_hour_timestamps():
    body = json.dumps(
        {
            "x": {
                "contentItems": [
                    {"transcriptSegmentViewModel": {"timestamp": "0:07", "simpleText": "first"}},
                    {"transcriptSegmentViewModel": {"timestamp": "1:02:03", "simpleText": "late"}},
                ]
            }
        }
    )
    assert transcript.parse_segments(body) == [
        {"start_ms": 7000, "text": "first"},
        {"start_ms": 3723000, "text": "late"},
    ]


def test_response_without_segments_is_empty():
    assert transcript.parse_segments(json.dumps({"chapters": [{"title": "Intro"}]})) == []


def test_text_format_is_minutes_and_seconds():
    rows = [{"start_ms": 67000, "text": "x"}]
    assert transcript.format_text(rows) == "[1:07] x"
