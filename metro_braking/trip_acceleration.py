"""Compare acceleration after human-validated station stop boundaries."""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


FIELDS = ["recording", "section", "start_s", "restart_s", "arrival_s", "end_s", "window_seconds", "window_start_s", "window_end_s",
          "max_acceleration_mps2", "peak_time_s", "status",
          "max_deceleration_mps2", "deceleration_peak_time_s", "deceleration_status", "display_recording", "run_label", "origin_station", "destination_station",
          "line", "station_assignment", "coverage", "duration_s", "duration_eligible",
          "duration_sample_count", "duration_mean_s", "duration_std_s", "duration_warning", "duration_check_status"]

def window_status(signal, window, start, end):
    """Check finite values and continuous coverage without interpolating gaps."""
    if window.empty:
        return "no_samples"
    times = window.seconds_elapsed.to_numpy(dtype=float)
    values = window.axis_filtered_mps2.to_numpy(dtype=float)
    steps = np.diff(signal.seconds_elapsed.to_numpy(dtype=float))
    positive = steps[steps > 0]
    dt = float(np.median(positive)) if len(positive) else 0
    if not np.isfinite(values).all():
        return "invalid_samples"
    if (window.block.nunique() != 1 or dt <= 0 or
        times[0] - start > dt * 1.01 or end - times[-1] > dt * 1.01 or
        np.any(np.diff(times) > dt * 1.5)):
        return "gap_or_missing_samples"
    return "complete"


def trip_acceleration(recording, number, start, end, signal, window_seconds=30, restart_offset_seconds=0.5, arrival_s=None):
    """Measure extrema after restart + offset; chart time zero is the restart."""
    if not np.isfinite(window_seconds) or window_seconds <= 0:
        raise ValueError("trip_window_seconds must be finite and positive")
    if not np.isfinite(restart_offset_seconds) or restart_offset_seconds < 0:
        raise ValueError("trip_restart_offset_seconds must be finite and nonnegative")
    if start >= end:
        raise ValueError("Restart time must precede the next accepted arrival")
    window_start = start + restart_offset_seconds
    window_end = min(window_start + window_seconds, end)
    window = signal[(signal.seconds_elapsed >= window_start) &
                    (signal.seconds_elapsed < window_end)]
    row = dict(recording=recording, section=number, start_s=start, restart_s=start, arrival_s=arrival_s, end_s=end,
               window_seconds=window_seconds, window_start_s=window_start, window_end_s=window_end,
               max_acceleration_mps2=None, peak_time_s=None,
               status=window_status(signal, window, window_start, window_end),
               max_deceleration_mps2=None, deceleration_peak_time_s=None,
               deceleration_status="incomplete_window")
    if row["status"] != "complete":
        return row
    if end - window_start < window_seconds:
        row["status"] = "short_trip"
        return row
    times = window.seconds_elapsed.to_numpy(dtype=float)
    values = window.axis_filtered_mps2.to_numpy(dtype=float)
    peak, minimum = int(np.argmax(values)), int(np.argmin(values))
    row["max_acceleration_mps2"] = float(values[peak])
    row["peak_time_s"] = float(times[peak])
    if values[minimum] < 0:
        row["max_deceleration_mps2"] = -float(values[minimum])
        row["deceleration_peak_time_s"] = float(times[minimum])
        row["deceleration_status"] = "complete"
    else:
        row["deceleration_status"] = "no_deceleration"
    return row


def write_trip_acceleration(output, rows, window_seconds=30, restart_offset_seconds=0.5):
    """Persist all outcomes and compare independent extrema in complete windows."""
    pd.DataFrame(rows, columns=FIELDS).to_csv(output / "trip_acceleration.csv", index=False)
    duration_fields = ["recording", "display_recording", "run_label", "line", "origin_station", "destination_station",
                       "coverage", "duration_s", "duration_sample_count", "duration_mean_s", "duration_std_s",
                       "duration_warning", "duration_check_status"]
    pd.DataFrame(rows).reindex(columns=duration_fields).to_csv(output / "run_durations.csv", index=False)
    for field, filename, label, color in [
        ("max_acceleration_mps2", "trip_acceleration.png", "Maximum filtered forward acceleration (m/s²)", "#1f77b4"),
        ("max_deceleration_mps2", "trip_deceleration.png", "Maximum filtered deceleration magnitude (m/s²)", "#b34c32"),
    ]:
        measured = [r for r in rows if r["status"] == "complete" and r[field] is not None]
        fig, ax = plt.subplots(figsize=(12, max(4, len(measured) * 0.28)), constrained_layout=True)
        if measured:
            labels = [f'{r.get("display_recording", r["recording"])} / {r.get("run_label", "Interstation run " + str(r["section"]))}' for r in measured]
            ax.barh(range(len(measured)), [r[field] for r in measured], color=color)
            ax.set_yticks(range(len(measured)), labels, fontsize=7)
            ax.invert_yaxis()
        else:
            ax.text(0.5, 0.5, "No measurements in complete windows from validated restarts",
                    ha="center", va="center", transform=ax.transAxes)
            ax.set_yticks([])
        ax.set_xlabel(label)
        ax.set_title(f"{window_seconds:g}-second window from restart + {restart_offset_seconds:g} s")
        ax.axvline(0, color="0.4", linewidth=0.8)
        ax.grid(axis="x", alpha=0.2)
        fig.savefig(output / filename, dpi=140)
        plt.close(fig)


def trip_chart(metric, signal):
    """Keep full-precision filtered samples and separate blocks for visual review."""
    blocks = []
    signal = signal[signal.seconds_elapsed >= (metric["restart_s"] if metric["restart_s"] is not None else metric["start_s"])]
    for _, block in signal.groupby("block", sort=False):
        blocks.append([[float(t - metric["start_s"]), float(a)]
                       for t, a in zip(block.seconds_elapsed, block.axis_filtered_mps2)])
    return {"metrics": metric, "blocks": blocks}


def resolve_restart(stop_rows, arrival_s, candidates):
    """Reuse explicit restart times, or uniquely match legacy arrival-only reviews."""
    accepted = stop_rows.loc[~stop_rows.accepted.astype(str).str.lower().isin(["false", "0", "no"])]
    row = accepted[np.isclose(pd.to_numeric(accepted.time_s), arrival_s, atol=1e-8, rtol=0)].iloc[0]
    if "restart_s" in row and pd.notna(row.restart_s) and str(row.restart_s).strip():
        restart = float(row.restart_s)
    else:
        key = row.get("candidate_id", "")
        matches = [c for c in candidates if c.get("id") == key] if key else []
        if not matches:
            matches = [c for c in candidates if np.isclose(c["start_s"], arrival_s, atol=0.051, rtol=0)]
        if len(matches) != 1:
            raise ValueError("Cannot resolve restart from arrival-only stop CSV; export reviews again or add restart_s")
        restart = float(matches[0]["end_s"])
    if not np.isfinite(restart) or restart < arrival_s:
        raise ValueError("Restart time must be finite and at or after arrival")
    return restart
