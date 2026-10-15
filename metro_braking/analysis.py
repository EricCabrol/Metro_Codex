"""Signal processing with an explicit, checked phone-axis convention.

All timestamps remain seconds since SensorLogger recording start.
Named recordings assume Y forward, reversed by Yinv; PCA checks alignment.
"""
from dataclasses import dataclass
import re
import numpy as np
import pandas as pd
from scipy.signal import butter, sosfiltfilt


@dataclass
class Segment:
    t: np.ndarray
    xyz: np.ndarray
    filtered_xyz: np.ndarray
    projected: np.ndarray
    filtered: np.ndarray


def lowpass(values, hz, cutoff):
    if not 0 < cutoff < hz / 2:
        raise ValueError("Filter cutoff must be positive and below Nyquist")
    return sosfiltfilt(butter(4, cutoff, fs=hz, output="sos"), values, axis=0)


def runs(mask):
    edges = np.diff(np.r_[False, mask, False].astype(int))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def load_recording(folder, config, source_name=None):
    calibrated = folder / "Accelerometer.csv"
    source = calibrated if calibrated.exists() else folder / "AccelerometerUncalibrated.csv"
    if source_name is not None:
        source = folder / source_name
    frame = pd.read_csv(source)
    required = ["seconds_elapsed", "x", "y", "z"]
    if not set(required).issubset(frame):
        raise ValueError(f"Missing columns in {source.name}")
    numeric = frame[required].apply(pd.to_numeric, errors="coerce")
    invalid = ~np.isfinite(numeric.to_numpy()).all(axis=1)
    valid = numeric.loc[~invalid].copy()
    nonmonotonic = int((valid.seconds_elapsed.diff().dropna() <= 0).sum())
    duplicates = int(valid.seconds_elapsed.duplicated().sum())
    valid = valid.sort_values("seconds_elapsed").drop_duplicates("seconds_elapsed")
    if len(valid) < 40:
        raise ValueError("Too few valid samples")
    t = valid.seconds_elapsed.to_numpy()
    xyz = valid[["x", "y", "z"]].to_numpy()
    dt = np.diff(t)
    meta = {}
    metadata = folder / "Metadata.csv"
    if metadata.exists() and metadata.stat().st_size:
        meta = pd.read_csv(metadata).iloc[0].fillna("").to_dict()
    summary = {
        "recording": folder.name, "source": source.name, "samples": len(valid),
        "invalid_rows": int(invalid.sum()), "duplicate_timestamps": duplicates,
        "nonmonotonic_timestamps": nonmonotonic,
        "start_s": float(t[0]), "end_s": float(t[-1]),
        "duration_s": float(t[-1] - t[0]), "median_sample_hz": float(1 / np.median(dt)),
        "interval_p95_s": float(np.percentile(dt, 95)), "largest_gap_s": float(dt.max()),
        "gap_count": int((dt > config["gap_seconds"]).sum()),
        "gap_intervals": [{"start_s": float(t[i]), "end_s": float(t[i+1]),
                           "duration_s": float(dt[i])} for i in np.flatnonzero(dt > config["gap_seconds"])],
        "estimated_missing_s": float(np.maximum(dt[dt > config["gap_seconds"]] - np.median(dt), 0).sum()),
        "device": meta.get("device name", "unknown"),
        "trip_hint": "brutal" if "brutal" in folder.name else "smooth" if "smooth" in folder.name else "unknown",
    }
    # Never interpolate across long gaps. Downsample with an anti-alias filter
    # within each continuous block before moving to the common time grid.
    boundaries = np.r_[0, np.flatnonzero(dt > config["gap_seconds"]) + 1, len(t)]
    blocks = []
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        bt, bx = t[start:end], xyz[start:end]
        if len(bt) < 40 or bt[-1] - bt[0] < 4:
            continue
        native_hz = 1 / np.median(np.diff(bt))
        uniform_t = np.arange(bt[0], bt[-1], 1 / native_hz)
        uniform_x = np.column_stack([np.interp(uniform_t, bt, bx[:, i]) for i in range(3)])
        if native_hz > config["sample_hz"]:
            uniform_x = lowpass(uniform_x, native_hz, config["sample_hz"] * 0.4)
        grid = np.arange(bt[0], uniform_t[-1], 1 / min(native_hz, config["sample_hz"]))
        resampled = np.column_stack([np.interp(grid, uniform_t, uniform_x[:, i]) for i in range(3)])
        hz = 1 / np.median(np.diff(grid))
        # Uncalibrated-only fallback removes a constant gravity/bias vector.
        # Phone rotation invalidates this approximation and is flagged below.
        resampled -= np.median(resampled, axis=0)
        filtered_xyz = lowpass(resampled, hz, config["cutoff_hz"])
        blocks.append((grid, resampled, filtered_xyz))
    if not blocks:
        raise ValueError("No usable continuous signal blocks")
    all_filtered = np.concatenate([b[2] for b in blocks])
    covariance = np.cov(all_filtered, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    pca_axis = eigenvectors[:, -1]
    pca_axis *= 1 if pca_axis[np.argmax(np.abs(pca_axis))] >= 0 else -1
    named_recording = bool(re.match(r"^L\d+_", folder.name))
    expected_polarity = -1 if re.search(r"(?:^|_)Yinv(?:_|-|$)", folder.name, re.I) else 1
    axis = np.array([0., 1., 0.]) if named_recording else pca_axis.copy()
    overrides = config.get("recordings", {}).get(folder.name, {})
    if "axis" in overrides:
        axis = np.array(overrides["axis"], dtype=float)
        if axis.shape != (3,) or not np.isfinite(axis).all() or np.linalg.norm(axis) == 0:
            raise ValueError("axis must contain three finite values with nonzero norm")
        axis /= np.linalg.norm(axis)
    polarity = overrides.get("polarity", expected_polarity if named_recording else 0)
    if polarity not in (-1, 0, 1):
        raise ValueError("polarity must be -1, 0, or 1")
    direction = polarity or 1
    segments = [Segment(bt, bx, fx, bx @ axis * direction, fx @ axis * direction) for bt, bx, fx in blocks]
    share = float(eigenvalues[-1] / max(eigenvalues.sum(), 1e-12))
    axis_source = "manual" if "axis" in overrides else "folder Y convention" if named_recording else "PCA"
    flags = ["manual axis supplied" if axis_source == "manual" else
             "Y forward convention pending checks" if named_recording else "PCA axis requires physical validation"]
    if not polarity:
        flags.append("travel direction unresolved; both signs are candidates")
    if not calibrated.exists():
        flags.append("uncalibrated-only: constant gravity removal; rotation may contaminate signal")
    if share < 0.7:
        flags.append("weak dominant axis")
    alignment = float(abs(pca_axis @ axis))
    if named_recording and alignment < 0.7:
        flags.append("dominant motion axis disagrees with selected travel axis")
    if summary["gap_count"]:
        flags.append("gaps excluded from interpolation")
    if invalid.any() or nonmonotonic:
        flags.append("invalid or nonmonotonic input repaired")
    summary.update(axis_x=float(axis[0]), axis_y=float(axis[1]), axis_z=float(axis[2]),
                   dominant_variance_share=share, pca_axis_alignment=alignment,
                   axis_source=axis_source, expected_y_polarity=expected_polarity if named_recording else 0,
                   polarity=polarity, quality_flags="; ".join(flags),
                   usable_duration_s=float(sum(s.t[-1] - s.t[0] for s in segments)))
    return segments, summary


def event_metrics(segment, start, end, sign, config):
    t, a = segment.t, segment.filtered
    selected = (t >= start) & (t <= end)
    if selected.sum() < 3:
        raise ValueError("Event must contain at least three samples")
    hz = 1 / np.median(np.diff(t))
    intensity = sign * a[selected]
    jerk = np.gradient(a, t)[selected]
    sustained = np.convolve(intensity, np.ones(max(1, round(hz))) / max(1, round(hz)), mode="valid") if len(intensity) >= round(hz) else intensity
    peaks = [float(np.max(sign * lowpass(segment.projected, hz, c)[selected])) for c in config["sensitivity_cutoffs_hz"]]
    return {
        "start_s": float(t[selected][0]), "end_s": float(t[selected][-1]),
        "duration_s": float(t[selected][-1] - t[selected][0]), "sign": int(sign),
        "peak_mps2": float(intensity.max()), "sustained_1s_mps2": float(sustained.max()),
        "mean_mps2": float(intensity.mean()), "jerk_p95_mps3": float(np.percentile(np.abs(jerk), 95)),
        "jerk_peak_mps3": float(np.max(np.abs(jerk))),
        "delta_velocity_mps": float(np.trapz(intensity, t[selected])),
        "peak_cutoff_min_mps2": min(peaks), "peak_cutoff_max_mps2": max(peaks),
        "cutoff_peak_spread_mps2": max(peaks) - min(peaks),
    }


def detect_events(segments, summary, config):
    events, quiet = [], []
    for block_id, segment in enumerate(segments):
        t, a = segment.t, segment.filtered
        hz = 1 / np.median(np.diff(t))
        directional = summary["polarity"] and not summary.get("orientation_inconsistent", False)
        for sign in ([-1] if directional else [-1, 1]):
            for lo, hi in runs(sign * a >= config["event_threshold_mps2"]):
                if (hi - lo) / hz < config["minimum_event_seconds"]:
                    continue
                row = event_metrics(segment, t[lo], t[hi - 1], sign, config)
                near_quiet = any(0 <= q["start_s"] - row["end_s"] <= 8 for q in quiet if q["block"] == block_id)
                row.update(recording=summary["recording"], block=block_id,
                           status="braking candidate" if directional else "direction uncertain acceleration candidate",
                           quiet_follows=near_quiet, label="", reviewed=False,
                           quality_flags=summary["quality_flags"])
                events.append(row)
    events.sort(key=lambda e: e["start_s"])
    for i, row in enumerate(events, 1):
        row["event_id"] = f"candidate-{i:03d}"
    return events, quiet


def detect_stops(folder, config):
    """Find sustained uncalibrated dwell evidence relative to a rest baseline.

    Rolling XYZ variance is computed before the braking low-pass. A trailing
    window makes timing explicit and never evaluates across recording gaps.
    """
    source = folder / "AccelerometerUncalibrated.csv"
    if not source.exists():
        return [], [], {"stop_source": "unavailable", "stop_gap_count": None, "short_dwell_candidates": 0}
    segments, raw_summary = load_recording(folder, config, source.name)
    diagnostics, intervals = [], []
    for block, segment in enumerate(segments):
        hz = 1 / np.median(np.diff(segment.t))
        window = max(2, round(hz * config.get("stop_window_seconds", 1.0)))
        variance = pd.DataFrame(segment.xyz).rolling(window, min_periods=window).var(ddof=0)
        # Floating-point rolling variance can be slightly negative at rest.
        variation = np.sqrt(variance.clip(lower=0).sum(axis=1, min_count=3).to_numpy())
        local_mean = pd.DataFrame(segment.xyz).rolling(window, min_periods=window).mean().to_numpy()
        # Constancy alone would accept sustained propulsion/braking. The raw
        # signal was centered on its block median (gravity/bias reference).
        # Estimate the rest level from quiet windows, rather than all samples:
        # motion can bias a whole-recording median. This remains an estimate;
        # a recording dominated by constant acceleration can defeat it.
        stop_axis = np.array([raw_summary[k] for k in ("axis_x", "axis_y", "axis_z")])
        quiet_windows = np.isfinite(variation) & (variation < config["quiet_threshold_mps2"])
        reference = float(np.median(local_mean[quiet_windows] @ stop_axis)) if quiet_windows.any() else 0.0
        level = np.abs(local_mean @ stop_axis - reference)
        diagnostics.append({"t": segment.t, "variation": variation, "block": block,
                            "filtered_xyz": segment.filtered_xyz, "baseline_distance": level,
                            "rejected_dwells": []})
        mask = (np.isfinite(variation) & (variation < config["quiet_threshold_mps2"])
                & (level < config.get("stop_level_tolerance_mps2", 0.25)))
        bridged = mask.copy()
        # Ignore brief phone disturbances, but never bridge a recording gap or
        # leading/trailing unknown samples. Require predominantly quiet evidence.
        for lo, hi in runs(~mask):
            if lo > 0 and hi < len(mask) and (hi - lo) / hz <= config.get("stop_max_interruption_seconds", 1.5):
                bridged[lo:hi] = True
        for lo, hi in runs(bridged):
            duration = float(segment.t[hi - 1] - segment.t[lo] + 1 / hz)
            quiet_fraction = float(mask[lo:hi].mean())
            minimum = config.get("minimum_stop_seconds", 15.0)
            if duration >= 5.0 and quiet_fraction >= config.get("stop_min_quiet_fraction", 0.9):
                left = segment.t[lo] - segment.t[0] <= window / hz + 0.1
                right = segment.t[-1] - segment.t[hi - 1] <= 0.1
                boundary = "interior"
                if left or right:
                    touches_recording = ((left and abs(segment.t[0] - raw_summary["start_s"]) < 0.1)
                                         or (right and raw_summary["end_s"] - segment.t[-1] < 0.2))
                    boundary = "recording edge" if touches_recording else "gap edge"
                row = {"recording": folder.name, "block": block,
                    "start_s": float(segment.t[lo]), "end_s": float(segment.t[hi - 1]),
                    "duration_s": duration, "quiet_fraction": quiet_fraction,
                    "boundary": boundary, "left_censored": left, "right_censored": right,
                    "source": source.name,
                    "median_variation_mps2": float(np.median(variation[lo:hi])),
                    "window_seconds": window / hz,
                    "status": f"{minimum:g}s dwell candidate; station stop unconfirmed"}
                if duration >= minimum:
                    intervals.append(row)
                else:
                    row["status"] = "rejected: observed dwell shorter than minimum"
                    diagnostics[-1]["rejected_dwells"].append(row)
    return intervals, diagnostics, {"stop_source": source.name,
                                  "stop_gap_count": raw_summary["gap_count"],
                                  "stop_gap_intervals": raw_summary["gap_intervals"],
                                  "stop_estimated_missing_s": raw_summary["estimated_missing_s"],
                                  "stop_duration_s": raw_summary["duration_s"],
                                  "short_dwell_candidates": sum(len(d["rejected_dwells"]) for d in diagnostics)}


def check_orientation(summary, intervals, diagnostics, config):
    """Check signed motion around dwell boundaries using uncalibrated data.

    Compare with each dwell's local baseline so gravity offsets do not count
    as acceleration. Do not silently flip direction to make the data fit.
    """
    axis = np.array([summary[k] for k in ("axis_x", "axis_y", "axis_z")])
    checks = []
    sign = summary["polarity"] or 1
    for stop in intervals:
        d = next(d for d in diagnostics if d["block"] == stop["block"])
        t, xyz = d["t"], d["filtered_xyz"]
        dwell = (t >= stop["start_s"] + 2) & (t <= stop["end_s"] - 2)
        baseline = np.median(xyz[dwell] @ axis) if dwell.any() else 0
        forward = (xyz @ axis - baseline) * sign
        for phase, lo, hi, expected in (("arrival", stop["start_s"] - 15, stop["start_s"] - 1, -1),
                                        ("departure", stop["end_s"] + 1, stop["end_s"] + 15, 1)):
            if (phase == "arrival" and stop["left_censored"]) or (phase == "departure" and stop["right_censored"]):
                continue
            mask = (t >= lo) & (t <= hi)
            if mask.sum() < 5 or t[0] > lo or t[-1] < hi:
                continue
            mean = float(np.mean(forward[mask]))
            verdict = "consistent" if expected * mean >= 0.1 else "opposite" if expected * mean <= -0.1 else "weak evidence"
            checks.append({"recording": summary["recording"], "stop_start_s": stop["start_s"],
                           "phase": phase, "mean_forward_mps2": mean, "verdict": verdict})
    consistent = sum(c["verdict"] == "consistent" for c in checks)
    opposite = sum(c["verdict"] == "opposite" for c in checks)
    axis_disagreement = summary["pca_axis_alignment"] < 0.7
    inconsistent = bool(opposite or axis_disagreement)
    status = "inconsistent evidence; review direction" if inconsistent else "supported by motion around dwells" if consistent >= 2 else "insufficient evidence to verify convention"
    if not summary["polarity"]:
        status = "direction explicitly unresolved"
    summary.update(orientation_status=status, orientation_inconsistent=inconsistent,
                   orientation_consistent_checks=consistent, orientation_opposite_checks=opposite)
    summary["quality_flags"] = summary["quality_flags"].replace("Y forward convention pending checks", "Yinv convention" if summary.get("expected_y_polarity") == -1 else "Y forward convention") + "; " + status
    return checks


def apply_reviews(events, segments, summary, config, reviews):
    """Reviewed rows replace automatic candidates for that recording.

    Allows correcting boundaries, deleting false positives, and adding misses.
    """
    rows = reviews.loc[reviews.recording == summary["recording"]] if not reviews.empty else reviews
    if rows.empty:
        return events
    result = []
    for _, row in rows.iterrows():
        if str(row.get("accepted", "true")).lower() in ("false", "0", "no"):
            continue
        start, end = float(row.start_s), float(row.end_s)
        sign = int(row.sign)
        if end <= start or sign not in (-1, 1):
            raise ValueError("Reviewed events need end > start and sign -1 or 1")
        segment = next((s for s in segments if s.t[0] <= start <= end <= s.t[-1]), None)
        if segment is None:
            raise ValueError(f"Reviewed event crosses a gap or lies outside recording: {row.event_id}")
        metrics = event_metrics(segment, start, end, sign, config)
        metrics.update(recording=summary["recording"], event_id=str(row.event_id),
                       status="reviewed braking event", reviewed=True, quiet_follows=None,
                       label=str(row.get("label", "")), quality_flags=summary["quality_flags"])
        result.append(metrics)
    return result


def sensor_inventory(folder):
    result = []
    for path in sorted(folder.glob("*.csv")):
        row = {"recording": folder.name, "file": path.name, "bytes": path.stat().st_size}
        if path.stat().st_size:
            header = pd.read_csv(path, nrows=0).columns.tolist()
            row["columns"] = ", ".join(header)
        else:
            row["columns"] = "empty"
        result.append(row)
    return result


def split_sections(signal, stop_rows):
    """Split processed samples between reviewed stop times; preserve block IDs."""
    accepted = stop_rows.loc[~stop_rows.accepted.astype(str).str.lower().isin(["false", "0", "no"])]
    times = pd.to_numeric(accepted.time_s, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(times).all() or len(np.unique(times)) != len(times):
        raise ValueError("Reviewed stop times must be finite and distinct")
    times.sort()
    if len(times) and (times[0] < signal.seconds_elapsed.min() or times[-1] > signal.seconds_elapsed.max()):
        raise ValueError("Reviewed stop time outside usable recording")
    result = []
    for number, (start, end) in enumerate(zip(times[:-1], times[1:]), 1):
        section = signal[(signal.seconds_elapsed >= start) & (signal.seconds_elapsed < end)].copy()
        if section.empty:
            raise ValueError("No samples between reviewed stops")
        result.append((number, start, end, section))
    return result
