"""Process one recording independently of report ordering and other recordings."""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .analysis import (load_recording, detect_stops, detect_events, check_orientation,
                       apply_reviews, sensor_inventory, split_sections)
from .routes import route_from_name, compare_stop_count, display_recording_name
from .review import candidate_id
from .runs import build_runs


def write_csv(rows, path, columns=None):
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


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
    fig.suptitle(display_recording_name(summary["recording"]) + "\n" + summary["quality_flags"], fontsize=9)
    fig.savefig(output, dpi=120)
    plt.close(fig)


def analyze_recording(folder, config, reviews, stops, output):
    """Write isolated artifacts so a cached recording can be placed at any report index."""
    (output / "plots").mkdir(parents=True)
    sensors, orientation_checks, rejected_dwells, trip_metrics, trip_charts, section_inventory = [], [], [], [], [], []
    series = {}
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
    diagnostic_plot(segments, summary, candidates, intervals, folder, output / "plots" / f"{0:02d}.png", stop_diagnostics)
    stop_signal = [pd.DataFrame({"seconds_elapsed": d["t"], "block": d["block"],
                    "uncalibrated_variation_mps2": d["variation"],
                    "baseline_distance_mps2": d["baseline_distance"]}) for d in stop_diagnostics]
    stop_output = output / f"stop_signal_{0:02d}.csv"
    if stop_signal:
        pd.concat(stop_signal).to_csv(stop_output, index=False)
    else:
        write_csv([], stop_output, columns=["seconds_elapsed", "block", "uncalibrated_variation_mps2"])
    signal = pd.concat([pd.DataFrame({"seconds_elapsed": s.t, "block": i,
                 "axis_resampled_mps2": s.projected, "axis_filtered_mps2": s.filtered,
                 "jerk_mps3": np.gradient(s.filtered, s.t)}) for i, s in enumerate(segments)])
    signal.to_csv(output / f"signal_{0:02d}.csv", index=False)
    if not stops.empty:
        own_stops = stops[stops.recording == folder.name]
        own_metrics, own_charts = build_runs(folder.name, signal, own_stops,
            [dict(q, id=candidate_id(q)) for q in intervals + [q for d in stop_diagnostics for q in d["rejected_dwells"]]], config, summary)
        trip_metrics.extend(own_metrics)
        trip_charts.extend(own_charts)
        for number, start, end, section in split_sections(signal, own_stops):
            sections_folder = output / "sections"
            sections_folder.mkdir(exist_ok=True)
            filename = f"sections/{0:02d}_{number:03d}.csv"
            section.to_csv(output / filename, index=False)
            section_inventory.append({"recording": folder.name, "section": number,
                 "start_s": start, "end_s": end, "file": filename,
                 "contains_gap": section.block.nunique() > 1})
    summary["signal_file"] = f"signal_{0:02d}.csv"
    return dict(summary=summary, events=candidates, quiet=intervals, sensors=sensors,
                orientation_checks=orientation_checks, rejected_dwells=rejected_dwells,
                series=series[folder.name], metrics=trip_metrics, charts=trip_charts,
                sections=section_inventory)
