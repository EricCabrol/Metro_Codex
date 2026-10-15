"""Portable report data, validated review persistence and local serving."""
import csv
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import threading
import shutil
import subprocess
import sys
import tempfile

from urllib.parse import urlsplit

import pandas as pd
from .analysis import split_sections
from .recordings import RecordingArchive
from .trip_acceleration import write_trip_acceleration
from .runs import build_runs, duration_statistics
from .manual_stops import review_candidates


def candidate_id(row):
    identity = f'{row["recording"]}|{row["start_s"]:.3f}|{row["end_s"]:.3f}'
    return hashlib.sha256(identity.encode()).hexdigest()[:20]


def report_payload(summaries, quiet, rejected, config, series):
    candidates = [dict(row, id=candidate_id(row), automatic=True) for row in quiet]
    candidates += [dict(row, id=candidate_id(row), automatic=False) for row in rejected]
    return {"version": 1, "minimum_stop_seconds": config["minimum_stop_seconds"],
            "trip_window_seconds": config.get("trip_window_seconds", 30),
            "arrival_window_seconds": config.get("arrival_window_seconds", 20),
            "trip_restart_offset_seconds": config.get("trip_restart_offset_seconds", 0.5),
            "recordings": summaries, "candidates": candidates, "series": series}


def validate_decisions(payload, decisions):
    if not isinstance(decisions, dict):
        raise ValueError("decisions must be an object")
    candidates = {c["id"]: c for c in review_candidates(payload, decisions)}
    recordings = {r["recording"]: r for r in payload["recordings"]}
    validated = {}
    for key, value in decisions.items():
        if key not in candidates or not isinstance(value, dict):
            raise ValueError("Unknown candidate or invalid decision")
        decision = value.get("decision")
        if decision not in ("pending", "accepted", "discarded"):
            raise ValueError("Decision must be pending, accepted or discarded")
        time = value.get("time_s", candidates[key]["start_s"])
        if isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(time):
            raise ValueError("Arrival time must be finite")
        recording = recordings[candidates[key]["recording"]]
        if not recording["start_s"] <= time <= recording["end_s"]:
            raise ValueError("Arrival time lies outside the recording")
        restart = value.get("restart_s", candidates[key]["end_s"])
        if isinstance(restart, bool) or not isinstance(restart, (int, float)) or not math.isfinite(restart):
            raise ValueError("Restart time must be finite")
        if not time <= restart <= recording["end_s"]:
            raise ValueError("Restart time must be at or after arrival and inside the recording")
        note = value.get("note", "")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("Note must be text of at most 2000 characters")
        validated[key] = {"decision": decision, "time_s": time, "restart_s": restart, "note": note}
        if candidates[key].get("manual"):
            validated[key].update(manual=True, recording=candidates[key]["recording"])
    accepted_times = set()
    for key, value in validated.items():
        if value["decision"] == "accepted":
            identity = (candidates[key]["recording"], value["time_s"])
            if identity in accepted_times:
                raise ValueError("Accepted stops must have distinct arrival times")
            accepted_times.add(identity)
    for name in recordings:
        accepted = sorted((v for k,v in validated.items() if v['decision']=='accepted' and candidates[k]['recording']==name), key=lambda v:v['time_s'])
        if any(a['restart_s'] >= b['time_s'] for a,b in zip(accepted,accepted[1:])):
            raise ValueError('Accepted stop intervals must not overlap')
    return validated


def review_rows(payload, decisions):
    rows = []
    for candidate in review_candidates(payload, decisions):
        value = decisions.get(candidate["id"])
        if value and value["decision"] != "pending":
            rows.append({"recording": candidate["recording"], "time_s": value["time_s"],
                         "restart_s": value.get("restart_s", candidate["end_s"]), "candidate_id": candidate["id"],
                         "accepted": value["decision"] == "accepted", "note": value["note"], "manual": bool(candidate.get("manual"))})
    return rows


