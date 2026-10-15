import argparse
import html
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .routes import CATALOG, display_recording_name
from .review import report_payload, serve_report
from .trip_acceleration import write_trip_acceleration
from .runs import duration_statistics
from .manual_stops import initial_decisions_from_rows, review_candidates
from .recordings import available_review_rows
from .processing import analyze_recording
from .cache import RecordingCache, source_files, processing_version, recording_signature


def validate_config(config):
    arrival_window = config.get("arrival_window_seconds", 20)
    if not np.isfinite(arrival_window) or arrival_window <= 0:
        raise ValueError("arrival_window_seconds must be finite and positive")
    offset = config.get("trip_restart_offset_seconds", 0.5)
    if not np.isfinite(offset) or offset < 0:
        raise ValueError("trip_restart_offset_seconds must be finite and nonnegative")
    duration = config.get("trip_window_seconds", 30)
    if not np.isfinite(duration) or duration <= 0:
        raise ValueError("trip_window_seconds must be finite and positive")
    for name in ("sample_hz", "cutoff_hz", "gap_seconds", "event_threshold_mps2",
                 "minimum_event_seconds", "quiet_threshold_mps2", "minimum_stop_seconds", "stop_level_tolerance_mps2"):
        if not np.isfinite(config[name]) or config[name] <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if not np.isfinite(config.get("stop_window_seconds", 1.0)) or config.get("stop_window_seconds", 1.0) <= 0:
        raise ValueError("stop_window_seconds must be finite and positive")
    if not 0 < config.get("stop_min_quiet_fraction", 0.9) <= 1:
        raise ValueError("stop_min_quiet_fraction must be between zero and one")
    interruption = config.get("stop_max_interruption_seconds", 1.5)
    if not np.isfinite(interruption) or not 0 <= interruption < config["minimum_stop_seconds"]:
        raise ValueError("stop_max_interruption_seconds must be nonnegative and shorter than a stop")
    cutoffs = config["sensitivity_cutoffs_hz"] + [config["cutoff_hz"]]
    if not cutoffs or any(not np.isfinite(c) or not 0 < c < config["sample_hz"] / 2 for c in cutoffs):
        raise ValueError("All cutoffs must be positive and below target Nyquist")


def write_csv(rows, path, columns=None):
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def report_contents(summaries, has_failures):
    """Build navigation for report sections and individual recording reviews."""
    links = [("global-statistics", "Global Statistics"),
             ("report-guide", "Reading the report"),
             ("route-checks", "Route and direction checks"),
             ("event-comparison", "Exploratory comparison"),
             ("restart-extrema", "Acceleration and deceleration after validated restarts"),
             ("run-comparisons", "Compare the same interstation run"),
             ("trip-signals", "Filtered signal for each validated interstation run"),
             ("archived-recordings", "Archived recordings")]
    items = ''.join(f'<li><a href="#{target}">{label}</a></li>' for target, label in links)
    if has_failures:
        items += '<li><a href="#failed-recordings">Failed recordings</a></li>'
    recordings = ''.join(
        f'<li><a href="#recording-{i}">{html.escape(display_recording_name(summary["recording"]))}</a>'
        f'<ul><li><a href="#stop-review-{i}">Review station stops</a></li>'
        f'<li><a href="#diagnostics-{i}">Detection tables and full diagnostic plots</a></li></ul></li>'
        for i, summary in enumerate(summaries))
    return ('<nav id="report-contents" aria-label="Table of contents"><h2>Contents</h2>'
            f'<ul>{items}</ul><details><summary>Interstation runs</summary><div id="run-contents"></div></details>'
            f'<details><summary>Recordings and stop reviews</summary><ul>{recordings}</ul></details></nav>')


