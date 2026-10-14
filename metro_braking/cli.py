import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .analysis import load_recording, detect_events, detect_stops, check_orientation, apply_reviews, sensor_inventory, split_sections
from .routes import route_from_name, compare_stop_count, CATALOG
from .review import report_payload, serve_report
from .trip_acceleration import trip_acceleration, write_trip_acceleration, trip_chart


def validate_config(config):
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


def diagnostic_plot(segments, summary, events, quiet, folder, output, stop_diagnostics):
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), constrained_layout=True, sharex=True)
    for block, s in enumerate(segments):
        for i, name in enumerate(("x", "y", "z")):
            axes[0].plot(s.t, s.filtered_xyz[:, i], label=name if block == 0 else None, lw=0.8)
        axes[1].plot(s.t, s.projected, color="0.8", lw=0.5, label="resampled" if block == 0 else None)
        axes[1].plot(s.t, s.filtered, color="black", lw=1, label="filtered" if block == 0 else None)
        axes[2].plot(s.t, np.gradient(s.filtered, s.t), color="teal", lw=0.7)
    for diagnostic in stop_diagnostics:
        axes[3].plot(diagnostic["t"], diagnostic["variation"], color="darkgreen", lw=0.7)
        axes[3].plot(diagnostic["t"], diagnostic["baseline_distance"], color="0.7", lw=0.6)
        for rejected in diagnostic["rejected_dwells"]:
            axes[3].axvspan(rejected["start_s"], rejected["end_s"], color="orange", alpha=0.12)
    axes[3].set(xlabel="Seconds since recording start", ylabel="Raw variation / baseline distance (m/s²)")
    axes[3].axhline(summary["stop_variation_threshold_mps2"], color="0.4", ls=":", lw=0.8)
    for event in events:
        axes[1].axvspan(event["start_s"], event["end_s"], alpha=0.18,
                       color="red" if event["sign"] < 0 else "blue")
    for q in quiet:
        for ax in (axes[1], axes[3]):
            ax.axvspan(q["start_s"], q["end_s"], color="green", alpha=0.15)
        axes[3].annotate(f'{q["duration_s"]:.0f}s', (q["start_s"], 0), fontsize=7)
    annotation = folder / "Annotation.csv"
    if annotation.exists() and annotation.stat().st_size:
        annotations = pd.read_csv(annotation)
        for _, row in annotations.iterrows():
            if str(row.get("text", "")).lower() == "stop":
                axes[1].axvline(float(row.seconds_elapsed), color="purple", ls="--", lw=1)
                axes[3].axvline(float(row.seconds_elapsed), color="purple", ls="--", lw=1)
    axes[0].set_ylabel("Filtered XYZ (m/s²)")
    axes[1].set_ylabel("Forward acceleration (m/s²)")
    axes[2].set_ylabel("Jerk (m/s³)")
    for ax in axes:
        ax.set_xlabel("Seconds since recording start")
        ax.grid(alpha=0.2)
    axes[0].legend(loc="upper right")
    axes[1].legend(loc="upper right")
    fig.suptitle(summary["recording"] + "\n" + summary["quality_flags"], fontsize=9)
    fig.savefig(output, dpi=120)
    plt.close(fig)


