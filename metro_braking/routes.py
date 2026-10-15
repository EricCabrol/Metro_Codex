"""Resolve route endpoints independently of signal detection."""
import json
from datetime import datetime
import re
import unicodedata
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name("routes.json").read_text(encoding="utf-8"))


def normalized(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", text)
    return " ".join({"st": "saint", "ste": "sainte"}.get(word, word) for word in words)


def parse_recording_name(name):
    match = re.match(r"^L(\d+(?:[ _-]?(?:bis|b))?)[_ ](.*?)-(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$", name, re.I)
    if not match:
        return None
    line, body, timestamp = match.groups()
    line = re.sub(r"[ _-]", "", line).lower()
    if line.endswith("b"):
        line += "is"
    try:
        date = datetime.strptime(timestamp, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None
    return line, body, date


def station_aliases(line):
    stations = CATALOG["lines"][line]["stations"]
    mapping = {normalized(s): s for s in stations}
    mapping.update({normalized(k): v for k, v in CATALOG["aliases"].items() if v in stations})
    return mapping


def route_from_name(name):
    parsed = parse_recording_name(name)
    if parsed is None:
        return {"route_status": "unrecognized recording name"}
    line, body, date = parsed
    corridor = CATALOG["lines"].get(line)
    if not corridor:
        return {"route_status": "line outside reference coverage", "line": line}
    pieces = body.split("_-_", 1) if "_-_" in body else re.split(r" +[–—-] +", body, maxsplit=1)
    if len(pieces) < 2:
        return {"route_status": "missing route separator", "line": line}
    mapping = station_aliases(line)
    origin = mapping.get(normalized(pieces[0]))
    destination_text = normalized(pieces[1])
    matches = [(alias, station) for alias, station in mapping.items()
               if destination_text == alias or destination_text.startswith(alias + " ")]
    destination = max(matches, key=lambda item: len(item[0]))[1] if matches else None
    if origin is None or destination is None:
        return {"line": line, "route_status": "unrecognized station name on this line"}
    openings = corridor.get("station_openings", {})
    recording_date = date.strftime("%Y-%m-%d")
    if any(openings.get(station, "") > recording_date for station in (origin, destination)):
        return {"line": line, "route_status": "endpoint not open on recording date"}
    candidates = set()
    for service in corridor.get("paths", [{"stations": corridor["stations"], "bidirectional": True}]):
        stations = [s for s in service["stations"] if openings.get(s, "") <= recording_date]
        for start, station in enumerate(stations):
            if station != origin:
                continue
            for end, endpoint in enumerate(stations):
                if endpoint != destination:
                    continue
                if end >= start:
                    candidates.add(tuple(stations[start:end+1]))
                elif service.get("bidirectional", True):
                    candidates.add(tuple(reversed(stations[end:start+1])))
    if not candidates:
        return {"line": line, "route_status": "no direct regular service between endpoints"}
    length = min(map(len, candidates))
    shortest = [route for route in candidates if len(route) == length]
    if len(shortest) != 1:
        return {"line": line, "route_status": "ambiguous service pattern between endpoints"}
    route = shortest[0]
    return {"line": line, "origin": origin, "destination": destination,
            "route_status": "resolved", "route_stations": " → ".join(route),
            "expected_arrivals": len(route) - 1,
            "expected_intermediate_stops": max(0, len(route) - 2),
            "expected_stations_including_origin": len(route), "route_source": corridor["source"],
            "route_reference_date": CATALOG["updated_on"]}


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


def display_recording_name(name):
    """Format names for display only; preserve all non-station metadata."""
    parsed = parse_recording_name(name)
    if parsed is None:
        return name.replace("_", " ")
    line, body, date = parsed
    pieces = body.split("_-_", 1) if "_-_" in body else re.split(r" +[–—-] +", body, maxsplit=1)
    if len(pieces) != 2:
        return name.replace("_", " ")
    route = route_from_name(name)
    if route.get("route_status") != "resolved":
        return name.replace("_", " ")
    # Match the longest destination alias and keep every remaining token.
    aliases = station_aliases(line)
    destination = normalized(pieces[1])
    matches = [a for a, station in aliases.items() if station == route["destination"] and
               (destination == a or destination.startswith(a + " "))]
    alias = max(matches, key=len)
    tokens = re.findall(r"[^\W_]+", pieces[1], re.UNICODE)
    metadata = " ".join(tokens[len(alias.split()):])
    origin = route["origin"].replace(" - Bienvenüe", "")
    endpoint = route["destination"].replace(" - Bienvenüe", "")
    label = f"L{line} {origin} – {endpoint} {date:%Hh%M, %d/%m/%Y}"
    return label + (f" ({metadata})" if metadata else "")
