# Metro braking exploration

A fresh implementation of the original Metro README's research intent: examine
smartphone acceleration recordings to understand strong and abrupt braking,
and eventually relate measurements to perceived harshness. No original Python
implementation or generated stop timestamps are used.

## Run

Python 3.10+ with NumPy, pandas, SciPy and Matplotlib:

```powershell
python -m pip install -e .
python -m metro_braking --data data_test --output results
python -m unittest discover -s tests -v
```

On this workstation the pre-existing scientific environment is also usable
without installing anything:

```powershell
& '..\Metro\.conda\python.exe' -m metro_braking --data data_test --output results
```

Run from this directory. Open `results/report.html` in a browser. The report is
self-contained locally (PNG images beside the HTML; no network dependencies).

## Data provenance

`data_test/` contains SensorLogger recordings copied from `Metro/data_test`.
Git retains the inputs used by the analysis: `Accelerometer.csv`,
`AccelerometerUncalibrated.csv`, `Metadata.csv` and `Annotation.csv`.
Original SensorLogger `Stop` annotations are displayed as visual references,
not detection targets. Unused magnetometer exports, `TotalAcceleration.csv`
and legacy `timestamps*.txt` files are ignored; local copies may be retained.

Generated `results/` and `results_reviewed/` reports, recording caches and
archived source folders are ignored by Git. Regenerate a reviewed report with
`python -m metro_braking --output results_reviewed`; saved decisions in
`reviews/` remain tracked and are loaded automatically. The published snapshot
in `demo/` remains tracked for GitHub Pages.
`manifest.json` in the output records SHA-256 hashes, configuration, reviews,
package versions and any failures.

When first pulling the cleanup that removes previously tracked outputs and unused
sensor files, Git can remove unchanged copies from the working directory.
Copy any of those files you want to retain outside the checkout before pulling;
then restore them locally (they will be ignored). Reviewed reports can instead
be regenerated with the command above.

### Resolving the one-time generated-report cleanup

Before pulling the cleanup, copy results_reviewed outside the checkout to a new
backup location and verify that copy. If local report edits block the pull, run:

```powershell
git restore --source=HEAD --staged --worktree -- results_reviewed
git pull
```

If a local commit also changed those outputs, the merge may then report
modify/delete conflicts. Remove the report from the index, preserving remaining
local copies, and check for other unresolved conflicts:

```powershell
git rm -r --cached -f -- results_reviewed
git diff --name-only --diff-filter=U
```

Only if the second command lists no paths, finish with:

```powershell
git commit -m "Merge cleanup and stop tracking generated reports"
```

Otherwise resolve the remaining conflicts first. After the successful merge,
restore the backed-up report or regenerate it. Do not discard reviews or raw
recordings to resolve generated-report conflicts.

## Method

1. Inventory available CSV files. Prefer `Accelerometer.csv`, whose test-set
   measurements are near zero at rest; fall back to uncalibrated acceleration.
2. Drop invalid rows, sort timestamps, deduplicate, measure sampling quality,
   and split at gaps longer than 0.5 seconds. Blocks shorter than four seconds
   or 40 samples are excluded and usable duration is reported.
3. Interpolate within continuous blocks onto a native-rate grid, apply an
   anti-alias low-pass before downsampling to at most 20 Hz, then remove each
   block's median XYZ vector. The uncalibrated fallback removes only a constant
   gravity/bias vector; it cannot compensate phone rotation.
4. Apply a fourth-order, zero-phase Butterworth low-pass (default 0.5 Hz).
   Use Y as the forward axis for named recordings; `Yinv` reverses its sign.
   Positive means forward propulsion and negative means braking. An explicit
   axis/polarity in configuration overrides the convention. PCA is retained as
   a check on axis alignment, not as the primary travel axis. Generic unnamed
   recordings still use PCA with unresolved sign.
5. Propose contiguous lobes exceeding 0.25 m/s² for at least two seconds. Both
   signs are retained when direction is unknown. Independently detect stops
   using **AccelerometerUncalibrated.csv**, even when calibrated data exists.
   Resample with anti-alias filtering and measure trailing one-second XYZ
   variation (square root of summed axis variances), before the braking filter.
   Require at least **15 seconds** of observed dwell below 0.12 m/s² variation.
   Estimate a longitudinal rest level from quiet windows and require the local
   mean to remain within 0.25 m/s² of it, excluding constant propulsion plateaus.
   Bridge interruptions up to 1.5 seconds only within a continuous data block;
   at least 90% of the candidate must satisfy both conditions. Short quiet
   intervals (5–15 seconds) are reported separately and excluded from stop counts.
   Gravity and constant bias need not be zero. Calibrated acceleration can have
   a settling tail after arrival, so its return to zero is not used for stops.
   Constant acceleration does **not** prove a stop, and candidate onset can lag
   arrival by roughly a window. A recording dominated by steady acceleration
   can bias the rest-level estimate. Phone rotation may shift gravity projection
   and fragment genuine stops. Missing uncalibrated data yields no stop proposals.
