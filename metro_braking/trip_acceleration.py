"""Compare acceleration after human-validated station stop boundaries."""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


FIELDS = ["recording", "section", "start_s", "end_s", "window_seconds", "window_end_s",
          "max_acceleration_mps2", "peak_time_s", "status",
          "max_deceleration_mps2", "deceleration_peak_time_s", "deceleration_status"]

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


def trip_acceleration(recording, number, start, end, signal, window_seconds=30):
    """Measure independent signed extrema in [start, start + window_seconds)."""
    if not np.isfinite(window_seconds) or window_seconds <= 0:
        raise ValueError("trip_window_seconds must be finite and positive")
    window_end = min(start + window_seconds, end)
    window = signal[(signal.seconds_elapsed >= start) &
                    (signal.seconds_elapsed < window_end)]
    row = dict(recording=recording, section=number, start_s=start, end_s=end,
               window_seconds=window_seconds, window_end_s=window_end,
               max_acceleration_mps2=None, peak_time_s=None,
               status=window_status(signal, window, start, window_end),
               max_deceleration_mps2=None, deceleration_peak_time_s=None,
               deceleration_status="incomplete_window")
    if row["status"] != "complete":
        return row
    if end - start < window_seconds:
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


def write_trip_acceleration(output, rows, window_seconds=30):
    """Persist all outcomes and compare independent extrema in complete windows."""
    pd.DataFrame(rows, columns=FIELDS).to_csv(output / "trip_acceleration.csv", index=False)
    for field, filename, label, color in [
        ("max_acceleration_mps2", "trip_acceleration.png", "Maximum filtered forward acceleration (m/s²)", "#1f77b4"),
        ("max_deceleration_mps2", "trip_deceleration.png", "Maximum filtered deceleration magnitude (m/s²)", "#b34c32"),
    ]:
        measured = [r for r in rows if r["status"] == "complete" and r[field] is not None]
        fig, ax = plt.subplots(figsize=(12, max(4, len(measured) * 0.28)), constrained_layout=True)
        if measured:
            labels = [f'{r["recording"]} / trip {r["section"]}' for r in measured]
            ax.barh(range(len(measured)), [r[field] for r in measured], color=color)
            ax.set_yticks(range(len(measured)), labels, fontsize=7)
            ax.invert_yaxis()
        else:
            ax.text(0.5, 0.5, "No measurements in complete windows from validated stops",
                    ha="center", va="center", transform=ax.transAxes)
            ax.set_yticks([])
        ax.set_xlabel(label)
        ax.set_title(f"First {window_seconds:g} seconds after each accepted stop time")
        ax.axvline(0, color="0.4", linewidth=0.8)
        ax.grid(axis="x", alpha=0.2)
        fig.savefig(output / filename, dpi=140)
        plt.close(fig)


def trip_chart(metric, signal):
    """Keep full-precision filtered samples and separate blocks for visual review."""
    blocks = []
    for _, block in signal.groupby("block", sort=False):
        blocks.append([[float(t - metric["start_s"]), float(a)]
                       for t, a in zip(block.seconds_elapsed, block.axis_filtered_mps2)])
    return {"metrics": metric, "blocks": blocks}