def route_checks_table(summaries):
    columns = ["recording", "expected_intermediate_stops", "detected_interior_stops",
               "short_dwell_candidates", "stop_count_status", "orientation_status"]
    rows = []
    for i, summary in enumerate(summaries):
        expected = summary.get("expected_intermediate_stops")
        detected = summary.get("detected_interior_stops")
        mismatch = pd.notna(expected) and pd.notna(detected) and expected != detected
        cells = [f'<td><a href="#stop-review-{i}">{html.escape(display_recording_name(summary["recording"]))}</a></td>']
        cells.extend(f'<td>{html.escape(str(summary.get(column, "")))}</td>' for column in columns[1:])
        row_class = ' class="stop-count-mismatch"' if mismatch else ""
        rows.append(f'<tr{row_class}>{"".join(cells)}</tr>')
    headings = "".join(f'<th scope="col">{column}</th>' for column in columns)
    return f'<table class="route-checks-table"><thead><tr>{headings}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def continuity_notice(summary):
    notices = []
    for title, prefix, duration in (("Acceleration signal", "", summary["duration_s"]),
                                     ("Stop detection signal", "stop_", summary.get("stop_duration_s", 0))):
        intervals = summary.get(prefix + "gap_intervals", [])
        if not intervals:
            continue
        missing = summary[prefix + "estimated_missing_s"]
        percent = 100 * missing / duration if duration else 0
        rows = "".join(f'<li>{g["start_s"]:.2f}–{g["end_s"]:.2f} s ({g["duration_s"]:.2f} s)</li>' for g in intervals)
        notices.append(f'<p><strong>{title}: {len(intervals)} gap(s); estimated missing time {missing:.1f} s ({percent:.1f}%).</strong></p>'
                       f'<details><summary>Missing intervals (recording timestamps)</summary><ul>{rows}</ul></details>')
    if not notices:
        return ""
    return '<aside class="continuity-warning"><strong>Recording quality warning: missing samples</strong>' + "".join(notices) + '<p>Measurements crossing gaps are unavailable. Check this recording before comparing runs.</p></aside>'


