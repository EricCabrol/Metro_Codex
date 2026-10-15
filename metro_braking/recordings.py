"""Reversible recording archives; reviewed rows remain available for restoration."""
import json
from pathlib import Path

from .routes import display_recording_name


def available_review_rows(rows, available, kind):
    if rows.empty:
        return rows, []
    missing = sorted(set(rows.recording)-set(available))
    if missing:
        print(f'Ignoring {kind} for {len(missing)} absent recording(s): ' + ', '.join(missing), flush=True)
    return rows[rows.recording.isin(available)].copy(), missing


class RecordingArchive:
    def __init__(self, data_directory, rebuild):
        self.root = Path(data_directory).resolve()
        if not self.root.is_dir():
            raise ValueError('Recording data directory is not available')
        self.archive = self.root.parent / (self.root.name+'_archive')
        if self.archive.is_symlink():
            raise ValueError('Archive directory must not be a symbolic link')
        self.index = self.archive / '.metro-recordings.json'
        self.rebuild = rebuild

    def entries(self):
        if self.index.is_symlink():
            raise ValueError('Archive index must not be a symbolic link')
        if not self.index.exists():
            return []
        state = json.loads(self.index.read_text(encoding='utf-8'))
        if state.get('version') != 1 or not isinstance(state.get('archived'), list):
            raise ValueError('Unsupported recording archive index')
        return state['archived']

    def _write(self, entries):
        self.archive.mkdir(exist_ok=True)
        target = self.index.with_suffix('.tmp')
        target.write_text(json.dumps({'version':1,'archived':entries},ensure_ascii=False,indent=2),encoding='utf-8')
        target.replace(self.index)

    def change(self, name, action, active):
        if not isinstance(name,str) or not name or Path(name).name != name or '/' in name or '\\' in name or name in ('.','..'):
            raise ValueError('Invalid recording name')
        entries = self.entries()
        known = {entry['recording'] for entry in entries}
        original, archived = self.root/name, self.archive/name
        if original.is_symlink() or archived.is_symlink():
            raise ValueError('Recording directories must not be symbolic links')
        if action == 'archive':
            if name not in active:
                raise ValueError('Unknown active recording')
            source, target = original, archived
            updated = [entry for entry in entries if entry['recording']!=name]
            updated.append({'recording':name,'display_name':display_recording_name(name)})
        elif action == 'restore':
            if name not in known:
                raise ValueError('Unknown archived recording')
            source, target = archived, original
            updated = [entry for entry in entries if entry['recording']!=name]
        else:
            raise ValueError('Action must be archive or restore')
        if not source.is_dir():
            raise ValueError('Recording directory is missing; regenerate the report if it was moved externally')
        if target.exists():
            raise ValueError('Destination already exists; nothing was overwritten')
        self.archive.mkdir(exist_ok=True)
        source.rename(target)
        try:
            self._write(updated)
            self.rebuild()
        except Exception:
            target.rename(source)
            self._write(entries)
            raise
        return updated
