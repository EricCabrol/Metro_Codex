"""Pages comparisons use current filtered exports and expose run-relative samples only."""
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.build_pages_demo import comparison_trips


class PagesDemoTests(unittest.TestCase):
    def test_comparisons_use_latest_export_and_exclude_singletons_and_partial_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);recordings=[];rows=[]
            for i,start in enumerate([10.,20.]):
                name=f'private-recording-{i}'
                signal=f'signal_{i:02d}.csv'
                pd.DataFrame({'seconds_elapsed':[start-1,start,start+1,start+2,start+3],
                              'axis_filtered_mps2':[9,.1,.2,-.3,8],'block':0}).to_csv(root/signal,index=False)
                recordings.append(dict(recording=name,signal_file=signal))
                rows.append(dict(recording=name,display_recording=f'Recording {i}',section=1,line='4',
                    origin_station='A',destination_station='B',start_s=start-5,restart_s=start,end_s=start+2,
                    coverage='complete',duration_eligible=True))
            rows += [dict(rows[0],recording='partial',duration_eligible=False,coverage='arrival_not_identified'),
                     dict(rows[0],recording='reverse',origin_station='B',destination_station='A'),
                     dict(rows[0],recording='other-line',line='8')]
            # Embedded charts can be stale after review; the exporter must use the CSV rows and signals.
            trips=comparison_trips(dict(recordings=recordings,trips=[]),pd.DataFrame(rows),root)
            self.assertEqual(len(trips),2)
            for trip in trips:
                self.assertEqual(trip['blocks'],[[[0.,.1],[1.,.2],[2.,-.3]]])
                self.assertEqual(trip['metrics']['start_s'],0)
                self.assertEqual(trip['metrics']['restart_s'],0)
                self.assertEqual(trip['metrics']['end_s'],2)
                self.assertTrue(trip['metrics']['recording'].startswith('run-'))
            encoded=json.dumps(trips)
            self.assertNotIn('private-recording',encoded)
            self.assertNotIn('signal_00.csv',encoded)
            self.assertIn('Recording 0',encoded)

    def test_empty_or_singleton_exports_do_not_require_signal_files(self):
        self.assertEqual(comparison_trips({'recordings':[]},pd.DataFrame(),Path('/unused')),[])
        rows=pd.DataFrame([dict(recording='one',line='4',origin_station='A',destination_station='B',
                                duration_eligible=True,coverage='complete')])
        self.assertEqual(comparison_trips({'recordings':[]},rows,Path('/unused')),[])


if __name__=='__main__':
    unittest.main()
