"""Refresh the Pages statistics and filtered run-comparison snapshot."""
import argparse
import json
from collections import Counter
import hashlib
import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from metro_braking.global_statistics import write_global_statistics


def comparison_trips(payload, metrics, report):
    """Use the latest exported metrics and publish only repeated complete runs."""
    import pandas as pd
    rows = [row for row in metrics.to_dict('records')
            if str(row.get('duration_eligible')).lower() in ('true','1') and row.get('coverage')=='complete'
            and row.get('line') not in ('',None) and row.get('origin_station') and row.get('destination_station')]
    key = lambda row: (str(row['line']),row['origin_station'],row['destination_station'])
    counts = Counter(key(row) for row in rows)
    recordings = {r['recording']:r for r in payload['recordings']}
    signals, trips = {}, []
    for row in rows:
        if counts[key(row)]<2:
            continue
        name = row['recording']
        if name not in signals:
            signal_file = (report/recordings[name]['signal_file']).resolve()
            if not signal_file.is_relative_to(report.resolve()):
                raise ValueError('Filtered signal must be inside the report directory')
            signals[name] = pd.read_csv(signal_file,float_precision='round_trip')
        signal = signals[name]
        restart, end = float(row['restart_s']), float(row['end_s'])
        section = signal[(signal.seconds_elapsed>=restart)&(signal.seconds_elapsed<=end)]
        blocks = [[[float(t-restart),float(a)] for t,a in zip(block.seconds_elapsed,block.axis_filtered_mps2)]
                  for _,block in section.groupby('block',sort=False)]
        identity = hashlib.sha256(f"{name}|{row['section']}".encode()).hexdigest()[:16]
        metrics_out = dict(recording='run-'+identity,display_recording=row.get('display_recording') or recordings[name].get('display_name',name),
            line=str(row['line']),origin_station=row['origin_station'],destination_station=row['destination_station'],
            start_s=0,restart_s=0,end_s=end-restart,coverage='complete',duration_eligible=True)
        trips.append(dict(metrics=metrics_out,blocks=blocks))
    return trips


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,default=Path('results_reviewed'))
    parser.add_argument('--output',type=Path,default=Path('demo'))
    args=parser.parse_args()
    payload=json.loads((args.report/'report_data.json').read_text(encoding='utf-8'))
    # An export may contain newer reviewed boundaries than the embedded chart data.
    import pandas as pd
    metrics_path=args.report/'trip_acceleration.csv'
    metrics=pd.read_csv(metrics_path,keep_default_na=False,float_precision='round_trip')
    for field in ('max_deceleration_mps2','max_arrival_deceleration_mps2'):
        metrics[field]=metrics[field].map(lambda value: float(value) if value != '' else float('nan'))
    rows=metrics.to_dict('records')
    args.output.mkdir(parents=True,exist_ok=True)
    departure=payload.get('trip_window_seconds',30)
    offset=payload.get('trip_restart_offset_seconds',.5)
    arrival=payload.get('arrival_window_seconds',20)
    write_global_statistics(args.output,rows,departure,offset,arrival,[r.get('line') for r in payload['recordings']])
    trips=comparison_trips(payload,metrics,args.report)
    (args.output/'comparisons.json').write_text(json.dumps(dict(version=1,trips=trips),ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n',encoding='utf-8')
    for name in ('run_comparison.js','run_comparison.css'):
        shutil.copyfile(Path(__file__).resolve().parents[1]/'metro_braking'/name,args.output/name)
    # Only filtered run-relative samples and display labels are published; raw
    # sensor streams, full recording traces and review decisions stay local.
    (args.output/'snapshot.json').write_text(json.dumps(dict(recordings=len(payload['recordings']),runs=len(rows),
        departure_window_seconds=departure,restart_offset_seconds=offset,arrival_window_seconds=arrival,
        comparison_runs=len(trips),comparison_interstations=len({(t['metrics']['line'],t['metrics']['origin_station'],t['metrics']['destination_station']) for t in trips})),indent=2)+'\n',encoding='utf-8')
    print(f'Updated statistics and run-comparison snapshot: {args.output.resolve()}')


if __name__=='__main__':
    main()
