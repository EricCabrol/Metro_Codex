"""Recording removal, recovery and continuity diagnostics."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from metro_braking.analysis import load_recording
from metro_braking.cli import continuity_notice
from metro_braking.recordings import RecordingArchive, available_review_rows
from metro_braking.review import ReviewStore

CONFIG = json.loads((Path(__file__).parents[1] / 'config.json').read_text())


class RecordingTests(unittest.TestCase):
    def test_absent_reviews_are_ignored_without_modifying_input(self):
        rows = pd.DataFrame([{'recording': 'present', 'time_s': 1}, {'recording': 'gone', 'time_s': 2}])
        original = rows.copy(deep=True)
        current, ignored = available_review_rows(rows, {'present'}, 'stop reviews')
        self.assertEqual(current.recording.tolist(), ['present'])
        self.assertEqual(ignored, ['gone'])
        pd.testing.assert_frame_equal(rows, original)

    def test_archive_roundtrip_collision_paths_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / 'data'
            (data / 'one').mkdir(parents=True)
            (data / 'one' / 'source.csv').write_bytes(b'original data')
            calls = []
            archive = RecordingArchive(data, lambda: calls.append(True))
            archive.change('one', 'archive', {'one'})
            self.assertFalse((data / 'one').exists())
            self.assertEqual(len(archive.entries()), 1)
            (data / 'one').mkdir()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                archive.change('one', 'restore', set())
            (data / 'one').rmdir()
            archive.change('one', 'restore', set())
            self.assertEqual((data / 'one' / 'source.csv').read_bytes(), b'original data')
            self.assertEqual(archive.entries(), [])
            self.assertEqual(len(calls), 2)
            for name in ('../one', '..', '/tmp', 'a\\b'):
                with self.assertRaises(ValueError):
                    archive.change(name, 'archive', {name})
            def fail():
                raise ValueError('rebuild failed')
            archive.rebuild = fail
            with self.assertRaisesRegex(ValueError, 'rebuild failed'):
                archive.change('one', 'archive', {'one'})
            self.assertTrue((data / 'one' / 'source.csv').exists())
            self.assertEqual(archive.entries(), [])

    def test_gap_summary_uses_timestamps_and_reports_both_streams(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            t = np.r_[np.arange(0, 5, .02), np.arange(10, 15, .02)]
            pd.DataFrame({'seconds_elapsed': t, 'x': 0, 'y': 0, 'z': 0}).to_csv(data / 'Accelerometer.csv', index=False)
            _, summary = load_recording(data, CONFIG)
            self.assertEqual(summary['gap_count'], 1)
            self.assertAlmostEqual(summary['estimated_missing_s'], 5)
            self.assertAlmostEqual(summary['gap_intervals'][0]['start_s'], 4.98)
            notice = continuity_notice(summary)
            self.assertIn('Recording quality warning', notice)
            self.assertIn('4.98–10.00', notice)
            self.assertNotIn('Stop detection signal', notice)
            summary.update(stop_gap_intervals=summary['gap_intervals'], stop_estimated_missing_s=5, stop_duration_s=15)
            self.assertIn('Stop detection signal', continuity_notice(summary))

    def test_rebuild_to_empty_then_restore_keeps_reviews_and_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / 'data'
            folder = data / 'fixture'
            folder.mkdir(parents=True)
            t = np.arange(0, 40, .02)
            frame = pd.DataFrame({'seconds_elapsed': t, 'x': 0, 'y': np.sin(t), 'z': 0})
            source = folder / 'Accelerometer.csv'
            frame.to_csv(source, index=False)
            original = source.read_bytes()
            output = root / 'results'
            output.mkdir()
            (output / 'report_data.json').write_text(json.dumps({'data_directory': str(data), 'recordings': [], 'candidates': []}))
            (output / 'manifest.json').write_text(json.dumps({'configuration': CONFIG}))
            review_dir = root / 'reviews'
            review_dir.mkdir()
            pd.DataFrame([dict(recording='fixture', time_s=10, restart_s=15, accepted=True, manual=True,
                               candidate_id='manual-fixture', note='keep this')]).to_csv(review_dir / 'stops.csv', index=False)
            store = ReviewStore(output, review_dir)
            store.rebuild()
            self.assertEqual(len(store.payload()['recordings']), 1)
            store.change_recording('fixture', 'archive')
            self.assertEqual(store.payload()['recordings'], [])
            self.assertFalse(folder.exists())
            self.assertEqual(len(store.recordings()['archived']), 1)
            # Editing another report (here empty) must retain absent-record reviews.
            store.save({})
            self.assertEqual(pd.read_csv(review_dir / 'stops.csv').iloc[0]['note'], 'keep this')
            store.change_recording('fixture', 'restore')
            self.assertEqual(len(store.payload()['recordings']), 1)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(store.read()['decisions']['manual-fixture']['decision'], 'accepted')
            self.assertEqual(store.recordings()['archived'], [])


if __name__ == '__main__':
    unittest.main()
