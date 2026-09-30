import tempfile
import unittest
from pathlib import Path
import json

import numpy as np
import pandas as pd

from metro_braking.analysis import load_recording, detect_events, detect_stops, check_orientation, apply_reviews, lowpass, split_sections
from metro_braking.routes import route_from_name, compare_stop_count


CONFIG = json.loads((Path(__file__).parents[1] / "config.json").read_text())


class PipelineTests(unittest.TestCase):
    def test_minimum_dwell_rejects_short_pause_and_tolerates_brief_disturbance(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            t = np.arange(0, 100, 0.01)
            motion = 0.6 * np.sin(2 * np.pi * 2 * t)
            motion[((t >= 20) & (t < 30)) | ((t >= 50) & (t < 80))] = 0
            motion[(t >= 63) & (t < 63.15)] = 0.6
            pd.DataFrame({"seconds_elapsed": t, "x": motion, "y": 1.2, "z": 9.81}).to_csv(folder / "AccelerometerUncalibrated.csv", index=False)
            intervals, _, _ = detect_stops(folder, CONFIG)
            self.assertEqual(len(intervals), 1)
            self.assertGreater(intervals[0]["start_s"], 50)
            self.assertGreaterEqual(intervals[0]["duration_s"], 20)
            self.assertGreaterEqual(intervals[0]["quiet_fraction"], 0.9)

    def test_folder_convention_and_opposite_sign_are_checked(self):
        for marker, physical_polarity, expected_opposite in (("", 1, False), ("_Yinv", -1, False), ("_Yinv", 1, True)):
            with self.subTest(marker=marker, physical_polarity=physical_polarity), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp) / f"L4_Les_Halles_-_Montparnasse{marker}-2024-02-13_17-29-59"
                folder.mkdir()
                t = np.arange(0, 90, 0.01)
                a = np.zeros_like(t)
                a[(t >= 5) & (t < 15)] = 1
                a[(t >= 25) & (t < 35)] = -1
                a[(t >= 65) & (t < 75)] = 1
                frame = pd.DataFrame({"seconds_elapsed": t, "x": 0, "y": a * physical_polarity, "z": 9.81})
                frame.to_csv(folder / "AccelerometerUncalibrated.csv", index=False)
                frame["z"] = 0
                frame.to_csv(folder / "Accelerometer.csv", index=False)
                segments, summary = load_recording(folder, CONFIG)
                intervals, diagnostics, _ = detect_stops(folder, CONFIG)
                checks = check_orientation(summary, intervals, diagnostics, CONFIG)
                self.assertTrue(checks)
                self.assertEqual(summary["orientation_inconsistent"], expected_opposite)
                self.assertEqual(summary["axis_y"], 1)
                self.assertEqual(summary["polarity"], -1 if marker else 1)
                if expected_opposite:
                    events, _ = detect_events(segments, summary, CONFIG)
                    self.assertEqual({e["sign"] for e in events}, {-1, 1})

    def test_routes_aliases_reverse_direction_and_arrival_counts(self):
        cases = [("L4_Les_Halles_-_Montparnasse_", 8, 7),
                 ("L4_Chatelet_-_Montparnasse_-_brutal", 7, 6),
                 ("L4_Etienne_Marcel_-_Montparnasse_-_brutal_2_stops_annotated", 9, 8),
                 ("L8_Madeleine_-_La_Motte_Yinv", 5, 4),
                 ("L13_Montparnasse_-_Pernety_bag_Yinv", 2, 1),
                 ("L6_Raspail_-_Montparnasse_-_side", 2, 1),
                 ("L4_Montparnasse_-_Les_Halles", 8, 7)]
        for body, arrivals, intermediate in cases:
            with self.subTest(body=body):
                route = route_from_name(body + "-2024-02-13_17-29-59")
                self.assertEqual(route["route_status"], "resolved")
                self.assertEqual(route["expected_arrivals"], arrivals)
                self.assertEqual(route["expected_intermediate_stops"], intermediate)
        self.assertNotEqual(route_from_name("L4_Unknown_-_Montparnasse-2024-02-13_17-29-59")["route_status"], "resolved")
        summary = route_from_name("L13_Montparnasse_-_Pernety-2024-02-13_17-29-59")
        comparison = compare_stop_count(summary, [{"boundary": "interior"}, {"boundary": "recording edge"}])
        self.assertEqual(comparison["interior_stop_count_difference"], 0)
        comparison = compare_stop_count(summary, [])
        self.assertEqual(comparison["interior_stop_count_difference"], -1)

    def test_stops_use_uncalibrated_constancy_despite_calibrated_tail(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            t = np.arange(0, 45, 0.01)
            raw_x = np.where(t < 10, 0.6 * np.sin(2 * np.pi * 2 * t), 0.0)
            raw = pd.DataFrame({"seconds_elapsed": t, "x": raw_x, "y": 1.2, "z": 9.81})
            raw.to_csv(folder / "AccelerometerUncalibrated.csv", index=False)
            calibrated = raw.copy()
            calibrated["x"] = np.where(t < 10, raw_x, 0.8 * np.exp(-(t - 10) / 10))
            calibrated["y"] = 0
            calibrated["z"] = 0
            calibrated.to_csv(folder / "Accelerometer.csv", index=False)
            intervals, _, summary = detect_stops(folder, CONFIG)
            self.assertEqual(summary["stop_source"], "AccelerometerUncalibrated.csv")
            arrival = next(q for q in intervals if q["end_s"] > 20)
            self.assertGreater(arrival["start_s"], 10)
            self.assertLess(arrival["start_s"], 11.5)
            # Constant gravity and arbitrary axis offsets must not change timing.
            raw[["x", "y", "z"]] += [4, -3, 2]
            raw.to_csv(folder / "AccelerometerUncalibrated.csv", index=False)
            shifted, _, _ = detect_stops(folder, CONFIG)
            self.assertAlmostEqual(shifted[-1]["start_s"], arrival["start_s"])

    def test_stop_windows_do_not_cross_raw_gaps_and_no_calibrated_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder)
            intervals, diagnostics, summary = detect_stops(folder, CONFIG)
            self.assertEqual(intervals, [])
            self.assertEqual(summary["stop_source"], "unavailable")
            self.fixture(folder, gap=True, gravity=True)
            intervals, diagnostics, summary = detect_stops(folder, CONFIG)
            self.assertEqual(summary["stop_gap_count"], 1)
            self.assertEqual(len(diagnostics), 2)
            self.assertTrue(np.isnan(diagnostics[1]["variation"][0]))
            self.assertTrue(all(not (q["start_s"] < 30 < 35 < q["end_s"]) for q in intervals))

    def fixture(self, folder, gap=False, gravity=False):
        t = np.arange(0, 50, 0.01)
        # Known smooth 1 m/s² braking plateau with one-second ramps.
        pulse = np.clip(t - 12, 0, 1) * np.clip(23 - t, 0, 1)
        frame = pd.DataFrame({"seconds_elapsed": t, "x": -pulse,
                              "y": 0.02 * np.sin(2 * np.pi * 7 * t), "z": 9.81 if gravity else 0.0})
        if gap:
            frame = frame[(t < 30) | (t > 35)]
        frame.to_csv(folder / ("AccelerometerUncalibrated.csv" if gravity else "Accelerometer.csv"), index=False)

    def test_known_braking_metrics_and_polarity(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder)
            config = CONFIG | {"recordings": {folder.name: {"axis": [1, 0, 0], "polarity": 1}}}
            segments, summary = load_recording(folder, config)
            events, _ = detect_events(segments, summary, config)
            self.assertEqual(len(events), 1)
            self.assertAlmostEqual(events[0]["peak_mps2"], 1, delta=0.1)
            self.assertAlmostEqual(events[0]["delta_velocity_mps"], 10, delta=0.7)
            self.assertGreater(events[0]["jerk_p95_mps3"], 0.4)

    def test_gap_is_not_interpolated_and_review_cannot_cross_it(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder, gap=True)
            segments, summary = load_recording(folder, CONFIG)
            self.assertEqual(len(segments), 2)
            self.assertEqual(summary["gap_count"], 1)
            reviews = pd.DataFrame([dict(recording=folder.name, event_id="manual", start_s=29,
                                        end_s=36, sign=-1, label="strong")])
            with self.assertRaises(ValueError):
                apply_reviews([], segments, summary, CONFIG, reviews)

    def test_unknown_direction_and_uncalibrated_gravity(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder, gravity=True)
            segments, summary = load_recording(folder, CONFIG)
            self.assertEqual(summary["polarity"], 0)
            self.assertIn("uncalibrated-only", summary["quality_flags"])
            events, _ = detect_events(segments, summary, CONFIG)
            self.assertTrue(events)
            self.assertTrue(all(e["status"] == "direction uncertain acceleration candidate" for e in events))
            self.assertAlmostEqual(events[0]["peak_mps2"], 1, delta=0.1)

    def test_review_replaces_candidates_and_rejects_false_positives(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder)
            segments, summary = load_recording(folder, CONFIG)
            rows = pd.DataFrame([dict(recording=folder.name, event_id="one", start_s=12,
                      end_s=23, sign=-1, label="too harsh", accepted=True),
                      dict(recording=folder.name, event_id="two", start_s=25,
                      end_s=27, sign=1, label="", accepted=False)])
            events = apply_reviews([{"event_id": "old"}], segments, summary, CONFIG, rows)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["label"], "too harsh")
            self.assertTrue(events[0]["reviewed"])

    def test_filter_preserves_slow_motion_and_rejects_vibration(self):
        hz = 100
        t = np.arange(0, 60, 1 / hz)
        slow = np.sin(2 * np.pi * 0.1 * t)
        filtered = lowpass(slow + np.sin(2 * np.pi * 8 * t), hz, 0.5)
        self.assertLess(np.sqrt(np.mean((filtered[1000:-1000] - slow[1000:-1000]) ** 2)), 0.01)

    def test_reviewed_stops_cut_sections_without_filling_gaps(self):
        signal = pd.DataFrame({"seconds_elapsed": [0, 1, 2, 8, 9, 10], "block": [0, 0, 0, 1, 1, 1]})
        stops = pd.DataFrame({"time_s": [0, 10], "accepted": [True, True]})
        sections = split_sections(signal, stops)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0][3].seconds_elapsed.tolist(), [0, 1, 2, 8, 9])
        self.assertEqual(sections[0][3].block.nunique(), 2)
        stops.loc[1, "time_s"] = 11
        with self.assertRaises(ValueError):
            split_sections(signal, stops)


if __name__ == "__main__":
    unittest.main()
