import tempfile
import unittest
from pathlib import Path

import pandas as pd

from metro_braking.global_statistics import global_statistics, write_global_statistics


class GlobalStatisticsTests(unittest.TestCase):
    def row(self, line='4', departure=1., arrival=2., **changes):
        return dict(line=line, max_deceleration_mps2=departure, max_arrival_deceleration_mps2=arrival,
                    status='complete', deceleration_status='complete', arrival_deceleration_status='complete', **changes)

    def test_quartiles_pool_runs_by_line_and_keep_phases_separate(self):
        rows = [self.row(departure=value, arrival=10+value) for value in [1, 2, 3, 4, 100]]
        rows += [self.row('13', 6, 7), self.row('8', 8, 9)]
        lines, groups, summaries = global_statistics(rows)
        self.assertEqual(lines, ['4', '8', '13'])
        self.assertEqual(groups['4', 'departure'], [1, 2, 3, 4, 100])
        self.assertEqual(groups['4', 'arrival'], [11, 12, 13, 14, 110])
        summary = next(r for r in summaries if r['line']=='4' and r['phase']=='departure')
        self.assertEqual([summary[k] for k in ['valid_runs', 'excluded_runs', 'minimum_mps2', 'q1_mps2', 'median_mps2', 'q3_mps2', 'maximum_mps2']], [5, 0, 1, 2, 3, 4, 100])

    def test_each_phase_uses_only_valid_windows_including_partial_runs(self):
        # A missing departure must not suppress a valid arrival, or vice versa.
        partial_start = self.row(departure=None, coverage='departure_not_identified')
        partial_start.update(status='departure_not_identified', deceleration_status='incomplete_window')
        partial_end = self.row(arrival=None, coverage='arrival_not_identified')
        partial_end['arrival_deceleration_status']='arrival_not_identified'
        gap = self.row(departure=99, arrival=99)
        gap.update(status='gap_or_missing_samples', arrival_deceleration_status='gap_or_missing_samples')
        no_braking = self.row(departure=None, arrival=None)
        no_braking.update(deceleration_status='no_deceleration', arrival_deceleration_status='no_deceleration')
        rows = [partial_start, partial_end, gap, no_braking,
                self.row(departure=float('nan'), arrival=float('inf')), self.row(departure=-2, arrival=-3)]
        _, groups, summaries = global_statistics(rows)
        self.assertEqual(groups['4', 'departure'], [1])
        self.assertEqual(groups['4', 'arrival'], [2])
        self.assertTrue(all(r['valid_runs']==1 and r['excluded_runs']==5 for r in summaries))
        self.assertEqual(global_statistics([self.row(line=None)])[0], [])
        lines, groups, summaries = global_statistics([], ['6'])
        self.assertEqual(lines, ['6'])
        self.assertEqual(groups['6', 'departure'], [])
        self.assertTrue(all(r['valid_runs']==0 for r in summaries))

    def test_empty_and_single_sample_exports_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)
            write_global_statistics(output, [])
            self.assertTrue(pd.read_csv(output/'global_statistics.csv').empty)
            self.assertGreater((output/'global_statistics.png').stat().st_size, 1000)
            write_global_statistics(output, [self.row(departure=None, arrival=.75)])
            table=pd.read_csv(output/'global_statistics.csv')
            self.assertEqual(table.valid_runs.tolist(), [0,1])
            self.assertTrue(pd.isna(table.iloc[0].median_mps2))
            self.assertEqual(table.iloc[1].median_mps2, .75)


if __name__ == '__main__':
    unittest.main()
