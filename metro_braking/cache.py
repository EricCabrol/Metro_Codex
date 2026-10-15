"""Content-checked, per-recording processing snapshots (JSON and portable artifacts)."""
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

import matplotlib
import numpy as np
import pandas as pd
import scipy

from .review import candidate_id
from .trip_acceleration import window_status, trip_acceleration, trip_chart, resolve_restart, arrival_deceleration


CACHE_VERSION = 1


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def source_files(folder):
    return [{'file': path.name, 'bytes': path.stat().st_size, 'sha256': file_digest(path)}
            for path in sorted(folder.iterdir()) if path.is_file()]


def processing_version():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ('processing.py', 'analysis.py', 'routes.py', 'routes.json', 'runs.py', 'manual_stops.py'):
        digest.update(name.encode())
        digest.update((root/name).read_bytes())
    for function in (candidate_id, window_status, trip_acceleration, trip_chart, resolve_restart, arrival_deceleration):
        digest.update(inspect.getsource(function).encode())
    versions = [sys.version_info[:2], sys.platform, np.__version__, pd.__version__, scipy.__version__, matplotlib.__version__]
    digest.update(json.dumps(versions).encode())
    return digest.hexdigest()


def recording_signature(name, files, config, reviews, stops, version):
    own_config = {k:v for k,v in config.items() if k!='recordings'}
    own_config['recordings'] = {name:config.get('recordings', {}).get(name, {})}
    def own_rows(frame):
        rows = frame.loc[frame.recording==name].to_dict('records') if not frame.empty else []
        # Adding a fractional timestamp to another recording may change a whole
        # CSV column from integers to floats; that must not invalidate old rows.
        numeric = {'time_s', 'restart_s', 'start_s', 'end_s', 'sign'}
        for row in rows:
            for field in numeric & row.keys():
                if row[field] is not None and row[field] != '':
                    row[field] = float(row[field])
        return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
    inputs = dict(schema=CACHE_VERSION, recording=name, files=files, configuration=own_config,
                  event_reviews=own_rows(reviews), stops=own_rows(stops), processing_version=version)
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=lambda value:value.item()).encode()).hexdigest()


def retry_file_access(operation):
    """Allow bounded retries for transient Windows sharing/access locks."""
    delays = (.05, .1, .2, .4, .8)
    for attempt in range(len(delays)+1):
        try:
            return operation()
        except OSError as error:
            transient = isinstance(error, PermissionError) or getattr(error, 'winerror', None) in (5, 32, 33)
            if not transient or attempt == len(delays):
                raise
            time.sleep(delays[attempt])


class RecordingCache:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def entry(self, name, signature):
        identity = hashlib.sha256(name.encode()).hexdigest()[:20]
        return self.directory / f'{identity}-{signature}'

    def load(self, name, signature):
        entry = self.entry(name, signature)
        try:
            state = json.loads((entry/'cache.json').read_text(encoding='utf-8'))
            if state['schema']!=CACHE_VERSION or state['signature']!=signature or state['recording']!=name:
                return None
            required = {'result.json', 'signal_00.csv', 'stop_signal_00.csv', 'plots/00.png'}
            if not required.issubset(state['artifacts']):
                return None
            for relative, expected in state['artifacts'].items():
                path = (entry/relative).resolve()
                if not path.is_relative_to(entry) or not path.is_file() or file_digest(path)!=expected:
                    return None
            result = json.loads((entry/'result.json').read_text(encoding='utf-8'))
            if result['summary']['recording']!=name:
                return None
            for field in ('events', 'quiet', 'sensors', 'orientation_checks', 'rejected_dwells', 'metrics', 'charts', 'sections'):
                if not isinstance(result[field], list):
                    return None
            for section in result['sections']:
                if (not isinstance(section['section'], int) or section['section']<0 or
                    section['file']!=f"sections/00_{section['section']:03d}.csv" or section['file'] not in state['artifacts']):
                    return None
            # Restore shared metric objects before aggregate duration statistics update them.
            metrics = {row['section']:row for row in result['metrics']}
            for chart in result['charts']:
                chart['metrics'] = metrics[chart['metrics']['section']]
            return result
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def process(self, name, signature, build, output=None, index=0):
        """Export successful analysis before publishing its optional cache snapshot."""
        entry = self.entry(name, signature)
        with tempfile.TemporaryDirectory(prefix='building-', dir=self.directory, ignore_cleanup_errors=True) as temporary:
            staged = Path(temporary)/'snapshot'
            staged.mkdir()
            result = build(staged)
            persistence_error = None
            try:
                (staged/'result.json').write_text(json.dumps(result, allow_nan=False, default=lambda value:value.item()), encoding='utf-8')
                artifacts = {str(path.relative_to(staged).as_posix()):file_digest(path)
                             for path in staged.rglob('*') if path.is_file()}
                (staged/'cache.json').write_text(json.dumps(dict(schema=CACHE_VERSION, recording=name,
                    signature=signature, artifacts=artifacts), indent=2), encoding='utf-8')
            except OSError as error:
                persistence_error = error
            # The report must not depend on a successful directory rename. The
            # serialized snapshot retains index 00; only the exported in-memory
            # result gets remapped to its actual position in the current report.
            if output is not None:
                self.materialize(name, signature, result, output, index, source=staged)
            if persistence_error is None:
                previous = Path(temporary)/'previous'
                try:
                    if entry.exists():
                        retry_file_access(lambda: entry.rename(previous))
                    try:
                        retry_file_access(lambda: staged.rename(entry))
                    except OSError:
                        if previous.exists():
                            retry_file_access(lambda: previous.rename(entry))
                        raise
                except OSError as error:
                    persistence_error = error
            if persistence_error is not None:
                if output is None:
                    raise persistence_error
                print(f'Cache save skipped for {name}: {persistence_error}. '
                      'Analysis results were kept; this recording may be recomputed next time.', flush=True)
            return result

    def materialize(self, name, signature, result, output, index, source=None):
        entry = source if source is not None else self.entry(name, signature)
        for relative, destination in [('plots/00.png', f'plots/{index:02d}.png'),
                                    ('signal_00.csv', f'signal_{index:02d}.csv'),
                                    ('stop_signal_00.csv', f'stop_signal_{index:02d}.csv')]:
            target = output/destination
            target.parent.mkdir(parents=True, exist_ok=True)
            retry_file_access(lambda: shutil.copyfile(entry/relative, target))
        result['summary']['signal_file'] = f'signal_{index:02d}.csv'
        for section in result['sections']:
            target = f"sections/{index:02d}_{section['section']:03d}.csv"
            (output/'sections').mkdir(exist_ok=True)
            retry_file_access(lambda: shutil.copyfile(entry/section['file'], output/target))
            section['file'] = target