class ReviewStore:
    def __init__(self, output, review_dir):
        self.output, self.review_dir = Path(output).resolve(), Path(review_dir).resolve()
        self.path = self.review_dir / "stops.json"
        self.lock = threading.RLock()

    def payload(self):
        return json.loads((self.output / "report_data.json").read_text(encoding="utf-8"))

    def read(self):
        stored = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"version": 1, "decisions": {}}
        payload = self.payload()
        values = dict(payload.get('initial_decisions', {}))
        values.update(stored['decisions'])
        names = {r['recording'] for r in payload['recordings']}
        ids = {c['id'] for c in payload['candidates']}
        current = {k:v for k,v in values.items() if k in ids or isinstance(v,dict) and v.get('manual') is True and v.get('recording') in names}
        review_candidates(payload, current)  # Validate manual definitions before exposing them.
        return {"version": 1, "decisions": current, "unmatched_decisions": len(values) - len(current)}

    def save(self, decisions):
        with self.lock:
            payload = self.payload()
            current = validate_decisions(payload, decisions)
            # Keep older candidate decisions for future reruns; never apply them
            # to changed boundaries simply because a row number was reused.
            previous = json.loads(self.path.read_text(encoding="utf-8"))["decisions"] if self.path.exists() else {}
            previous.update(current)
            self.review_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"version": 1, "decisions": previous}, indent=2), encoding="utf-8")
            temporary.replace(self.path)
            fields = ["recording", "time_s", "restart_s", "candidate_id", "accepted", "note", "manual"]
            csv_path = self.review_dir / "stops.csv"
            retained = []
            names = {r["recording"] for r in payload["recordings"]}
            if csv_path.exists():
                with csv_path.open(newline="", encoding="utf-8") as stream:
                    retained = [{k: row.get(k, "") for k in fields} for row in csv.DictReader(stream) if row["recording"] not in names]
            else:
                manifest = self.output / "manifest.json"
                if manifest.exists():
                    rows = json.loads(manifest.read_text(encoding="utf-8"))
                    retained = [{k: row.get(k, "") for k in fields} for row in rows.get("all_reviewed_stops", rows.get("reviewed_stops", [])) if row["recording"] not in names]
            self._csv(csv_path, retained + review_rows(payload, current), fields)
            return self.read()

    @staticmethod
    def _csv(path, rows, fields):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def archive(self):
        root = self.payload().get("data_directory")
        if not root:
            raise ValueError("Regenerate this report once to enable recording archiving")
        return RecordingArchive(root, self.rebuild)

    def recordings(self):
        if not self.payload().get("data_directory"):
            return {"enabled": False, "archived": []}
        return {"enabled": True, "archived": self.archive().entries()}

    def change_recording(self, name, action):
        with self.lock:
            archive = self.archive()
            self.save(self.read()["decisions"])
            archive.change(name, action, {r["recording"] for r in self.payload()["recordings"]})
            return {"ok": True}

    def rebuild(self):
        """Build separately, then publish a complete report, retaining review exports."""
        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="metro-rebuild-", dir=self.output.parent) as temporary:
            work = Path(temporary)
            config = work / "config.json"
            config.write_text(json.dumps(manifest["configuration"]), encoding="utf-8")
            staged = work / "report"
            command = [sys.executable, "-m", "metro_braking", "--data", self.payload()["data_directory"],
                       "--output", str(staged), "--config", str(config), "--allow-empty",
                       "--cache-dir", manifest.get("cache_directory", str(self.output / ".recording_cache")),
                       "--stops", str(self.review_dir / "stops.csv")]
            events = manifest.get("all_reviews", manifest.get("reviews", []))
            if events:
                event_path = work / "events.csv"
                pd.DataFrame(events).to_csv(event_path, index=False)
                command += ["--reviews", str(event_path)]
            result = subprocess.run(command, cwd=Path(__file__).resolve().parent.parent,
                                    capture_output=True, text=True)
            if result.returncode:
                raise ValueError("Report rebuild failed; recording move cancelled. " + (result.stderr or result.stdout)[-1500:])
            combined = work / "combined"
            shutil.copytree(self.output, combined)
            shutil.copytree(staged, combined, dirs_exist_ok=True)
            backup = work / "previous"
            self.output.rename(backup)
            try:
                combined.rename(self.output)
            except Exception:
                backup.rename(self.output)
                raise

    def export_sections(self):
        with self.lock:
            payload = self.payload()
            decisions = validate_decisions(payload, self.read()["decisions"])
            rows = review_rows(payload, decisions)
            candidates = review_candidates(payload, decisions)
            generation = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:12]
            plan = []
            metrics = []
            charts = []
            for recording in payload["recordings"]:
                accepted = [r for r in rows if r["recording"] == recording["recording"] and r["accepted"]]
                if not accepted:
                    continue
                signal = pd.read_csv(self.output / recording["signal_file"], float_precision="round_trip")
                own_metrics, own_charts = build_runs(recording["recording"], signal, pd.DataFrame(accepted), candidates, payload, recording)
                metrics.extend(own_metrics)
                charts.extend(own_charts)
                for number, start, end, section in split_sections(signal, pd.DataFrame(accepted)):
                    key = hashlib.sha256(recording["recording"].encode()).hexdigest()[:12]
                    relative = f"reviewed_sections/{generation}/{key}_{number:03d}.csv"
                    plan.append((relative, section, {"recording": recording["recording"], "section": number,
                        "start_s": start, "end_s": end, "file": relative, "contains_gap": section.block.nunique() > 1}))
            # All boundaries validated before writing any section.
            for relative, section, _ in plan:
                target = self.output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                section.to_csv(target, index=False)
            self._csv(self.output / "reviewed_sections.csv", [p[2] for p in plan],
                      ["recording", "section", "start_s", "end_s", "file", "contains_gap"])
            duration_statistics(metrics)
            write_trip_acceleration(self.output, metrics, payload.get("trip_window_seconds", 30),
                                    payload.get("trip_restart_offset_seconds", 0.5), payload.get("arrival_window_seconds", 20),
                                    [r.get("line") for r in payload["recordings"]])
            return {"sections": len(plan), "index": "reviewed_sections.csv",
                    "trips": charts,
                    "acceleration_csv": "trip_acceleration.csv",
                    "acceleration_plot": "trip_acceleration.png",
                    "deceleration_plot": "trip_deceleration.png",
                    "global_statistics_plot": "global_statistics.png",
                    "window_seconds": payload.get("trip_window_seconds", 30),
                    "complete_windows": sum(r["status"] == "complete" for r in metrics)}