6. Measure peak and one-second sustained acceleration, mean acceleration,
   duration, jerk, and acceleration integral within event boundaries. Recompute
   peak values at 0.25, 0.5 and 1 Hz to expose filter sensitivity.

Thresholds are starting hypotheses, not calibrated physical or perceptual
boundaries. The analysis is offline; zero-phase filtering uses future samples.
Signal edges, movement of the phone, route curves and changes in gravity
projection can distort results. There is no speed sensor, so the acceleration
integral is not an independent measurement of train speed. Broad movement can
be mistaken for braking; low variance share warns about competing axes.

## Route counts and direction checks

The bundled `metro_braking/routes.json` reference covers all 16 Paris metro
lines (1–14, 3bis and 7bis), including both branches of lines 7 and 13 and the
asymmetric station order of lines 10 and 7bis. The offline reference was expanded
on 15 October 2026. Links to the published official line diagrams are retained;
live retrieval was unavailable in the development environment for this expansion.

Recording names accept bis variants (`L3bis`, `L3_bis`, `L3b`), accents, common
station aliases, and `St`/`Ste` abbreviations. For example,
`L3_Sentier_-_St-Lazare_-2024-05-03_17-04-03` resolves to Sentier → Bourse →
Quatre-Septembre → Opéra → Havre – Caumartin → Saint-Lazare: **4 intermediate
stops**, or 5 arrivals after departure.

Known recent opening dates are applied to the recording date: the 13 June 2024
line 11 extension, 24 June 2024 line 14 extensions, and 18 January 2025 opening of
Villejuif – Gustave Roussy. A station not yet open is omitted when passing through
it; an unopened endpoint is flagged explicitly. Services requiring a branch
transfer are not invented as direct journeys. Unknown/misspelled stations and
unsupported service patterns remain explicit rather than producing a guessed count.

Examples on the original recordings:

