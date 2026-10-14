import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from metro_braking.trip_acceleration import trip_acceleration, write_trip_acceleration, trip_chart


class TripAccelerationTests(unittest.TestCase):
    def test_chart_preserves_filtered_precision_and_gap_blocks(self):
        signal = self.departure_signal()
        signal.loc[signal.seconds_elapsed >= 42, "block"] = 1
        metric = trip_acceleration("trip", 1, 10, 50, signal)
        chart = trip_chart(metric, signal)
        self.assertEqual(len(chart["blocks"]), 2)
        self.assertEqual(sum(map(len, chart["blocks"])), len(signal))
        peak = next(row for row in chart["blocks"][0]
                    if row[0] == metric["peak_time_s"] - metric["start_s"])
        self.assertEqual(peak[1], metric["max_acceleration_mps2"])
        self.assertEqual(chart["metrics"], metric)
    def departure_signal(self):
        signal = self.signal()
        signal.axis_filtered_mps2 = 0.4
        for time, value in [(15, -3), (18, 2), (25, -0.8), (32, -1.5), (44, -8)]:
            signal.loc[(signal.seconds_elapsed - time).abs().idxmin(), "axis_filtered_mps2"] = value
        return signal

    def test_extrema_use_same_window_without_order_dependency(self):
        row = trip_acceleration("trip", 1, 10, 50, self.departure_signal())
        self.assertEqual(row["max_acceleration_mps2"], 2)
        self.assertEqual(row["max_deceleration_mps2"], 3)
        self.assertAlmostEqual(row["peak_time_s"], 18)
        self.assertAlmostEqual(row["deceleration_peak_time_s"], 15)
        self.assertEqual(row["window_end_s"], 40)
        shorter = trip_acceleration("trip", 1, 10, 50, self.departure_signal(), 6)
        self.assertEqual(shorter["max_acceleration_mps2"], 0.4)
        self.assertEqual(shorter["max_deceleration_mps2"], 3)
        self.assertEqual(shorter["window_end_s"], 16)

    def test_gap_or_short_trip_excludes_both_measurements(self):
        signal = self.departure_signal()
        gap = signal[(signal.seconds_elapsed < 30) | (signal.seconds_elapsed > 31)]
        row = trip_acceleration("trip", 1, 10, 50, gap)
        self.assertEqual(row["status"], "gap_or_missing_samples")
        self.assertIsNone(row["max_acceleration_mps2"])
        self.assertIsNone(row["max_deceleration_mps2"])
        short = trip_acceleration("trip", 1, 10, 30, signal[signal.seconds_elapsed < 30])
        self.assertEqual(short["status"], "short_trip")
        self.assertIsNone(short["max_deceleration_mps2"])

    def test_deceleration_does_not_require_positive_acceleration(self):
        signal = self.departure_signal()
        signal.loc[signal.axis_filtered_mps2 < 0, "axis_filtered_mps2"] = 0
        row = trip_acceleration("trip", 1, 10, 50, signal)
        self.assertEqual(row["deceleration_status"], "no_deceleration")
        self.assertIsNone(row["max_deceleration_mps2"])
        signal.axis_filtered_mps2 = -0.1
        row = trip_acceleration("trip", 1, 10, 50, signal)
        self.assertEqual(row["deceleration_status"], "complete")
        self.assertEqual(row["max_deceleration_mps2"], 0.1)
        for invalid in [0, -1, float("nan")]:
            with self.assertRaises(ValueError):
                trip_acceleration("trip", 1, 10, 50, signal, invalid)

    def signal(self):
        t = np.arange(10, 50, 0.05)
        acceleration = np.full(len(t), -0.5)
        acceleration[np.argmin(abs(t - 18))] = 1.25
        acceleration[np.argmin(abs(t - 35))] = 9
        return pd.DataFrame({"seconds_elapsed": t, "block": 0,
                             "axis_filtered_mps2": acceleration})

    def test_window_uses_validated_start_and_signed_filtered_peak(self):
        row = trip_acceleration("trip", 1, 10, 50, self.signal())
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["max_acceleration_mps2"], 9)
        self.assertAlmostEqual(row["peak_time_s"], 35)
        self.assertEqual(row["window_end_s"], 40)
        signal = self.signal()
        signal.axis_filtered_mps2 = -2.0
        signal.loc[20, "axis_filtered_mps2"] = -0.2
        self.assertEqual(trip_acceleration("trip", 1, 10, 50, signal)["max_acceleration_mps2"], -0.2)

    def test_short_and_missing_windows_are_excluded(self):
        signal = self.signal()
        short = trip_acceleration("trip", 1, 10, 25, signal[signal.seconds_elapsed < 25])
        self.assertEqual(short["status"], "short_trip")
        self.assertIsNone(short["max_acceleration_mps2"])
        gap = signal[(signal.seconds_elapsed < 15) | (signal.seconds_elapsed > 17)]
        self.assertEqual(trip_acceleration("trip", 1, 10, 50, gap)["status"], "gap_or_missing_samples")
        edge = signal[signal.seconds_elapsed > 11]
        self.assertEqual(trip_acceleration("trip", 1, 10, 50, edge)["status"], "gap_or_missing_samples")
        signal.loc[signal.seconds_elapsed >= 15, "block"] = 1
        self.assertEqual(trip_acceleration("trip", 1, 10, 50, signal)["status"], "gap_or_missing_samples")

    def test_exports_include_outcomes_and_plot(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            rows = [trip_acceleration("trip", 1, 10, 50, self.signal())]
            write_trip_acceleration(output, rows)
            self.assertEqual(pd.read_csv(output / "trip_acceleration.csv").iloc[0].max_acceleration_mps2, 9)
            self.assertEqual((output / "trip_acceleration.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual((output / "trip_deceleration.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            write_trip_acceleration(output, [])
            self.assertTrue(pd.read_csv(output / "trip_acceleration.csv").empty)
