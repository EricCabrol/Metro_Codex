# Working on Metro_Codex

## Project conventions

Analyze SensorLogger metro recordings and produce a local HTML review report.
Discuss work in French when appropriate; keep code, documentation and UI English.
Use "recording" for a recorded journey and "interstation run" for movement
between two stations, rather than "trip" in new UI text.
Use the existing checkout. Inspect Git status and preserve user changes.

## Files and persistence

- metro_braking/: processing, report generation, routes catalog, JS/CSS UI.
- config.json: processing parameters.
- data_test/: tracked accelerometer, metadata and annotation source inputs.
  Magnetometer exports, TotalAcceleration and legacy timestamps are ignored.
- reviews/: tracked human decisions, especially stops.csv and stops.json.
  Preserve them; they are not disposable generated artifacts.
- results/, results_reviewed/, .recording_cache/, data_test_archive/: ignored
  local outputs/cache/archive. Never force-add generated reports.
- demo/: intentionally tracked GitHub Pages snapshot. Refresh only when asked.
- .github/workflows/pages.yml: automatic publication on relevant main pushes
  and manual dispatch.

Git ignore rules do not untrack existing files. Removing files with
 git rm --cached preserves copies in this checkout, but pulling that deletion
elsewhere can remove unchanged copies there. Explain backup/regeneration steps.
Never discard reviews or raw inputs to resolve generated-report conflicts.

## Analysis invariants

Use filtered forward acceleration for extrema. Run time zero is validated
restart, not the preceding arrival. Departure acceleration and deceleration use
one configurable window, default restart +0.5 to +30.5 seconds. Pre-arrival
maximum deceleration uses the configurable last 20 seconds before the next
validated arrival. Respect gaps and incomplete windows; never invent an arrival
from the recording end.

Station assignments follow accepted stop order and route coverage; retain
"Station names inferred from stop order". Compare running durations only for
the same directed station pair on the same line, respecting existing sample-size
and standard-deviation rules.

Review edits update shading, labels, counts, badges and route mismatch highlighting
immediately. Review complete means no pending decisions, not proof of correct
counts or good data. Accept all acts on pending stops in one recording, preserves
discarded stops and edited fields, and saves once. Preserve both browser-only and
server-backed persistence.

## Development

Python 3.10+. Install with python -m pip install -e . using an existing virtual
environment where available. The user's Windows interpreter is
..\Metro\.conda\python.exe. Run commands from the repository root:

```sh
python -m unittest discover -s tests -v
python -m metro_braking --output results_reviewed
python -m metro_braking --serve --output results_reviewed
```

Analysis automatically loads reviews/stops.csv. Incremental cache signatures
check sources, configuration, reviews and processing code. Rendering-only changes
should not invalidate analysis caches. Serving alone does not regenerate HTML.

Run checks appropriate to the change. Optional Node/Playwright browser checks:
- tests/browser_edited_stops.cjs: edits, Accept all, persistence, live route checks.
- tests/browser_pages.cjs: static demo, desktop/mobile, zoom and curve toggles.

Set METRO_PYTHON, METRO_PLAYWRIGHT_DIR and METRO_BROWSER_EXE to available paths.
Other browser scripts may require generated reports and specific fixture counts;
inspect their prerequisites. Do not add browser tooling as Python dependencies.

For a requested Pages refresh:

```sh
python scripts/build_pages_demo.py --report results_reviewed
```

The builder exports statistics and filtered comparable-run samples, and copies
shared renderer assets to demo/. Do not publish raw recordings, full traces or
review decisions. A successful push does not prove Pages deployment completed.

## Handoff

Update README.md when workflows change. Report checks actually run and any
verification limits. Do not push unrelated user changes or rewrite remote history.
