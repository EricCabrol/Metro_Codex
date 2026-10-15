import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from metro_braking.manual_stops import review_candidates, manual_decisions_from_rows, initial_decisions_from_rows
from metro_braking.review import ReviewStore, validate_decisions
from metro_braking.runs import build_runs


class ManualStopTests(unittest.TestCase):
    def payload(self):
        return {'recordings':[{'recording':'test','start_s':0.,'end_s':100.,'signal_file':'signal.csv'}],
                'candidates':[{'id':'auto','recording':'test','start_s':20.,'end_s':30.,'boundary':'interior'}]}

    def manual(self, **changes):
        return dict(manual=True, recording='test', time_s=45., restart_s=55., note='human stop', decision='accepted', **changes)

    def test_save_reload_edit_discard_csv_and_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);output=root/'results';output.mkdir()
            (output/'report_data.json').write_text(json.dumps(self.payload()))
            pd.DataFrame({'seconds_elapsed':np.arange(0,100.05,.05),'block':0,'axis_filtered_mps2':0.}).to_csv(output/'signal.csv',index=False)
            store=ReviewStore(output,root/'reviews')
            decisions={'auto':{'decision':'accepted','time_s':20},'manual-custom':self.manual()}
            store.save(decisions)
            reloaded=ReviewStore(output,root/'reviews')
            self.assertEqual(reloaded.read()['decisions']['manual-custom']['restart_s'],55)
            self.assertEqual(reloaded.read()['unmatched_decisions'],0)
            result=reloaded.export_sections()
            self.assertEqual(result['sections'],1)
            self.assertEqual(result['trips'][0]['metrics']['next_arrival_s'],45)
            csv=pd.read_csv(root/'reviews'/'stops.csv',keep_default_na=False)
            manual=csv[csv.manual].iloc[0]
            self.assertEqual(manual.candidate_id,'manual-custom')
            initial=manual_decisions_from_rows(csv)
            self.assertEqual(initial['manual-custom']['note'],'human stop')
            regenerated=self.payload();regenerated['initial_decisions']=initial
            regenerated['candidates']=review_candidates(regenerated,initial)
            (output/'report_data.json').write_text(json.dumps(regenerated))
            decisions['manual-custom']['time_s']=46
            decisions['manual-custom']['restart_s']=56
            store.save(decisions)
            self.assertEqual(store.read()['decisions']['manual-custom']['time_s'],46)
            decisions['manual-custom']['decision']='discarded';store.save(decisions)
            self.assertEqual(store.export_sections()['sections'],0)
            self.assertTrue(store.read()['decisions']['manual-custom']['manual'])

    def test_csv_review_state_is_embedded_for_fresh_static_report(self):
        rows=pd.DataFrame([dict(recording='test',time_s=20,restart_s=30,accepted=True,manual=False,candidate_id='auto',note='old review'),
                           dict(recording='test',time_s=45,restart_s=55,accepted=True,manual=True,candidate_id='manual-custom',note='added')])
        initial=initial_decisions_from_rows(self.payload(),rows)
        self.assertEqual(initial['auto']['decision'],'accepted')
        self.assertEqual(initial['manual-custom']['restart_s'],55)
        self.assertEqual(len(review_candidates(self.payload(),initial)),2)

    def test_invalid_manual_intervals_identifiers_and_overlap_rejected(self):
        for change in [{'time_s':55},{'restart_s':101},{'time_s':float('nan')},
                       {'restart_s':True},{'recording':'other'}]:
            value=self.manual();value.update(change)
            with self.assertRaises(ValueError):
                validate_decisions(self.payload(),{'manual-invalid':value})
        with self.assertRaises(ValueError):
            validate_decisions(self.payload(),{'auto':self.manual()})
        value=self.manual();value.update(time_s=25,restart_s=40)
        with self.assertRaises(ValueError):
            validate_decisions(self.payload(),{'auto':{'decision':'accepted'},'manual-overlap':value})

    def test_manual_stop_repairs_station_assignment_after_csv_roundtrip(self):
        name='L4_Chatelet_-_Montparnasse_-_brutal-2024-03-29_18-03-15'
        times=[50.,120.,190.,260.,330.,400.]
        signal=pd.DataFrame({'seconds_elapsed':np.arange(0,501,.05),'block':0,'axis_filtered_mps2':0.})
        candidates=[dict(recording=name,start_s=t,end_s=t+15,boundary='interior') for t in times if t!=120]
        rows=pd.DataFrame([dict(recording=name,time_s=t,restart_s=t+15,accepted=True,manual=t==120,
                                candidate_id='manual-added' if t==120 else '',note='') for t in times])
        metrics,_=build_runs(name,signal,rows,candidates,{})
        self.assertEqual(len(metrics),7)
        self.assertEqual(metrics[1]['run_label'],'Cité – Saint-Michel')
        self.assertEqual(metrics[2]['run_label'],'Saint-Michel – Odéon')
        self.assertEqual(metrics[2]['restart_s'],135)
