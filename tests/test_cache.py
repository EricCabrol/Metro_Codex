"""Incremental runs must preserve reviews, exact signals and cross-recording statistics."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from metro_braking.cache import RecordingCache, recording_signature, source_files
from metro_braking.cli import main
from metro_braking.processing import analyze_recording

CONFIG = json.loads((Path(__file__).parents[1]/'config.json').read_text())


class CacheTests(unittest.TestCase):
    def test_fingerprints_are_local_to_recording_and_content_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'one';folder.mkdir()
            file=folder/'sensor.csv';file.write_text('value=1')
            stamp=file.stat()
            reviews=pd.DataFrame([{'recording':'one','note':'kept'}, {'recording':'two','note':'other'}])
            stops=pd.DataFrame([{'recording':'one','time_s':1,'accepted':True}])
            config=dict(CONFIG, recordings={'one':{'polarity':1},'two':{'polarity':-1}})
            key=lambda:recording_signature('one',source_files(folder),config,reviews,stops,'algorithm1')
            baseline=key()
            reviews.loc[1,'note']='changed elsewhere'
            config['recordings']['two']['polarity']=1
            self.assertEqual(key(),baseline)
            stops['time_s']=stops.time_s.astype(float)
            self.assertEqual(key(),baseline)
            reviews.loc[0,'note']='changed here'
            self.assertNotEqual(key(),baseline)
            reviews.loc[0,'note']='kept'
            file.write_text('value=2');os.utime(file, ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            self.assertNotEqual(key(),baseline)
            file.write_text('value=1')
            self.assertEqual(key(),baseline)
            config['cutoff_hz']=.25
            self.assertNotEqual(key(),baseline)
            self.assertNotEqual(recording_signature('one',source_files(folder),CONFIG,reviews,stops,'algorithm2'), baseline)

    def test_failed_analysis_never_publishes_a_cache_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            cache=RecordingCache(Path(temp))
            def broken(target):
                (target/'signal_00.csv').write_text('partial output')
                raise ValueError('broken recording')
            with self.assertRaisesRegex(ValueError,'broken recording'):
                cache.process('one','signature',broken)
            self.assertIsNone(cache.load('one','signature'))
            self.assertEqual(list(Path(temp).iterdir()),[])

    def test_cli_reuses_old_recordings_when_adding_and_reindexes_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/'data';data.mkdir()
            output=root/'results';reviews=root/'reviews';reviews.mkdir()
            config=root/'config.json';config.write_text(json.dumps(CONFIG))
            stops=[]
            def add(month, amplitude):
                name=f'L13_Montparnasse_-_Pernety-2024-{month:02d}-01_18-00-00'
                folder=data/name;folder.mkdir()
                t=np.arange(0,90,.02)
                pd.DataFrame({'seconds_elapsed':t,'x':0,'y':amplitude*np.sin(t/5),'z':0}).to_csv(folder/'Accelerometer.csv',index=False)
                identity=hashlib.sha256(name.encode()).hexdigest()[:10]
                for index,(arrival,restart) in enumerate([(0,3),(40,45),(80,89.9)]):
                    stops.append(dict(recording=name,time_s=arrival,restart_s=restart,accepted=True,manual=True,
                                      candidate_id=f'manual-{identity}-{index}',note='validated station'))
                pd.DataFrame(stops).to_csv(reviews/'stops.csv',index=False)
                return name
            names=[add(2,.5),add(3,.8)]
            args=['metro_braking','--data',str(data),'--output',str(output),'--config',str(config),'--review-dir',str(reviews)]
            def run(extra=()):
                with patch.object(sys,'argv',args+list(extra)), patch('metro_braking.cli.analyze_recording',wraps=analyze_recording) as analyze, redirect_stdout(io.StringIO()):
                    main()
                return analyze.call_count,json.loads((output/'manifest.json').read_text())['processing']
            self.assertEqual(run()[0],2)
            initial=json.loads((output/'report_data.json').read_text())
            signal_hashes={r['recording']:hashlib.sha256((output/r['signal_file']).read_bytes()).hexdigest() for r in initial['recordings']}
            first_metrics=(output/'trip_acceleration.csv').read_bytes()
            first_reviews=(reviews/'stops.csv').read_bytes()
            count,state=run()
            self.assertEqual(count,0);self.assertEqual(set(state['reused']),set(names))
            self.assertEqual((output/'trip_acceleration.csv').read_bytes(),first_metrics)
            self.assertEqual((reviews/'stops.csv').read_bytes(),first_reviews)
            # A new name sorts before the old ones and must not shift their signal data.
            new=add(1,.3)
            count,state=run();self.assertEqual(count,1);self.assertEqual(state['processed'],[new])
            payload=json.loads((output/'report_data.json').read_text())
            for recording in payload['recordings']:
                if recording['recording'] in signal_hashes:
                    self.assertEqual(hashlib.sha256((output/recording['signal_file']).read_bytes()).hexdigest(),signal_hashes[recording['recording']])
            self.assertTrue(all(t['metrics']['duration_sample_count']==3 for t in payload['trips']))
            self.assertEqual(len(payload['initial_decisions']),9)
            # Only the recording whose source changed is recomputed.
            source=data/names[0]/'Accelerometer.csv'
            frame=pd.read_csv(source);frame.loc[10,'y']+=.02;frame.to_csv(source,index=False)
            count,state=run();self.assertEqual(count,1);self.assertEqual(state['processed'],[names[0]])
            # A review edit is local as well; all duration summaries must still refresh.
            stops[0]['restart_s']=4
            pd.DataFrame(stops).to_csv(reviews/'stops.csv',index=False)
            count,state=run();self.assertEqual(count,1);self.assertEqual(state['processed'],[names[0]])
            refreshed=json.loads((output/'report_data.json').read_text())
            for trip in refreshed['trips']:
                if trip['metrics']['section']==1:
                    self.assertAlmostEqual(trip['metrics']['duration_mean_s'],(36+37+37)/3)
            # A missing/corrupt cached artifact triggers safe recomputation.
            cache=RecordingCache(output/'.recording_cache')
            entries=[p for p in cache.directory.iterdir() if p.is_dir() and json.loads((p/'cache.json').read_text())['recording']==names[1]]
            (entries[0]/'signal_00.csv').write_text('corrupt cache')
            count,state=run();self.assertEqual(count,1);self.assertEqual(state['processed'],[names[1]])
            self.assertEqual(run(['--reprocess'])[0],3)


if __name__=='__main__':
    unittest.main()
