# Benchmark

Task: open the Gödel article starting from Wikipedia's main page. One run each, local Chrome, `jev-1.13.0`,
2026-09-21. Reproduce with `scripts/bench.py`.

| Path | Result | Wall time | Model input |
| --- | --- | --- | --- |
| Manual harness (AX tree + click_at_xy) | ok | 2,043 ms | ~2.1 MB AX data |
| `jev_run` | done | 5,365 ms | ~120 KB requests, 5 calls |

## How to read it

- Jev is **slower** on this small task (5.4 s vs 2.0 s). Its advantage is ~18x less model input and no
  hand-written selector or coordinate logic; the gap in input size grows with page complexity.
- The manual row is a scripted mechanical floor. It excludes the LLM reasoning time a real agent adds at every
  step, so it flatters the manual path.
- One task, one run, one browser profile. This is an illustration, not a reliability or speed benchmark. The
  repo's broader measurements (repeated Flights runs, alternating comparison) are in `docs/performance.md`.
