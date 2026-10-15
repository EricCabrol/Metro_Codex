import unittest

import numpy as np
import pandas as pd

from metro_braking.routes import display_recording_name
from metro_braking.runs import build_runs, duration_statistics


NAME = 'L4_Chatelet_-_Montparnasse_-_brutal-2024-03-29_18-03-15'


class RunTests(unittest.TestCase):
    def fixture(self):
        t = np.arange(0, 501, 0.05)
        signal = pd.DataFrame({'seconds_elapsed': t, 'axis_filtered_mps2': np.sin(t), 'block': 0})
        times = [50., 120., 190., 260., 330., 400.]
        rows = pd.DataFrame({'recording': NAME, 'time_s': times, 'accepted': True})
        candidates = [dict(recording=NAME, start_s=t, end_s=t+15, boundary='interior') for t in times]
        return signal, rows, candidates

    def test_pretty_names_preserve_metadata_and_actual_date(self):
        self.assertEqual(display_recording_name(NAME), 'L4 Châtelet – Montparnasse 18h03, 29/03/2024 (brutal)')
        name = 'L4_Les_Halles_-_Montparnasse_-_brutal_avt_Odeon_-_side_-_89n2_1013-2024-05-06_16-43-20'
        self.assertIn('(brutal avt Odeon side 89n2 1013)', display_recording_name(name))
        self.assertIn('La Motte-Picquet - Grenelle', display_recording_name('L8_Madeleine_-_La_Motte_Yinv-2024-02-09_16-30-00'))
        self.assertTrue(display_recording_name('L8_Madeleine_-_La_Motte_Yinv-2024-02-09_16-30-00').endswith('(Yinv)'))

    def test_six_interior_stops_give_five_complete_and_two_partial_runs(self):
        signal, rows, candidates = self.fixture()
        metrics, charts = build_runs(NAME, signal, rows, candidates, {})
        self.assertEqual(len(metrics), 7)
        self.assertEqual(metrics[0]['run_label'], 'Châtelet – Cité')
        self.assertEqual(metrics[0]['coverage'], 'departure_not_identified')
        self.assertIsNone(metrics[0]['restart_s'])
        self.assertIsNone(metrics[0]['max_deceleration_mps2'])
        self.assertEqual(metrics[1]['run_label'], 'Cité – Saint-Michel')
        self.assertEqual(metrics[1]['duration_s'], 55)
        self.assertEqual(metrics[1]['window_start_s'], 65.5)
        self.assertEqual(metrics[-1]['run_label'], 'Saint-Placide – Montparnasse - Bienvenüe')
        self.assertEqual(metrics[-1]['coverage'], 'arrival_not_identified')
        self.assertIsNone(metrics[-1]['duration_s'])
        self.assertEqual(metrics[-1]['status'], 'complete')
        self.assertFalse(metrics[-1]['duration_eligible'])
        self.assertEqual(len(charts), 7)

    def test_candidate_ids_preserve_station_assignment_after_arrival_edit(self):
        signal, rows, candidates = self.fixture()
        for index, candidate in enumerate(candidates):
            candidate['id'] = str(index)
        rows['candidate_id'] = [str(i) for i in range(len(rows))]
        rows['restart_s'] = rows.time_s + 15
        rows.loc[0, 'time_s'] -= 0.5
        metrics, _ = build_runs(NAME, signal, rows, candidates, {})
        self.assertEqual(metrics[1]['run_label'], 'Cité – Saint-Michel')
        self.assertEqual(metrics[1]['restart_s'], 65)

    def test_mismatches_and_gaps_keep_neutral_names(self):
        signal, rows, candidates = self.fixture()
        metrics, _ = build_runs(NAME, signal, rows.iloc[:-1], candidates, {})
        self.assertTrue(all(m['origin_station'] is None for m in metrics))
        signal.loc[signal.seconds_elapsed > 210, 'block'] = 1
        metrics, _ = build_runs(NAME, signal, rows, candidates, {})
        self.assertEqual(len(metrics), 5)
        self.assertTrue(all(m['origin_station'] is None for m in metrics))

    def test_direction_specific_duration_statistics_and_partial_exclusion(self):
        def row(duration, origin='A', destination='B', eligible=True):
            return dict(line='4', origin_station=origin, destination_station=destination,
                        duration_s=duration, duration_eligible=eligible)
        rows = [row(t) for t in [40, 41, 39, 40, 42, 38, 40, 100]]
        reverse = row(200, 'B', 'A')
        partial = row(None, eligible=False)
        duration_statistics(rows + [reverse, partial])
        self.assertEqual(rows[0]['duration_sample_count'], 8)
        self.assertAlmostEqual(rows[0]['duration_mean_s'], 47.5)
        self.assertAlmostEqual(rows[0]['duration_std_s'], np.std([40,41,39,40,42,38,40,100], ddof=1))
        self.assertTrue(rows[-1]['duration_warning'])
        self.assertFalse(rows[0]['duration_warning'])
        self.assertEqual(reverse['duration_check_status'], 'insufficient_samples')
        self.assertFalse(partial['duration_warning'])
        self.assertEqual(partial['duration_check_status'], 'partial_or_unassigned')