def make_server(output, review_dir, port=8765):
    output = Path(output).resolve()
    store = ReviewStore(output, review_dir)
    store.payload()  # Fail early if analysis has not produced a report.

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output), **kwargs)

        def end_headers(self):
            # Reports and assets are regenerated in place during review.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def send_head(self):
            # Same-second regeneration can leave Last-Modified unchanged.
            if "If-Modified-Since" in self.headers:
                del self.headers["If-Modified-Since"]
            return super().send_head()

        def allowed(self):
            port = self.server.server_port
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in hosts:
                return False
            origin = self.headers.get("Origin")
            return not origin or origin in {f"http://{host}" for host in hosts}

        def json_response(self, value, status=200):
            content = json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if not self.allowed():
                return self.json_response({"error": "Local origin required"}, 403)
            if urlsplit(self.path).path == "/api/reviews":
                return self.json_response(store.read())
            if urlsplit(self.path).path == "/api/recordings":
                try:
                    return self.json_response(store.recordings())
                except (ValueError, OSError) as error:
                    return self.json_response({"error": str(error)}, 400)
            self.path = "/report.html" if self.path == "/" else self.path
            super().do_GET()

        def do_POST(self):
            if not self.allowed():
                return self.json_response({"error": "Local origin required"}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 1_000_000:
                    raise ValueError("Request too large")
                path = urlsplit(self.path).path
                if path == "/api/reviews":
                    body = json.loads(self.rfile.read(length))
                    return self.json_response(store.save(body["decisions"]))
                if path == "/api/recordings":
                    body = json.loads(self.rfile.read(length))
                    return self.json_response(store.change_recording(body["recording"], body["action"]))
                if path == "/api/export-sections":
                    return self.json_response(store.export_sections())
                return self.json_response({"error": "Unknown endpoint"}, 404)
            except (ValueError, KeyError, OSError, TypeError) as error:
                self.json_response({"error": str(error)}, 400)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_report(output, review_dir, port):
    server = make_server(output, review_dir, port)
    print(f"Review stops at http://127.0.0.1:{server.server_port}/report.html", flush=True)
    print(f"Serving report from {Path(output).resolve()}", flush=True)
    print(f"Decisions saved in {Path(review_dir).resolve()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