def write_csv(rows, path, columns=None):
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def report(output, summaries, events, failures, quiet, rejected_dwells, config, series, trips=None):
    inventory = pd.DataFrame(summaries)
    event_frame = pd.DataFrame(events)
    cells = []
    for i, summary in enumerate(summaries):
        own = [e for e in events if e["recording"] == summary["recording"]]
        own_stops = pd.DataFrame([q for q in quiet if q["recording"] == summary["recording"]])
        stop_table = own_stops[["start_s", "end_s", "duration_s", "quiet_fraction", "boundary"]].to_html(index=False, float_format=lambda x: f"{x:.2f}") if not own_stops.empty else "<p>No qualifying dwell detected.</p>"
        rejected = pd.DataFrame([q for q in rejected_dwells if q["recording"] == summary["recording"]])
        short_table = '<details><summary>Short quiet intervals excluded by minimum duration</summary>' + rejected[["start_s", "end_s", "duration_s", "boundary"]].to_html(index=False, float_format=lambda x: f"{x:.2f}") + '</details>' if not rejected.empty else ""
        route = html.escape(summary.get("route_stations", summary["route_status"]))
        cells.append(f'<article><h2>{html.escape(summary["recording"])}</h2>'
                     f'<p>{route}</p>'
                     f'<p>{summary["duration_s"]:.1f} seconds; {summary["median_sample_hz"]:.1f} Hz; '
                     f'{len(own)} candidates/events. {html.escape(summary["quality_flags"])}</p>'
                     f'<p>{html.escape(summary["stop_count_status"])}</p>'
                     f'<div class="stop-review" data-review-recording="{i}"></div>'
                     f'<details><summary>Detection tables and full diagnostic plots</summary>{stop_table}{short_table}'
                     f'<a href="plots/{i:02d}.png"><img src="plots/{i:02d}.png" alt="Signal diagnostics"></a></details></article>')
    comparison = ""
    if events:
        grouped = event_frame.groupby("recording").agg(event_count=("event_id", "count"),
                largest_candidate_peak_mps2=("peak_mps2", "max"),
                largest_candidate_jerk_p95_mps3=("jerk_p95_mps3", "max"))
        grouped.to_csv(output / "comparison.csv")
        comparison = grouped.to_html(float_format=lambda x: f"{x:.3f}")
        fig, ax = plt.subplots(figsize=(12, 6), constrained_layout=True)
        for summary in summaries:
            own = event_frame[event_frame.recording == summary["recording"]]
            if own.empty:
                continue
            ax.scatter(own.peak_mps2, own.jerk_p95_mps3, alpha=0.55, label=summary["recording"])
        ax.set(xlabel="Candidate peak acceleration (m/s²)", ylabel="Candidate jerk, 95th percentile (m/s³)",
               title="Exploratory event comparison — candidates require review")
        ax.legend(fontsize=5, loc="upper left")
        ax.grid(alpha=0.2)
        fig.savefig(output / "comparison.png", dpi=140)
        plt.close(fig)
    text = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Metro braking analysis</title><link rel="stylesheet" href="report_ui.css">'
    text += '<style>body{font:16px system-ui;max-width:1400px;margin:30px auto;padding:0 20px;color:#17202a}img{max-width:100%}table{border-collapse:collapse;font-size:12px}td,th{padding:6px;border:1px solid #ddd}article{margin:40px 0} .warning{background:#fff3cd;padding:18px}</style>'
    text += '<h1>Metro braking exploration</h1><div class="review-global"><button id="download-stops" type="button">Download reviewed stops CSV</button><button id="export-sections" type="button" disabled>Export sections from accepted stops</button><a id="section-index" hidden>Open section index</a><p id="review-save-status" role="status">Loading review controls…</p></div>'
    text += f'<p class="warning">Y points forward by convention; Yinv reverses it. PCA alignment and motion around dwells check that convention, with inconsistencies flagged rather than silently corrected. Stop candidates require at least {config["minimum_stop_seconds"]:g} seconds of observed dwell. Route counts are independent checks, not detection targets. No perceptual threshold or safety conclusion is established.</p>'
    text += '<p>Red shading: expected braking; blue: opposite-sign events retained when direction is uncertain; green: qualifying dwell candidates; orange: short quiet intervals excluded from counts; purple dashed lines: original SensorLogger Stop annotations. Stops use uncalibrated XYZ variation (green trace) and longitudinal distance from a quiet-window baseline (gray trace), with a trailing one-second window. Disturbances of up to 1.5 seconds may be bridged; recording gaps never are. At least 90% of each candidate must be quiet. Steady motion can still produce a false positive.</p>'
    text += '<p>Expected arrivals exclude the origin and include the destination. Intermediate stops exclude both endpoints. The count check compares interior candidates with intermediate stations; endpoint dwells may be truncated, and gaps or passenger movement may hide stops. Matching the count does not confirm station identities.</p>'
    text += '<p>Use Accept, Discard or Pending below to review station stops. Arrival times are editable because rolling-window onset can lag the actual stop. Download reviewed stops CSV exports decisions without manual editing. For automatic disk saves and section exports, run <code>python -m metro_braking --serve</code> and open the printed local URL. Braking-event labels remain a separate workflow.</p>'
    columns = ["recording", "expected_arrivals", "expected_intermediate_stops", "detected_interior_stops", "short_dwell_candidates", "stop_count_status", "orientation_status"]
    text += '<h2>Route and direction checks</h2>' + inventory.reindex(columns=columns).to_html(index=False)
    text += '<p>Full sampling and axis details: <a href="inventory.csv">inventory.csv</a>. Direction evidence: <a href="orientation_checks.csv">orientation_checks.csv</a>.</p>'
    text += '<p>Station-order sources, checked 30 September 2026: ' + ', '.join(f'<a href="{line["source"]}">Transilien line {number}</a>' for number, line in CATALOG["lines"].items()) + '. Regular topology is used for the 2024 recordings; current closures are not applied.</p>'
    text += '<h2>Exploratory comparison</h2>' + comparison
    text += f'<h2>Acceleration and deceleration after validated stops</h2><p>Independent maximum signed filtered forward acceleration and maximum deceleration magnitude during the first {config.get("trip_window_seconds", 30):g} seconds after each accepted stop time. Both use the same window, which includes any remaining station dwell. Short trips and windows with missing samples are excluded. No negative acceleration is recorded as no_deceleration. Inspect orientation flags before interpretation.</p><p><a href="trip_acceleration.csv">Download trip acceleration and deceleration CSV</a></p><img src="trip_acceleration.png" alt="Maximum acceleration after validated stops"><img src="trip_deceleration.png" alt="Maximum deceleration after validated stops">'
    if events:
        text += '<img src="comparison.png" alt="Candidate intensity and jerk comparison">'
    if failures:
        text += '<h2>Failed recordings</h2><pre>' + html.escape(json.dumps(failures, indent=2)) + '</pre>'
    payload = report_payload(summaries, quiet, rejected_dwells, config, series)
    payload["trips"] = trips or []
    text += '<section id="trip-signals"><h2>Filtered signal for each validated trip</h2><p>Blue: maximum acceleration. Orange: maximum deceleration. Shading marks the measurement window. Drag across a chart or use the mouse wheel to zoom in time; exact time bounds and reset buttons are also available.</p><div id="trip-charts"></div></section>'
    encoded = json.dumps(payload, allow_nan=False, default=lambda v: v.item(), separators=(",", ":"))
    (output / "report_data.json").write_text(encoded, encoding="utf-8")
    text += ''.join(cells) + '<script id="review-data" type="application/json">' + encoded.replace("<", "\\u003c") + '</script><script src="trip_charts.js"></script><script src="report_ui.js"></script></html>'
    for asset in ("report_ui.js", "report_ui.css", "trip_charts.js"):
        shutil.copyfile(Path(__file__).with_name(asset), output / asset)
    (output / "report.html").write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Analyze copied SensorLogger recordings")
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
    if not folders:
        parser.error("No recording folders found")
    reviews = pd.read_csv(args.reviews, keep_default_na=False) if args.reviews else pd.DataFrame()
    stops = pd.read_csv(args.stops, keep_default_na=False) if args.stops else pd.DataFrame()
    if args.stops and not {"recording", "time_s", "accepted"}.issubset(stops):
        parser.error("Stops CSV requires recording,time_s,accepted")
    if not stops.empty and set(stops.recording) - {p.name for p in folders}:
        parser.error("Stop file references unknown recordings")
    required_review_columns = {"recording", "event_id", "start_s", "end_s", "sign", "accepted", "label"}
    if args.reviews and not required_review_columns.issubset(reviews):
        parser.error("Reviews must contain: " + ", ".join(sorted(required_review_columns)))
    if not reviews.empty and set(reviews.recording) - {p.name for p in folders}:
        parser.error("Review file references unknown recordings")
    output.mkdir(parents=True, exist_ok=True)
    (output / "plots").mkdir(exist_ok=True)
    summaries, events, quiet, sensors, failures, manifest = [], [], [], [], [], []
    section_inventory = []
    trip_metrics = []
    trip_charts = []
    orientation_checks = []
    rejected_dwells = []
    series = {}
    for folder in folders:
        print(f"Analyzing {folder.name}", flush=True)
        for file in sorted(folder.iterdir()):
            if file.is_file():
                manifest.append({"file": str(file.relative_to(data)), "bytes": file.stat().st_size,
                                 "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
        try:
            sensors.extend(sensor_inventory(folder))
            segments, summary = load_recording(folder, config)
            summary.update(route_from_name(folder.name))
            intervals, stop_diagnostics, stop_summary = detect_stops(folder, config)
            summary.update(stop_summary)
            rejected_dwells.extend(q for d in stop_diagnostics for q in d["rejected_dwells"])
            summary["stop_variation_threshold_mps2"] = config["quiet_threshold_mps2"]
            orientation_checks.extend(check_orientation(summary, intervals, stop_diagnostics, config))
            summary.update(compare_stop_count(summary, intervals))
            series[folder.name] = {
                "acceleration": [[[round(float(t), 3), round(float(a), 4)] for t, a in zip(s.t, s.filtered)] for s in segments],
                "stops": [[[round(float(t), 3), round(float(v), 4) if np.isfinite(v) else None,
                            round(float(b), 4) if np.isfinite(b) else None]
                           for t, v, b in zip(d["t"], d["variation"], d["baseline_distance"])] for d in stop_diagnostics]}
            candidates, _ = detect_events(segments, summary, config)
            for candidate in candidates:
                candidate["quiet_follows"] = any(0 <= q["start_s"] - candidate["end_s"] <= 8 for q in intervals)
            candidates = apply_reviews(candidates, segments, summary, config, reviews)
            diagnostic_plot(segments, summary, candidates, intervals, folder, output / "plots" / f"{len(summaries):02d}.png", stop_diagnostics)
            stop_signal = [pd.DataFrame({"seconds_elapsed": d["t"], "block": d["block"],
                            "uncalibrated_variation_mps2": d["variation"],
                            "baseline_distance_mps2": d["baseline_distance"]}) for d in stop_diagnostics]
            stop_output = output / f"stop_signal_{len(summaries):02d}.csv"
            if stop_signal:
                pd.concat(stop_signal).to_csv(stop_output, index=False)
            else:
                write_csv([], stop_output, columns=["seconds_elapsed", "block", "uncalibrated_variation_mps2"])
            signal = pd.concat([pd.DataFrame({"seconds_elapsed": s.t, "block": i,
                         "axis_resampled_mps2": s.projected, "axis_filtered_mps2": s.filtered,
                         "jerk_mps3": np.gradient(s.filtered, s.t)}) for i, s in enumerate(segments)])
            signal.to_csv(output / f"signal_{len(summaries):02d}.csv", index=False)
            if not stops.empty:
                own_stops = stops[stops.recording == folder.name]
                for number, start, end, section in split_sections(signal, own_stops):
                    trip_metrics.append(trip_acceleration(folder.name, number, start, end, section,
                        config.get("trip_window_seconds", 30)))
                    trip_charts.append(trip_chart(trip_metrics[-1], section))
                    sections_folder = output / "sections"
                    sections_folder.mkdir(exist_ok=True)
                    filename = f"sections/{len(summaries):02d}_{number:03d}.csv"
                    section.to_csv(output / filename, index=False)
                    section_inventory.append({"recording": folder.name, "section": number,
                         "start_s": start, "end_s": end, "file": filename,
                         "contains_gap": section.block.nunique() > 1})
            summary["signal_file"] = f"signal_{len(summaries):02d}.csv"
            summaries.append(summary)
            events.extend(candidates)
            quiet.extend(intervals)
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
    write_trip_acceleration(output, trip_metrics, config.get("trip_window_seconds", 30))
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
    manifest_out = {"configuration": config, "source_files": manifest, "failures": failures,
                    "reviews": reviews.to_dict("records"), "reviewed_stops": stops.to_dict("records"),
                    "route_reference": CATALOG,
                    "versions": {"numpy": np.__version__, "pandas": pd.__version__,
                                 "scipy": __import__("scipy").__version__, "matplotlib": matplotlib.__version__}}
    (output / "manifest.json").write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")
    report(output, summaries, events, failures, quiet, rejected_dwells, config, series, trip_charts)
    print(f"Finished: {len(summaries)} recordings, {len(events)} events, {len(failures)} failures. {output / 'report.html'}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
