import unittest
import numpy as np
import pandas as pd

from metro_braking.trip_acceleration import arrival_deceleration


class ArrivalDecelerationTests(unittest.TestCase):
    def signal(self):
        t = np.arange(0, 101, 0.05)
        a = np.full(len(t), 0.1)
        for time, value in [(20, -9), (65, -0.8), (75, -1.4), (80, -12), (85, -15)]:
            a[np.argmin(abs(t-time))] = value
        return pd.DataFrame({'seconds_elapsed':t, 'axis_filtered_mps2':a, 'block':0})

    def test_peak_is_inside_twenty_seconds_strictly_before_arrival(self):
        result = arrival_deceleration(self.signal(), 10, 80)
        self.assertEqual(result['arrival_deceleration_status'], 'complete')
        self.assertEqual(result['max_arrival_deceleration_mps2'], 1.4)
        self.assertAlmostEqual(result['arrival_deceleration_peak_time_s'], 75)
        self.assertEqual(result['arrival_window_start_s'], 60)
        self.assertEqual(result['arrival_window_end_s'], 80)
        short_window = arrival_deceleration(self.signal(), 10, 80, 10)
        self.assertEqual(short_window['arrival_window_start_s'], 70)

    def test_unknown_arrival_is_not_replaced_by_recording_end(self):
        result = arrival_deceleration(self.signal(), 10, None)
        self.assertEqual(result['arrival_deceleration_status'], 'arrival_not_identified')
        self.assertIsNone(result['max_arrival_deceleration_mps2'])
        self.assertIsNone(result['arrival_window_end_s'])

    def test_missing_samples_short_runs_and_no_negative_acceleration(self):
        signal = self.signal()
        signal = signal[(signal.seconds_elapsed < 70) | (signal.seconds_elapsed > 71)]
        result = arrival_deceleration(signal, 10, 80)
        self.assertEqual(result['arrival_deceleration_status'], 'gap_or_missing_samples')
        self.assertIsNone(result['max_arrival_deceleration_mps2'])
        self.assertEqual(arrival_deceleration(self.signal(), 70, 80)['arrival_deceleration_status'], 'short_run')
        positive = self.signal(); positive.axis_filtered_mps2 = 0.2
        self.assertEqual(arrival_deceleration(positive, 10, 80)['arrival_deceleration_status'], 'no_deceleration')
        for invalid in [0, -1, float('nan')]:
            with self.assertRaises(ValueError):
                arrival_deceleration(self.signal(), 10, 80, invalid)
