"""Refresh the boxplot-only Pages snapshot from a generated reviewed report."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from metro_braking.global_statistics import write_global_statistics


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
    # No raw sensor samples, recording identities or individual run metrics are published.
    (args.output/'snapshot.json').write_text(json.dumps(dict(recordings=len(payload['recordings']),runs=len(rows),
        departure_window_seconds=departure,restart_offset_seconds=offset,arrival_window_seconds=arrival),indent=2)+'\n',encoding='utf-8')
    print(f'Updated boxplot-only snapshot: {args.output.resolve()}')


if __name__=='__main__':
    main()
