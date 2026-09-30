"""Resolve route endpoints independently of signal detection."""
import json
import re
import unicodedata
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name("routes.json").read_text(encoding="utf-8"))


def normalized(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def route_from_name(name):
    match = re.match(r"^L(\d+)_(.*?)-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", name)
    if not match:
        return {"route_status": "unrecognized recording name"}
    line, body = match.groups()
    corridor = CATALOG["lines"].get(line)
    if not corridor:
        return {"route_status": "line outside reference coverage", "line": line}
    pieces = body.split("_-_", 2)
    if len(pieces) < 2:
        return {"route_status": "missing route separator", "line": line}
    mapping = {normalized(s): s for s in corridor["stations"]}
    mapping.update({normalized(k): v for k, v in CATALOG["aliases"].items() if v in corridor["stations"]})
    origin = mapping.get(normalized(pieces[0]))
    destination_text = normalized(pieces[1])
    matches = [(alias, station) for alias, station in mapping.items()
               if destination_text == alias or destination_text.startswith(alias + " ")]
    destination = max(matches, key=lambda item: len(item[0]))[1] if matches else None
    if origin is None or destination is None:
        return {"line": line, "route_status": "endpoint outside reference coverage"}
    stations = corridor["stations"]
    start, end = stations.index(origin), stations.index(destination)
    step = 1 if end >= start else -1
    route = [stations[i] for i in range(start, end + step, step)]
    return {"line": line, "origin": origin, "destination": destination,
            "route_status": "resolved", "route_stations": " → ".join(route),
            "expected_arrivals": len(route) - 1,
            "expected_intermediate_stops": max(0, len(route) - 2),
            "expected_stations_including_origin": len(route), "route_source": corridor["source"],
            "route_reference_date": CATALOG["checked_on"]}


def compare_stop_count(summary, intervals):
    interior = [q for q in intervals if q["boundary"] == "interior"]
    edge = [q for q in intervals if q["boundary"] == "recording edge"]
    gap = [q for q in intervals if q["boundary"] == "gap edge"]
    result = {"detected_stop_candidates": len(intervals), "detected_interior_stops": len(interior),
              "detected_recording_edge_stops": len(edge), "detected_gap_edge_stops": len(gap)}
    if summary.get("route_status") != "resolved":
        return result | {"stop_count_status": "route unavailable"}
    expected = summary["expected_intermediate_stops"]
    result["interior_stop_count_difference"] = len(interior) - expected
    result["stop_count_status"] = ("matches intermediate count; station identities unconfirmed"
        if len(interior) == expected and not gap else
        "review required: gaps or intermediate count mismatch")
    return result