def report(output, summaries, events, failures, quiet, rejected_dwells, config, series, trips=None, stops=None, data_directory=None, processing=None):
    event_frame = pd.DataFrame(events)
    cells = []
    for i, summary in enumerate(summaries):
        own = [e for e in events if e["recording"] == summary["recording"]]
        own_stops = pd.DataFrame([q for q in quiet if q["recording"] == summary["recording"]])
        stop_table = own_stops[["start_s", "end_s", "duration_s", "quiet_fraction", "boundary"]].to_html(index=False, float_format=lambda x: f"{x:.2f}") if not own_stops.empty else "<p>No qualifying dwell detected.</p>"
        rejected = pd.DataFrame([q for q in rejected_dwells if q["recording"] == summary["recording"]])
        short_table = '<details><summary>Short quiet intervals excluded by minimum duration</summary>' + rejected[["start_s", "end_s", "duration_s", "boundary"]].to_html(index=False, float_format=lambda x: f"{x:.2f}") + '</details>' if not rejected.empty else ""
        route = html.escape(summary.get("route_stations", summary["route_status"]))
        cells.append(f'<article id="recording-{i}"><h2>{html.escape(display_recording_name(summary["recording"]))}</h2>'
                     f'<p>{route}</p>'
                     f'<p>{summary["duration_s"]:.1f} seconds; {summary["median_sample_hz"]:.1f} Hz; '
                     f'{len(own)} candidates/events. {html.escape(summary["quality_flags"])}</p>'
                     f'<p>{html.escape(summary["stop_count_status"])}</p>'
                     f'{continuity_notice(summary)}'
                     f'<div id="stop-review-{i}" class="stop-review" data-review-recording="{i}"></div>'
                     f'<details id="diagnostics-{i}"><summary>Detection tables and full diagnostic plots</summary>{stop_table}{short_table}'
                     f'<a href="plots/{i:02d}.png"><img src="plots/{i:02d}.png" alt="Signal diagnostics"></a></details></article>')
    comparison = ""
    if events:
        grouped = event_frame.groupby("recording").agg(event_count=("event_id", "count"),
                largest_candidate_peak_mps2=("peak_mps2", "max"),
                largest_candidate_jerk_p95_mps3=("jerk_p95_mps3", "max"))
        grouped.to_csv(output / "comparison.csv")
        comparison = grouped.rename(index=display_recording_name).to_html(float_format=lambda x: f"{x:.3f}")
        fig, ax = plt.subplots(figsize=(12, 6), constrained_layout=True)
        for summary in summaries:
            own = event_frame[event_frame.recording == summary["recording"]]
            if own.empty:
                continue
            ax.scatter(own.peak_mps2, own.jerk_p95_mps3, alpha=0.55, label=display_recording_name(summary["recording"]))
        ax.set(xlabel="Candidate peak acceleration (m/s²)", ylabel="Candidate jerk, 95th percentile (m/s³)",
               title="Exploratory event comparison — candidates require review")
        ax.legend(fontsize=5, loc="upper left")
        ax.grid(alpha=0.2)
        fig.savefig(output / "comparison.png", dpi=140)
        plt.close(fig)
    text = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Metro braking analysis</title><link rel="stylesheet" href="report_ui.css">'
    text += '<style>body{font:16px system-ui;max-width:1400px;margin:30px auto;padding:0 20px;color:#17202a}img{max-width:100%}table{border-collapse:collapse;font-size:12px}td,th{padding:6px;border:1px solid #ddd}article{margin:40px 0} .warning{background:#fff3cd;padding:18px}</style>'
    text += '<h1>Metro braking exploration</h1>'
    if processing is not None:
        text += f'<p>Incremental processing: <strong>{len(processing["reused"])} recording(s) reused</strong>, {len(processing["processed"])} recomputed. Source files and reviews are checked before reuse; aggregate statistics and the report are refreshed.</p>'
    text += report_contents(summaries, bool(failures))
    text += '<div class="review-global"><a href="#report-contents">Contents</a><button id="download-stops" type="button">Download reviewed stops CSV</button><button id="export-sections" type="button" disabled>Export sections from accepted stops</button><a id="section-index" hidden>Open section index</a><p id="review-save-status" role="status">Loading review controls…</p></div>'
    text += f'<section id="global-statistics"><h2>Global Statistics</h2><p>Maximum filtered deceleration magnitude for each run, grouped by metro line. Orange: {config.get("trip_window_seconds", 30):g} seconds starting {config.get("trip_restart_offset_seconds", .5):g} s after validated restart. Purple: the last {config.get("arrival_window_seconds", 20):g} seconds before the next validated arrival. Each window contributes independently when valid, including on partial runs; missing or incomplete windows and windows without negative acceleration are excluded. All directions and interstations on a line are pooled.</p><img id="global-statistics-plot" src="global_statistics.png" alt="Boxplots of maximum departure and pre-arrival deceleration for each metro line"><p>Box: 25th–75th percentiles; center line: median; whiskers: extreme values within 1.5 times the interquartile range. Dots show every included run; n is the number of valid measurements. Small samples remain visible and should be interpreted cautiously. <a href="global_statistics.csv">Download statistics and excluded counts CSV</a>.</p></section>'
    text += '<section id="archived-recordings"><h2>Archived recordings</h2><div id="archive-list">Start the review server to archive or restore recordings.</div></section>'
    text += '<h2 id="report-guide">Reading the report</h2>'
    text += f'<p class="warning">Y points forward by convention; Yinv reverses it. PCA alignment and motion around dwells check that convention, with inconsistencies flagged rather than silently corrected. Stop candidates require at least {config["minimum_stop_seconds"]:g} seconds of observed dwell. Route counts are independent checks, not detection targets. No perceptual threshold or safety conclusion is established.</p>'
    text += '<p>Red shading: expected braking; blue: opposite-sign events retained when direction is uncertain; green: qualifying dwell candidates; orange: short quiet intervals excluded from counts; purple dashed lines: original SensorLogger Stop annotations. Stops use uncalibrated XYZ variation (green trace) and longitudinal distance from a quiet-window baseline (gray trace), with a trailing one-second window. Disturbances of up to 1.5 seconds may be bridged; recording gaps never are. At least 90% of each candidate must be quiet. Steady motion can still produce a false positive.</p>'
    text += '<p>Expected arrivals exclude the origin and include the destination. Intermediate stops exclude both endpoints. The count check compares interior candidates with intermediate stations; endpoint dwells may be truncated, and gaps or passenger movement may hide stops. Accepted stops with a matching count are assigned station names in route order, as checked during human review.</p>'
    text += '<p>Use Accept, Discard or Pending below to review station stops. Arrival and restart times are editable. Rolling-window onset can lag the actual arrival. Download reviewed stops CSV exports decisions without manual editing. For automatic disk saves and section exports, run <code>python -m metro_braking --serve</code> and open the printed local URL. Braking-event labels remain a separate workflow.</p>'
    text += '<h2 id="route-checks">Route and direction checks</h2>' + route_checks_table(summaries)
    text += '<p>Full sampling and axis details: <a href="inventory.csv">inventory.csv</a>. Direction evidence: <a href="orientation_checks.csv">orientation_checks.csv</a>.</p>'
    text += '<p>Station-order reference (version ' + CATALOG['updated_on'] + '): ' + ', '.join(f'<a href="{line["source"]}">Transilien line {number}</a>' for number, line in CATALOG["lines"].items()) + '. All 16 Paris metro lines are covered, including branches and directional loops. Known recent opening dates are applied to the recording date; temporary closures and special services are not modeled.</p>'
    text += '<h2 id="event-comparison">Exploratory comparison</h2>' + comparison
    text += f'<h2 id="restart-extrema">Acceleration and deceleration after validated restarts</h2><p>Independent maximum signed filtered forward acceleration and maximum deceleration magnitude over {config.get("trip_window_seconds", 30):g} seconds starting {config.get("trip_restart_offset_seconds", 0.5):g} seconds after each validated restart (end of the accepted stop). Both use the same window; station dwell is excluded. Short trips and windows with missing samples are excluded. No negative acceleration is recorded as no_deceleration. Inspect orientation flags before interpretation.</p><p><a href="trip_acceleration.csv">Download interstation run acceleration and deceleration CSV</a></p><img src="trip_acceleration.png" alt="Maximum acceleration after validated restarts"><img src="trip_deceleration.png" alt="Maximum deceleration after validated restarts">'
    if events:
        text += '<img src="comparison.png" alt="Candidate intensity and jerk comparison">'
    if failures:
        text += '<h2 id="failed-recordings">Failed recordings</h2><pre>' + html.escape(json.dumps(failures, indent=2)) + '</pre>'
    payload = report_payload(summaries, quiet, rejected_dwells, config, series)
    initial = initial_decisions_from_rows(payload, stops)
    payload['candidates'] = review_candidates(payload, initial)
    payload['initial_decisions'] = initial
    if data_directory is not None:
        payload["data_directory"] = str(data_directory)
    payload["trips"] = trips or []
    if processing is not None:
        payload["processing"] = processing
    for recording in payload["recordings"]:
        recording["display_name"] = display_recording_name(recording["recording"])
    text += '<section id="run-comparisons"><h2>Compare the same interstation run</h2><p>Overlay complete validated runs on the same line and directed station pair. Time zero is the validated restart; time is not stretched to match arrivals. Partial runs and runs without confirmed station assignments are excluded. Use the checkboxes to hide individual signals, drag or scroll to zoom, and click a recording name to inspect its run.</p><div id="run-comparison-charts"></div></section>'
    text += '<section id="trip-signals"><h2>Filtered signal for each validated interstation run</h2><p>Blue: departure-window maximum acceleration. Orange: departure-window maximum deceleration. Purple: maximum deceleration in the configured window before the next validated arrival (20 seconds by default). Blue and purple shading identify the two measurement windows; a purple vertical line marks the next arrival. If arrival is not identified, no pre-arrival measurement is inferred from the recording end. Drag across a chart or use the mouse wheel to zoom in time; exact time bounds and reset buttons are also available.</p><p>Station names inferred from stop order when accepted stop counts match the route. Partial edge runs are identified explicitly. Running durations exclude station dwell; warnings compare the same directed interstation on the same line, using the sample standard deviation and at least three complete runs. Fewer samples are marked insufficient. The rule is a diagnostic, not proof of an incorrect station assignment.</p><p><a href="run_durations.csv">Download running-duration statistics</a></p><div id="trip-charts"></div></section>'
    encoded = json.dumps(payload, allow_nan=False, default=lambda v: v.item(), separators=(",", ":"))
    (output / "report_data.json").write_text(encoded, encoding="utf-8")
    text += ''.join(cells) + '<script id="review-data" type="application/json">' + encoded.replace("<", "\\u003c") + '</script><script src="run_comparison.js"></script><script src="trip_charts.js"></script><script src="report_ui.js"></script></html>'
    for asset in ("report_ui.js", "report_ui.css", "run_comparison.js", "trip_charts.js"):
        shutil.copyfile(Path(__file__).with_name(asset), output / asset)
    (output / "report.html").write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Analyze copied SensorLogger recordings")
    parser.add_argument("--allow-empty", action="store_true", help="Allow an empty report when all recordings are archived")
    parser.add_argument("--cache-dir", type=Path, help="Reusable per-recording results (default: OUTPUT/.recording_cache)")
    parser.add_argument("--reprocess", action="store_true", help="Recompute recordings even when unchanged cached results exist")
    parser.add_argument("--data", type=Path, default=Path("data_test"))
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--stops", type=Path, help="CSV of reviewed recording,time_s,accepted stop boundaries")
    parser.add_argument("--serve", action="store_true", help="Serve existing report with browser review and disk saves")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--review-dir", type=Path, default=Path("reviews"))
    args = parser.parse_args()
    if args.serve:
        serve_report(args.output, args.review_dir, args.port)
        return
    config = json.loads(args.config.read_text(encoding="utf-8"))
    validate_config(config)
    data, output = args.data.resolve(), args.output.resolve()
    if not data.is_dir():
        parser.error(f"Data directory does not exist: {data}")
    if output == data or data in output.parents or output in data.parents:
        parser.error("Output and source data directories must be separate")
    folders = sorted(p for p in data.iterdir() if p.is_dir())
    if not folders and not args.allow_empty:
        parser.error("No recording folders found")
    reviews = pd.read_csv(args.reviews, keep_default_na=False) if args.reviews else pd.DataFrame()
    stop_file = args.stops
    if stop_file is None and (args.review_dir / "stops.csv").is_file():
        stop_file = args.review_dir / "stops.csv"
        print(f"Using saved stop reviews: {stop_file}", flush=True)
    stops = pd.read_csv(stop_file, keep_default_na=False) if stop_file else pd.DataFrame()
    if stop_file and not {"recording", "time_s", "accepted"}.issubset(stops):
        parser.error("Stops CSV requires recording,time_s,accepted")
    all_stops = stops.to_dict("records")
    all_reviews = reviews.to_dict("records")
    stops, ignored_stops = available_review_rows(stops, {p.name for p in folders}, "stop reviews")
    required_review_columns = {"recording", "event_id", "start_s", "end_s", "sign", "accepted", "label"}
    if args.reviews and not required_review_columns.issubset(reviews):
        parser.error("Reviews must contain: " + ", ".join(sorted(required_review_columns)))
    reviews, ignored_reviews = available_review_rows(reviews, {p.name for p in folders}, "event reviews")
    output.mkdir(parents=True, exist_ok=True)
    (output / "plots").mkdir(exist_ok=True)
    summaries, events, quiet, sensors, failures, manifest = [], [], [], [], [], []
    section_inventory = []
    trip_metrics = []
    trip_charts = []
    orientation_checks = []
    rejected_dwells = []
    series = {}
    cache_directory = (args.cache_dir or output / ".recording_cache").resolve()
    if cache_directory == data or data in cache_directory.parents:
        parser.error("Cache and source data directories must be separate")
    cache = RecordingCache(cache_directory)
    version = processing_version()
    processing = {"reused": [], "processed": []}
    for folder in folders:
        try:
            files = source_files(folder)
            manifest.extend(dict(file=str(Path(folder.name)/row["file"]), bytes=row["bytes"], sha256=row["sha256"]) for row in files)
            signature = recording_signature(folder.name, files, config, reviews, stops, version)
            result = None if args.reprocess else cache.load(folder.name, signature)
            reused = result is not None
            if result is None:
                print(f"Analyzing {folder.name}", flush=True)
                def build(target):
                    computed = analyze_recording(folder, config, reviews, stops, target)
                    if source_files(folder) != files:
                        raise ValueError("Source files changed during analysis; retry with a stable recording")
                    return computed
                result = cache.process(folder.name, signature, build, output=output, index=len(summaries))
            else:
                print(f"Reusing {folder.name}", flush=True)
                cache.materialize(folder.name, signature, result, output, len(summaries))
            processing["reused" if reused else "processed"].append(folder.name)
            summaries.append(result["summary"])
            events.extend(result["events"])
            quiet.extend(result["quiet"])
            sensors.extend(result["sensors"])
            orientation_checks.extend(result["orientation_checks"])
            rejected_dwells.extend(result["rejected_dwells"])
            trip_metrics.extend(result["metrics"])
            trip_charts.extend(result["charts"])
            section_inventory.extend(result["sections"])
            series[folder.name] = result["series"]
        except (ValueError, OSError, KeyError, pd.errors.ParserError) as error:
            failures.append({"recording": folder.name, "error": str(error)})
            print(f"FAILED: {error}", flush=True)
    write_csv(summaries, output / "inventory.csv")
    write_csv(events, output / "events.csv")
    write_csv(quiet, output / "quiet_intervals.csv")
    write_csv(rejected_dwells, output / "short_dwell_candidates.csv")
    write_csv(orientation_checks, output / "orientation_checks.csv", columns=["recording", "stop_start_s", "phase", "mean_forward_mps2", "verdict"])
    write_csv(sensors, output / "sensors.csv")
    write_csv(section_inventory, output / "sections.csv", columns=["recording", "section", "start_s", "end_s", "file", "contains_gap"])
    duration_statistics(trip_metrics)
    write_trip_acceleration(output, trip_metrics, config.get("trip_window_seconds", 30),
                            config.get("trip_restart_offset_seconds", 0.5), config.get("arrival_window_seconds", 20),
                            [r.get("line") for r in summaries])
    write_csv([{"recording": q["recording"], "time_s": q["start_s"], "accepted": False,
                "note": f'{q["duration_s"]:.1f}s dwell, {q["boundary"]}; onset delayed by rolling window; requires review'} for q in quiet],
              output / "stop_review_template.csv", columns=["recording", "time_s", "accepted", "note"])
    labeled = pd.DataFrame([e for e in events if e["reviewed"] and e["label"]])
    if not labeled.empty:
        labeled.groupby("label")[["peak_mps2", "sustained_1s_mps2", "jerk_p95_mps3"]].agg(["count", "median", "min", "max"]).to_csv(output / "label_summary.csv")
    else:
        write_csv([], output / "label_summary.csv", columns=["label", "count"])
    review_rows = [{k: e[k] for k in ("recording", "event_id", "start_s", "end_s", "sign", "label")} | {"accepted": True} for e in events]
    write_csv(review_rows, output / "review_template.csv", columns=["recording", "event_id", "start_s", "end_s", "sign", "accepted", "label"])
    manifest_out = {"cache_directory": str(cache_directory), "processing": processing,
                    "reviewed_stops_file": str(stop_file.resolve()) if stop_file else None, "configuration": config, "source_files": manifest, "failures": failures,
                    "reviews": reviews.to_dict("records"), "reviewed_stops": stops.to_dict("records"),
                    "all_reviews": all_reviews, "all_reviewed_stops": all_stops,
                    "ignored_stop_recordings": ignored_stops, "ignored_review_recordings": ignored_reviews,
                    "route_reference": CATALOG,
                    "versions": {"numpy": np.__version__, "pandas": pd.__version__,
                                 "scipy": __import__("scipy").__version__, "matplotlib": matplotlib.__version__}}
    (output / "manifest.json").write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")
    report(output, summaries, events, failures, quiet, rejected_dwells, config, series, trip_charts, stops, data, processing)
    print(f"Incremental processing: {len(processing['reused'])} reused, {len(processing['processed'])} recomputed", flush=True)
    print(f"Finished: {len(summaries)} recordings, {len(events)} events, {len(failures)} failures. {output / 'report.html'}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