| Line / test commute | Intermediate stations | Arrivals after departure | Source |
| --- | ---: | ---: | --- |
| 4 Les Halles → Montparnasse | 7 | 8 | [Line 4](https://www.transilien.com/fr/page-lignes/metro-4) |
| 4 Châtelet → Montparnasse | 6 | 7 | [Line 4](https://www.transilien.com/fr/page-lignes/metro-4) |
| 4 Étienne Marcel → Montparnasse | 8 | 9 | [Line 4](https://www.transilien.com/fr/page-lignes/metro-4) |
| 6 Raspail → Montparnasse | 1 | 2 | [Line 6](https://www.transilien.com/fr/page-lignes/metro-6) |
| 8 Madeleine → La Motte-Picquet - Grenelle | 4 | 5 | [Line 8](https://www.transilien.com/fr/page-lignes/metro-8) |
| 13 Montparnasse → Pernety | 1 | 2 | [Line 13](https://www.transilien.com/fr/page-lignes/metro-13) |

Les Halles → Montparnasse is: Les Halles, Châtelet, Cité, Saint-Michel, Odéon,
Saint-Germain-des-Prés, Saint-Sulpice, Saint-Placide, Montparnasse - Bienvenüe.
Arrivals exclude the origin and include the destination; intermediate stations
exclude both endpoints. Recording-edge dwells and gap-edge dwells are counted
separately. Only interior candidates are compared with intermediate stations.
The reference models regular service paths and known recent openings, not temporary
closures or special services. It is not a complete history of the network. A matching count does not prove
station identities, and mismatches never cause candidates to be added or removed.

For orientation, examine 14-second raw acceleration windows before arrival and
after departure, relative to each dwell's local baseline. Arrival should be
negative and departure positive under the folder convention. Opposite evidence
or PCA alignment below 0.7 raises an inconsistency flag. Weak evidence remains
unverified. These checks can also be affected by false stop candidates and phone
movement; a flag is not proof that the phone was reversed. Direction is never
silently flipped. If a flag is raised, both acceleration signs are retained for
review. `orientation_checks.csv` contains the individual checks.

## Review and rerun

The report has browser stop-review controls: **Accept**, **Discard**, **Pending**,
editable arrival/restart times and notes, a signal view that zooms around the selected
candidate, and live accepted counts alongside the expected intermediate count.
**Accept all** accepts all pending candidates in one recording in a single save,
including short candidates. Discarded stops stay discarded; edited times and
notes are preserved. The button is disabled when no decisions remain pending.

Yellow **Pending decisions (n)** and green **Review complete** badges update
immediately in each review and in the route-check table. Review complete means
no pending decisions; count mismatches and quality warnings remain independent.

The main report retains Global Statistics and same-interstation comparisons.
The Exploratory comparison and restart-extrema overview sections are removed.
Detailed filtered run signals appear at the end, after recording reviews.

The Route and direction checks table uses `accepted_interior_stops` and refreshes
its count, pending status and red mismatch highlighting immediately with each
review edit or manual stop addition. Hover its count to see the original automatic
detection count. Saved CSV decisions also populate the initial static table.
All candidates start pending, including those passing automatic detection.
You can inspect and accept a short candidate manually. Diagnostics remain
available under an expandable section for each recording.

Shading, focus zoom, interval labels and durations follow edited arrival/restart
times immediately. Solid blue marks the selected arrival; dashed blue marks its
restart. Hover an automatically detected candidate name to see its original
bounds. An extended accepted interval remains visible over pending or discarded
candidates that it replaces. Export sections to refresh the run metrics.

For automatic disk saves, run from this directory:

```powershell
python -m metro_braking --serve
# With the existing workstation environment:
& '..\Metro\.conda\python.exe' -m metro_braking --serve
```

Open the printed address (default `http://127.0.0.1:8765/report.html`). Each choice
is saved to `reviews/stops.json` and `reviews/stops.csv`, outside generated output.
Browser choices survive refreshes and server restarts. Candidate IDs are based
on recording and dwell boundaries, so rerunning with a different duration cutoff
preserves decisions for unchanged candidates. Changed boundaries require review.
You can choose `--port` and `--review-dir` when starting the server.

**Export sections from accepted stops** creates processed signal CSVs between
accepted arrival times, preserving gap block IDs. At least two accepted stops
per recording are needed. The downloadable `reviewed_sections.csv` indexes them.
Pending and discarded stops are excluded. You can also use saved browser reviews
with the existing batch workflow:

```powershell
python -m metro_braking --stops reviews/stops.csv --output results_reviewed
```

Each interstation run between two accepted stop times produces independent acceleration
and deceleration maxima over the same window: the first **30 seconds** starting 0.5 seconds after
the restart by default. Set `trip_window_seconds` in `config.json` to change the
common duration. Neither peak depends on the timing or presence of the other.
The restart is the end of the accepted stop interval, not its arrival time.
Set `trip_restart_offset_seconds` (default 0.5) to change the delay before analysis.
Both graphs use restart time as t=0, so the default measurement window is
[0.5, 30.5) seconds on that axis. Station dwell and the first 0.5 seconds are excluded.
For a stop spanning 131.8–159.3 s, the restart is 159.3 s and analysis covers
[159.8, 189.8) s in the recording. The next accepted arrival still bounds the interstation run.
Review controls expose editable arrival and restart times. New stop CSV exports
include `restart_s` and `candidate_id`. Legacy arrival-only CSVs are supported
when an arrival uniquely matches a detected stop start (including one-decimal
rounding); its detected end supplies the restart. If the match is ambiguous or
missing, re-export decisions or add an explicit `restart_s`; arrival is never
silently used as restart.
`trip_acceleration.csv` records the signed maximum filtered forward acceleration,
its timestamp, the positive magnitude of the most negative filtered acceleration,
and its timestamp. No negative acceleration yields `no_deceleration` and an empty
deceleration value. A negative-only window can still have a deceleration measurement.
Trips shorter than the configured duration and windows with missing samples or
gaps are flagged and excluded from both comparison plots, `trip_acceleration.png`
and `trip_deceleration.png`. Positive acceleration means propulsion under the
configured axis convention; check orientation flags before comparing recordings.
Without a following validated arrival, the final run is marked partial. A matched route can still expose its departure extrema if the analysis window is complete; otherwise at least two accepted stops are needed for a bounded run.

Recording titles use readable station names, local recording date and time, and
parenthesized metadata, for example `L4 Châtelet – Montparnasse 18h03, 29/03/2024
(brutal)`. Raw recording identifiers, filenames and review keys remain unchanged.
When accepted interior stop counts match the route, with no gaps or ambiguous
edge assignments, the stops receive station names in route order. Reports show
`Station names inferred from stop order`. A mismatch keeps neutral run labels.

For six accepted interior stops on Châtelet–Montparnasse, five complete runs are
bounded by those stops, starting with Cité–Saint-Michel. Two additional partial
portions are displayed: Châtelet–Cité has no identified departure and yields no
extrema; Saint-Placide–Montparnasse has no identified arrival but can yield
extrema if the validated restart has a complete analysis window before recording
ends. Neither partial portion contributes to running-duration statistics. Their
charts explicitly identify coverage and the time origin; no endpoint stop is invented.

`run_durations.csv` and each chart show running duration (restart to next arrival),
mean, sample standard deviation, comparable sample count and a diagnostic status.
Only complete station-assigned runs on the same line and directed station pair
are pooled. At least three comparable runs are needed for a warning when the
absolute deviation from the group mean exceeds twice the sample standard deviation.
The tested run is included in that mean. Small groups are marked insufficient;
warnings are diagnostics for review, not automatic rejection or reassignment.
Warnings are visible in collapsed chart headings and the contents navigation.
Statistics and station labels are rebuilt from all current saved decisions on export.

To restore a missed stop, click **Add manual stop** in its recording's review
panel. Drag across the signal to fill arrival/restart timestamps, or type both
values (seconds since recording start). Check the boundaries and choose
**Add and accept stop**. The new row is marked manual, sorted by time, and counted
immediately; its times and note remain editable and **Discard** excludes it.
Manual intervals must have positive duration, lie inside the recording and not
overlap another accepted stop. **Cancel** saves nothing.

With the review server, manual stops persist in `reviews/stops.json` and
`reviews/stops.csv`; browser-only mode saves them in local storage and the
Download CSV export. Manual IDs survive edits and analysis reruns. CSV rows
include `manual=true`, `candidate_id`, `time_s` and `restart_s`; preserve those
columns when reprocessing with `--stops`. Regenerated reports also embed manual
stop definitions, so they remain visible in static reports without the old
browser cache. Export sections after editing to refresh station names, extrema,
pre-arrival braking and duration warnings. Adding a stop never changes the
signal-processing thresholds or automatically validates other candidates.

The report also measures **maximum pre-arrival deceleration** independently of
the departure metrics: the positive magnitude of the most negative filtered
forward acceleration in `[next accepted arrival - 20 s, next accepted arrival)`.
Set `arrival_window_seconds` in `config.json` to change this duration. The full
window must be observed, remain within the run, and contain no signal gaps.
A purple marker and purple shading identify the measurement and its window;
a purple line marks the next accepted arrival. The Before arrival button zooms
to this window. Values, peak timestamp, window bounds and status are exported in
`trip_acceleration.csv` as `max_arrival_deceleration_mps2`,
`arrival_deceleration_peak_time_s` and `arrival_deceleration_status`.
A partial start can still have a measured pre-arrival deceleration when its last
20 seconds are recorded. A partial end with no validated arrival yields
`arrival_not_identified`, not a measurement anchored arbitrarily to recording end.
In particular the supplied L13 file ends at about 155.64 s before a Pernety stop
is identifiable; the plot cannot be extended beyond the recorded samples.
Review station stop charts initially show the full recording; selecting a stop
or using Zoom to selected stop still enables focused review.

The report starts with a clickable table of contents linking to each section,
recording review, diagnostic panel and individual interstation run. Links open
collapsed target panels automatically; the sticky Contents link returns to the top.

The report includes a filtered signal chart for each validated interstation run, with blue
acceleration and orange deceleration markers at the exact measured extrema.
Time is relative to the restart; shading marks the configured measurement
window. Expand an interstation run, drag across the chart or use the mouse wheel to zoom.
Numeric time bounds, Zoom in/out, Analysis window and Full interstation run buttons provide
keyboard-accessible controls. Gaps remain separate signal blocks. Batch charts
work without a server; exporting current browser decisions refreshes the charts.

The browser's **Export sections from accepted stops** action also refreshes
this CSV and plot from current accepted decisions. Pending and discarded stops
are excluded. With no reviewed trips, the plot explicitly shows no measurements.

Opening `results/report.html` directly also works without a server: choices are
saved in that browser's local storage, and **Download reviewed stops CSV** exports
them without manual editing. Direct section exports and disk autosaves require
server mode. CSV exports include accepted/discarded decisions; pending candidates
are omitted. Browser review concerns station stops; braking labels remain separate.

Inspect the plots before interpreting any ranking. Red/blue shaded regions are
negative/positive lobes; green marks qualifying dwell intervals and orange marks
short intervals excluded by the minimum duration. The last panel shows XYZ
variation in green and longitudinal baseline distance in gray. Purple dashed lines show
original SensorLogger Stop annotations.

Copy `results/review_template.csv` to `review.csv`. Correct start/end times,
remove unwanted rows or set `accepted=false`, add missed events, and label
confirmed braking events (`comfortable`, `strong`, `too harsh`, or your own
labels). `sign` is the sign of the plotted acceleration during that event.
A recording with any review rows uses those rows instead of automatic candidates;
reviewing every candidate as rejected produces no events for that recording.
Accepted review rows assert a human-confirmed braking event. Keep review files
outside generated output directories to avoid overwriting your work.

```powershell
python -m metro_braking --reviews review.csv --output results_reviewed
```

For station-to-station sections, copy `results/stop_review_template.csv` to
`stops.csv`, correct or add stop times and set `accepted=true` only for confirmed
stops. Quiet interval starts are suggestions and may occur after the actual
arrival. Supply at least two accepted boundaries per recording to export a
section. Sections preserve original elapsed timestamps and gap block IDs.

```powershell
python -m metro_braking --reviews review.csv --stops stops.csv --output results_reviewed
```

`sections.csv` indexes the section CSVs and flags sections spanning gaps.
If reviewed events have labels, `label_summary.csv` compares their metric
distributions without fitting an unsupported perceptual threshold.

To supply known phone orientation, add a recording-name key to `recordings` in
`config.json`, for example:

```json
"recordings": {
  "EXACT_RECORDING_FOLDER_NAME": {"axis": [0, 1, 0], "polarity": -1}
}
```

The axis is a normalized XYZ vector. Polarity makes positive acceleration mean
forward propulsion, so braking is negative. `polarity: 0` leaves sign unresolved.
When changing polarity, review event signs again against the new plots.

Outputs: recording and sensor inventories (including separate stop source and
gap count), processed braking and stop-variation time series, candidate or
reviewed event metrics, quiet intervals, review template, comparison table and
plot, per-recording diagnostics and source manifest. All metrics use SI units
and seconds since the original recording start. Recording-name `brutal`/`smooth`
hints are recorded only at trip level; they do not label individual events.
`short_dwell_candidates.csv` records quiet intervals excluded by minimum
duration. They are also shown in expandable tables under each recording's
diagnostics, so route mismatches can be investigated.

## Research boundary

The current output supports review and comparison, not a perceptual threshold
or a passenger-safety conclusion. Establishing a threshold requires confirmed
orientation, reviewed braking boundaries, enough event-level comfort labels,
and checks that results generalize across trips, phone placements and devices.
Route references support expected counts. Station names are inferred in route order only after human acceptance produces a matching stop count without ambiguous gaps or endpoint assignments; automatic detections alone do not establish station identity.

Tests cover known synthetic braking, filter response, gravity fallback,
unresolved direction, Y/Yinv and opposite signs, minimum dwell and short pauses,
brief disturbances, route counts, gap isolation and reviewed-boundary handling. Run results
on the supplied recordings demonstrate execution, not detection accuracy.

## Recording quality and archiving

The report warns about timestamp gaps separately for the acceleration signal and
uncalibrated stop detection signal. It lists each missing interval, estimated
missing time (subtracting one normal sample interval per gap), and its percentage
of that signal's observed recording span. The existing `gap_seconds` configuration
sets the detection threshold. This detects interrupted sampling; it does not
establish the cause or detect every kind of sensor failure.

When serving a newly generated report, **Archive recording** moves its source
folder from `data_test` to sibling `data_test_archive` and rebuilds the report and
comparisons. **Archived recordings → Restore recording** reverses the move and
rebuilds again. Reviews are retained, including after editing other recordings.
A failed rebuild cancels the move. This requires the review server and access to
the source directory; regenerate older reports once to add that directory metadata.
Rebuilding can take a moment for a large dataset.

If a recording is removed externally, its CSV review rows are now ignored with a
console warning rather than failing with “Stop file references unknown recordings”.
The input CSV is not changed; restore the source folder to reuse those reviews.
`manifest.json` records ignored recording names. `--allow-empty` allows regeneration
when all recordings are archived.

## Comparing the same interstation run

**Compare the same interstation run** overlays filtered acceleration for repeated
complete runs on the same line and directed station pair. Select an interstation;
all curves start at their validated restart, without stretching time to align
arrivals. Runs with unknown departures, unknown arrivals, ambiguous station names
or gaps are excluded. Each group needs at least two eligible runs.

The legend identifies each recording and its running duration. Uncheck a recording
to hide it, or click its name to inspect its individual run. Drag, scroll, enter
time bounds, or use the zoom buttons to inspect the comparison. **First 30 s**
focuses on departures; **Full runs** restores the full time range. Comparisons
update when sections are exported after reviewing stops.

## Global Statistics

At the beginning of the report, paired boxplots pool maximum filtered deceleration
magnitudes by metro line: the configured departure window (default restart +0.5
to +30.5 seconds) and pre-arrival window (default last 20 seconds). All directions
and interstations contribute. Each phase uses its own valid measurements, so a
partial run can contribute one phase when that window is complete. Missing,
incomplete, invalid, or no-deceleration windows do not contribute a zero.

Dots show individual runs, boxes span the 25th–75th percentiles, the center line
is the median, and whiskers extend to the most extreme observations within 1.5
interquartile ranges. Every group displays its valid sample count, including zero.
`global_statistics.csv` contains counts, exclusions and distribution summaries.
The plots refresh on section export and when recordings are archived or restored.

## Incremental processing and saved validations

Analysis now saves each successful recording separately. On later runs it checks
source content, the recording's own stop/event reviews, processing parameters,
analysis code and dependency versions. Unchanged recordings reuse their filtered
signals, detections, metrics and diagnostic plots. Adding a new recording only
processes that recording; its position in the sorted report does not change the
identity of previous results. Global plots/statistics and report pages are always
rebuilt to include the current dataset.

Stop decisions saved by the review server remain in `reviews/stops.json` and
`reviews/stops.csv`. If `--stops` is omitted, the CLI automatically uses
`--review-dir/stops.csv` when present (`reviews/stops.csv` by default). An explicit
`--stops` still selects another file. With this default workflow, after adding
recording folders simply rerun:

```powershell
python -m metro_braking --output results_reviewed
```

The console and report show how many recordings were reused or recomputed.
Changing one recording's reviews or source data only invalidates that recording;
changes to common processing parameters invalidate every affected recording.
Rendering changes alone do not invalidate the analysis cache. Archived recordings
keep their cache and reviews available for restoration. Raw source files stay in
their original folders; reusing saved results does not move or delete them.

The first run after this update must build the cache. Default storage is
`OUTPUT/.recording_cache`; use `--cache-dir PATH` to share results between report
folders. Snapshots are checked for missing/corrupt artifacts and rebuilt when
necessary. `--reprocess` forces recalculation of all active recordings. Cache
snapshots can be deleted to reclaim disk space; keep the review files to preserve
validation decisions. `manifest.json` records the cache path and processing summary.

On Windows, transient file-access locks during cache publication are retried.
If the cache still cannot be saved, successful analysis artifacts remain in the
report; the console reports `Cache save skipped` and the recording may be
recomputed next time. A genuine recording failure is listed in the report, while
its review rows remain saved for a later retry. To place the cache outside a
Documents/sync folder, use `--cache-dir "$env:LOCALAPPDATA\MetroCodex\cache"`
in PowerShell.

## GitHub Pages demo

`demo/` is a standalone snapshot with departure/arrival boxplots, aggregate
statistics CSV, and **Compare the same interstation run**. Comparisons load on
scroll and use the same renderer as the report: choose an interstation, hide
individual curves, enter time bounds or zoom. The snapshot currently includes
59 complete runs across 12 repeated directed interstations.

Only filtered samples within comparable runs, relative times and readable recording
labels are included for the interactive charts. Raw recordings, full recording
traces, review decisions and review controls are not published. Comparison legends
have no links to the local report's individual-run sections.

To enable the site once, choose **Settings → Pages → Build and deployment →
Source: GitHub Actions**. Then choose **Actions → Publish metro demo → Run
workflow** on `main`. The workflow publishes only `demo/` on pushes to `main` that change the demo or
its workflow. Manual publication remains available. Analysis code changes alone
do not replace the snapshot.
The site URL is `https://ericcabrol.github.io/Metro_Codex/` after successful deployment.

To update the snapshot after generating/reviewing a report:

```powershell
python scripts/build_pages_demo.py --report results_reviewed
```

Commit the updated files in `demo/` and push to `main` to publish the new snapshot.
For local inspection, serve that folder with `python -m http.server 8000 --directory demo`.
