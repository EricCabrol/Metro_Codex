import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd
from metro_braking.review import candidate_id, validate_decisions, ReviewStore, make_server


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "results"
        self.output.mkdir()
        recording = {"recording": "test", "start_s": 0, "end_s": 100, "signal_file": "signal_00.csv"}
        candidates = [dict(recording="test", start_s=20., end_s=40., id="one"),
                      dict(recording="test", start_s=70., end_s=90., id="two")]
        self.payload = {"version": 1, "recordings": [recording], "candidates": candidates}
        (self.output / "report_data.json").write_text(json.dumps(self.payload))
        (self.output / "report.html").write_text("<h1>Review fixture</h1>")
        pd.DataFrame({"seconds_elapsed": range(101), "block": [0]*50+[1]*51,
                      "axis_filtered_mps2": [0]*101}).to_csv(self.output / "signal_00.csv", index=False)
        self.store = ReviewStore(self.output, self.root / "reviews")

    def tearDown(self):
        self.temp.cleanup()

    def test_persistence_csv_and_section_exports(self):
        decisions = {"one": {"decision": "accepted", "time_s": 19., "restart_s": 40., "note": 'quote " and, comma'},
                     "two": {"decision": "discarded", "time_s": 70., "restart_s": 90., "note": "false positive"}}
        self.store.save(decisions)
        reloaded = ReviewStore(self.output, self.root / "reviews")
        self.assertEqual(reloaded.read()["decisions"], decisions)
        rows = pd.read_csv(self.root / "reviews" / "stops.csv")
        self.assertEqual(rows.accepted.tolist(), [True, False])
        self.assertEqual(reloaded.export_sections()["sections"], 0)
        decisions["two"]["decision"] = "accepted"
        self.store.save(decisions)
        self.assertEqual(self.store.export_sections()["sections"], 1)
        sections = pd.read_csv(self.output / "reviewed_sections.csv")
        self.assertTrue(sections.iloc[0].contains_gap)
        signal = pd.read_csv(self.output / sections.iloc[0].file)
        self.assertEqual(signal.seconds_elapsed.iloc[0], 19)
        self.assertEqual(signal.seconds_elapsed.iloc[-1], 69)
        metrics = pd.read_csv(self.output / "trip_acceleration.csv")
        # Restart at 40s moves the window across the gap at 50s.
        self.assertEqual(metrics.iloc[0].status, "gap_or_missing_samples")
        self.assertTrue(pd.isna(metrics.iloc[0].max_acceleration_mps2))
        self.assertTrue((self.output / "trip_acceleration.png").exists())

    def test_pending_and_changed_candidates_are_not_accepted(self):
        self.store.save({"one": {"decision": "pending", "time_s": 20., "note": ""}})
        self.assertTrue(pd.read_csv(self.root / "reviews" / "stops.csv").empty)
        self.payload["candidates"][0]["id"] = "new-boundary"
        (self.output / "report_data.json").write_text(json.dumps(self.payload))
        self.assertEqual(self.store.read()["decisions"], {})
        self.assertEqual(self.store.read()["unmatched_decisions"], 1)

    def test_export_uses_configured_shared_window(self):
        self.payload["trip_window_seconds"] = 10
        (self.output / "report_data.json").write_text(json.dumps(self.payload))
        signal = pd.read_csv(self.output / "signal_00.csv")
        signal.loc[22, "axis_filtered_mps2"] = -2
        signal.loc[25, "axis_filtered_mps2"] = 1
        signal.loc[35, "axis_filtered_mps2"] = 8
        signal.to_csv(self.output / "signal_00.csv", index=False)
        self.store.save({"one": {"decision": "accepted", "time_s": 20, "restart_s": 21},
                         "two": {"decision": "accepted", "time_s": 70}})
        result = self.store.export_sections()
        self.assertEqual(result["window_seconds"], 10)
        self.assertEqual(len(result["trips"]), 1)
        self.assertEqual(result["trips"][0]["metrics"]["max_deceleration_mps2"], 2)
        row = pd.read_csv(self.output / "trip_acceleration.csv").iloc[0]
        self.assertEqual(row.window_start_s, 21.5)
        self.assertEqual(row.window_end_s, 31.5)
        self.assertEqual(row.max_acceleration_mps2, 1)
        self.assertEqual(row.max_deceleration_mps2, 2)
        self.assertLess(row.deceleration_peak_time_s, row.peak_time_s)

    def test_invalid_times_unknown_candidates_and_duplicates_rejected(self):
        for decisions in ({"missing": {"decision": "accepted", "time_s": 1}},
                          {"one": {"decision": "accepted", "time_s": float("nan")}},
                          {"one": {"decision": "accepted", "time_s": 101}},
                          {"one": {"decision": "accepted", "time_s": 20}, "two": {"decision": "accepted", "time_s": 20}}):
            with self.assertRaises(ValueError):
                validate_decisions(self.payload, decisions)

    def test_restart_validation_and_legacy_decision_migration(self):
        legacy = {"one": {"decision": "accepted", "time_s": 20, "note": "kept"}}
        validated = validate_decisions(self.payload, legacy)
        self.assertEqual(validated["one"]["restart_s"], 40)
        self.assertEqual(validated["one"]["note"], "kept")
        for restart in [19, 101, float("nan"), True]:
            with self.assertRaises(ValueError):
                validate_decisions(self.payload, {"one": {"decision": "accepted", "time_s": 20, "restart_s": restart}})

    def test_browser_api_roundtrip_and_origin_check(self):
        server = make_server(self.output, self.root / "reviews", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            data = json.dumps({"decisions": {"one": {"decision": "accepted", "time_s": 20., "note": ""}}}).encode()
            request = Request(url + "/api/reviews", data, {"Content-Type": "application/json", "Origin": url})
            with urlopen(request) as response:
                self.assertEqual(json.load(response)["decisions"]["one"]["decision"], "accepted")
            with urlopen(url + "/api/reviews") as response:
                self.assertEqual(json.load(response)["decisions"]["one"]["time_s"], 20.)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url + "/api/reviews", data, {"Origin": "https://example.com"}))
            self.assertEqual(error.exception.code, 403)
            with urlopen(Request(url + "/api/export-sections", b"{}")) as response:
                self.assertEqual(json.load(response)["sections"], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_candidate_ids_use_boundaries_not_row_numbers(self):
        first = dict(recording="trip", start_s=1., end_s=21.)
        self.assertEqual(candidate_id(first), candidate_id(first | {"status": "short"}))
        self.assertNotEqual(candidate_id(first), candidate_id(first | {"start_s": 2.}))


if __name__ == "__main__":
    unittest.main()
