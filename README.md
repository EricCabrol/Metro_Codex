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

`data_test` is an unchanged copy of **only** `Metro/data_test`: 11 recordings
from lines 4, 6, 8 and 13. The copied legacy `timestamps*.txt` files are retained
as source artifacts but are never read by the pipeline. Original SensorLogger
`Stop` annotations are displayed as visual references, not detection targets.
`manifest.json` records SHA-256 hashes, configuration, reviews, package versions,
and any failures. Data and generated results are ignored by Git.

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

The local `metro_braking/routes.json` reference contains the corridors used by
the test recordings, with published station sequences checked on 30 September
2026. It has partial coverage, supports reverse trips and common folder aliases,
and flags unknown endpoints rather than guessing. The sources are Transilien's
text transcriptions of the RATP line diagrams:

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
The reference is regular topology for these 2024 recordings, not today's service
status. Historical closures are not inferred. A matching count does not prove
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
editable arrival times and notes, a signal view that zooms around the selected
candidate, and live accepted counts alongside the expected intermediate count.
All candidates start pending, including those passing automatic detection.
You can inspect and accept a short candidate manually. Diagnostics remain
available under an expandable section for each recording.

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

Each trip between two accepted stop times produces independent acceleration
and deceleration maxima over the same window: the first **30 seconds** starting 0.5 seconds after
the restart by default. Set `trip_window_seconds` in `config.json` to change the
common duration. Neither peak depends on the timing or presence of the other.
The restart is the end of the accepted stop interval, not its arrival time.
Set `trip_restart_offset_seconds` (default 0.5) to change the delay before analysis.
Both graphs use restart time as t=0, so the default measurement window is
[0.5, 30.5) seconds on that axis. Station dwell and the first 0.5 seconds are excluded.
For a stop spanning 131.8–159.3 s, the restart is 159.3 s and analysis covers
[159.8, 189.8) s in the recording. The next accepted arrival still bounds the trip.
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
The last accepted stop has no following validated trip boundary and produces no
trip measurement. At least two accepted stops per recording are required.

The report includes a filtered signal chart for each validated trip, with blue
acceleration and orange deceleration markers at the exact measured extrema.
Time is relative to the restart; shading marks the configured measurement
window. Expand a trip, drag across the chart or use the mouse wheel to zoom.
Numeric time bounds, Zoom in/out, Analysis window and Full trip buttons provide
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
Route references support expected counts, but station labels are not assigned
automatically to individual detections.

Tests cover known synthetic braking, filter response, gravity fallback,
unresolved direction, Y/Yinv and opposite signs, minimum dwell and short pauses,
brief disturbances, route counts, gap isolation and reviewed-boundary handling. Run results
on the supplied recordings demonstrate execution, not detection accuracy.
