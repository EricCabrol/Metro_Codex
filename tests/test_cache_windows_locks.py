"""Simulate Windows access locks; a cache failure must not lose successful analysis."""
from contextlib import redirect_stdout
import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from metro_braking.cache import RecordingCache
from metro_braking.cli import main


class WindowsCacheLockTests(unittest.TestCase):
    def build(self, target, marker='computed'):
        for relative in ['signal_00.csv','stop_signal_00.csv','plots/00.png','sections/00_001.csv']:
            path=target/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(marker)
        metric={'section':1}
        return dict(summary={'recording':'one','signal_file':'signal_00.csv'},events=[],quiet=[],sensors=[],
                    orientation_checks=[],rejected_dwells=[],series={},metrics=[metric],charts=[{'metrics':metric,'blocks':[]}],
                    sections=[{'section':1,'file':'sections/00_001.csv'}])

    def test_transient_rename_lock_is_retried_and_cache_keeps_stable_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cache=RecordingCache(root/'cache');output=root/'results'
            rename=Path.rename;attempts=[]
            def sometimes_locked(path,destination):
                if path.name=='snapshot':
                    attempts.append(path)
                    if len(attempts)<3:raise PermissionError(5,'Access denied')
                return rename(path,destination)
            with patch.object(Path,'rename',autospec=True,side_effect=sometimes_locked),patch('metro_braking.cache.time.sleep'):
                result=cache.process('one','signature',self.build,output=output,index=3)
            self.assertEqual(len(attempts),3)
            self.assertEqual(result['summary']['signal_file'],'signal_03.csv')
            cached=cache.load('one','signature')
            self.assertIsNotNone(cached)
            self.assertEqual(cached['summary']['signal_file'],'signal_00.csv')
            self.assertEqual(cached['sections'][0]['file'],'sections/00_001.csv')
            self.assertEqual((output/'sections/03_001.csv').read_text(),'computed')

    def test_permanent_rename_lock_keeps_exported_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cache=RecordingCache(root/'cache');output=root/'results'
            log=io.StringIO()
            with patch.object(Path,'rename',side_effect=PermissionError(5,'Access denied')),patch('metro_braking.cache.time.sleep'),redirect_stdout(log):
                result=cache.process('one','signature',self.build,output=output,index=2)
            self.assertEqual(result['summary']['recording'],'one')
            self.assertEqual((output/'signal_02.csv').read_text(),'computed')
            self.assertEqual((output/'sections/02_001.csv').read_text(),'computed')
            self.assertIsNone(cache.load('one','signature'))
            self.assertIn('Cache save skipped',log.getvalue())

    def test_failed_replacement_restores_previous_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cache=RecordingCache(root/'cache')
            cache.process('one','signature',lambda target:self.build(target,'old'))
            rename=Path.rename
            def locked_snapshot(path,destination):
                if path.name=='snapshot':raise PermissionError(5,'Access denied')
                return rename(path,destination)
            with patch.object(Path,'rename',autospec=True,side_effect=locked_snapshot),patch('metro_braking.cache.time.sleep'),redirect_stdout(io.StringIO()):
                cache.process('one','signature',lambda target:self.build(target,'new'),output=root/'results')
            self.assertIsNotNone(cache.load('one','signature'))
            self.assertEqual((cache.entry('one','signature')/'signal_00.csv').read_text(),'old')
            self.assertEqual((root/'results/signal_00.csv').read_text(),'new')

    def test_locked_metadata_does_not_discard_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cache=RecordingCache(root/'cache')
            write=Path.write_text
            def locked_metadata(path,*args,**kwargs):
                if path.name=='cache.json':raise PermissionError(5,'Access denied')
                return write(path,*args,**kwargs)
            with patch.object(Path,'write_text',autospec=True,side_effect=locked_metadata),redirect_stdout(io.StringIO()):
                cache.process('one','signature',self.build,output=root/'results')
            self.assertEqual((root/'results/signal_00.csv').read_text(),'computed')
            self.assertIsNone(cache.load('one','signature'))

    def test_cli_keeps_manual_reviews_when_cache_save_or_recording_processing_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'data'/'fixture';folder.mkdir(parents=True)
            output=root/'results';reviews=root/'reviews';reviews.mkdir()
            t=np.arange(0,90,.02)
            pd.DataFrame({'seconds_elapsed':t,'x':0,'y':.5*np.sin(t/5),'z':0}).to_csv(folder/'Accelerometer.csv',index=False)
            stop_file=reviews/'stops.csv'
            pd.DataFrame([dict(recording='fixture',time_s=10,restart_s=15,accepted=True,manual=True,
                               candidate_id='manual-kept',note='retain my validation')]).to_csv(stop_file,index=False)
            original=stop_file.read_bytes()
            config=Path(__file__).parents[1]/'config.json'
            args=['metro_braking','--data',str(root/'data'),'--output',str(output),'--config',str(config),'--review-dir',str(reviews)]
            rename=Path.rename
            def locked_snapshot(path,destination):
                if path.name=='snapshot':raise PermissionError(5,'Access denied')
                return rename(path,destination)
            with patch.object(sys,'argv',args),patch.object(Path,'rename',autospec=True,side_effect=locked_snapshot),patch('metro_braking.cache.time.sleep'),redirect_stdout(io.StringIO()):
                main()
            payload=json.loads((output/'report_data.json').read_text())
            self.assertEqual(len(payload['recordings']),1)
            self.assertIn('manual-kept',payload['initial_decisions'])
            self.assertEqual(json.loads((output/'manifest.json').read_text())['failures'],[])
            self.assertEqual(stop_file.read_bytes(),original)
            # A genuine analysis failure still produces a usable failure report.
            with patch.object(sys,'argv',args+['--reprocess']),patch('metro_braking.cli.analyze_recording',side_effect=ValueError('bad sensor data')),redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as exited:
                main()
            self.assertEqual(exited.exception.code,1)
            failed=json.loads((output/'report_data.json').read_text())
            self.assertEqual(failed['recordings'],[])
            self.assertEqual(failed['initial_decisions'],{})
            manifest=json.loads((output/'manifest.json').read_text())
            self.assertEqual(manifest['all_reviewed_stops'][0]['candidate_id'],'manual-kept')
            self.assertIn('bad sensor data',(output/'report.html').read_text())
            self.assertEqual(stop_file.read_bytes(),original)


if __name__=='__main__':
    unittest.main()
