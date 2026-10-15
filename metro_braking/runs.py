"""Validated interstation coverage and comparable running-duration diagnostics."""
import numpy as np
import pandas as pd

from .analysis import split_sections
from .routes import route_from_name, display_recording_name
from .trip_acceleration import trip_acceleration, trip_chart, resolve_restart, arrival_deceleration


def build_runs(recording, signal, stop_rows, candidates, config):
    if stop_rows.empty:
        return [], []
    accepted = stop_rows.loc[~stop_rows.accepted.astype(str).str.lower().isin(["false", "0", "no"])].copy()
    accepted = accepted.sort_values("time_s")
    if accepted.empty:
        return [], []
    route = route_from_name(recording)
    stations = route.get("route_stations", "").split(" → ")
    own_candidates = [c for c in candidates if c["recording"] == recording]
    boundary_rows = []
    for row in accepted.to_dict("records"):
        key = row.get("candidate_id")
        matches = [c for c in own_candidates if key and c.get("id") == key]
        if not matches:
            matches = [c for c in own_candidates if np.isclose(c["start_s"], row["time_s"], atol=0.051, rtol=0)]
        candidate = matches[0] if len(matches) == 1 else {}
        boundary_rows.append(dict(row, candidate=candidate, restart=resolve_restart(accepted, row["time_s"], own_candidates)))
    interior = [r for r in boundary_rows if r["candidate"].get("boundary") == "interior"]
    aligned = (route.get("route_status") == "resolved" and len(interior) == len(stations)-2
               and all(r["candidate"].get("boundary") in ("interior", "recording edge") for r in boundary_rows)
               and signal.block.nunique() == 1)
    station_at = {}
    if aligned:
        for station, row in zip(stations[1:-1], interior):
            station_at[row["time_s"]] = station
        for row in boundary_rows:
            c = row["candidate"]
            if c.get("boundary") == "recording edge":
                if c.get("left_censored") and not c.get("right_censored"):
                    station_at[row["time_s"]] = stations[0]
                elif c.get("right_censored") and not c.get("left_censored"):
                    station_at[row["time_s"]] = stations[-1]
        assigned = [station_at.get(r["time_s"]) for r in boundary_rows]
        aligned = (all(assigned) and len(set(assigned)) == len(assigned)
                   and [stations.index(s) for s in assigned] == sorted(stations.index(s) for s in assigned))
    if not aligned:
        station_at = {}
    metrics, charts = [], []

    def append_run(number, start, end, section, origin=None, destination=None,
                   coverage="complete", arrival=None, restart_known=True):
        metric = trip_acceleration(recording, number, start, end, section,
            config.get("trip_window_seconds", 30), config.get("trip_restart_offset_seconds", 0.5), arrival)
        if not restart_known:
            metric.update(restart_s=None, window_start_s=None, window_end_s=None,
                          max_acceleration_mps2=None, peak_time_s=None, max_deceleration_mps2=None,
                          deceleration_peak_time_s=None, status="departure_not_identified", deceleration_status="incomplete_window")
        metric.update(arrival_deceleration(section, start,
                      None if coverage == "arrival_not_identified" else end,
                      config.get("arrival_window_seconds", 20)))
        label = f"{origin} – {destination}" if origin and destination else f"Interstation run {number}"
        metric.update(display_recording=display_recording_name(recording), run_label=label,
                      origin_station=origin, destination_station=destination, line=route.get("line"),
                      station_assignment="Station names inferred from stop order" if origin and destination else "Station names unavailable: review stop count or gaps",
                      coverage=coverage, duration_s=end-start if coverage == "complete" else None,
                      duration_eligible=(coverage == "complete" and bool(origin and destination) and section.block.nunique() == 1))
        metrics.append(metric)
        # Include the signal up to the validated arrival, even when the analysis
        # window near departure ends much earlier. Never extend beyond observed data.
        chart_signal = signal[(signal.seconds_elapsed >= start) & (signal.seconds_elapsed <= end)]
        charts.append(trip_chart(metric, chart_signal))

    for number, arrival, end, section in split_sections(signal, accepted):
        restart = resolve_restart(accepted, arrival, own_candidates)
        append_run(number, restart, end, section, station_at.get(arrival), station_at.get(end), arrival=arrival)
    # Missing endpoint dwells are displayed as partial coverage, never invented stops.
    if aligned and boundary_rows:
        first, last = boundary_rows[0], boundary_rows[-1]
        if station_at[first["time_s"]] != stations[0]:
            section = signal[signal.seconds_elapsed < first["time_s"]]
            if not section.empty:
                append_run(0, float(section.seconds_elapsed.min()), first["time_s"], section,
                           stations[0], station_at[first["time_s"]], "departure_not_identified", restart_known=False)
        if station_at[last["time_s"]] != stations[-1]:
            section = signal[signal.seconds_elapsed >= last["restart"]]
            if not section.empty and float(signal.seconds_elapsed.max()) > last["restart"]:
                append_run(len(boundary_rows), last["restart"], float(signal.seconds_elapsed.max()), section,
                           station_at[last["time_s"]], stations[-1], "arrival_not_identified", arrival=last["time_s"])
    order = sorted(range(len(metrics)), key=lambda i: metrics[i]["start_s"])
    return [metrics[i] for i in order], [charts[i] for i in order]


def duration_statistics(rows):
    """Compare full running times on the same line, station pair and direction."""
    groups = {}
    for row in rows:
        if row.get("duration_eligible"):
            key = (row["line"], row["origin_station"], row["destination_station"])
            groups.setdefault(key, []).append(row["duration_s"])
    for row in rows:
        values = groups.get((row.get("line"), row.get("origin_station"), row.get("destination_station")), [])
        n = len(values)
        mean = float(np.mean(values)) if n else None
        std = float(np.std(values, ddof=1)) if n >= 2 else None
        warning = bool(row.get("duration_eligible") and n >= 3 and std is not None and
                       abs(row["duration_s"]-mean) > 2*std)
        row.update(duration_sample_count=n, duration_mean_s=mean, duration_std_s=std,
                   duration_warning=warning,
                   duration_check_status=("partial_or_unassigned" if not row.get("duration_eligible") else
                       "insufficient_samples" if n < 3 else "outside_2_std" if warning else "within_2_std"))
