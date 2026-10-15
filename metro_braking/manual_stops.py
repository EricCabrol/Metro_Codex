"""Manual stop definitions shared by browser reviews and CSV reprocessing."""
import math
import re


def is_manual(value):
    return value is True or isinstance(value, str) and value.lower() == 'true'


def manual_candidate(key, recording, arrival, restart, start, end):
    for value in (arrival, restart):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Manual stop times must be finite numbers')
    if not start <= arrival < restart <= end:
        raise ValueError('Manual stop requires arrival < restart inside the recording')
    left, right = arrival <= start+0.1, restart >= end-0.1
    return dict(id=key, recording=recording, start_s=arrival, end_s=restart,
                duration_s=restart-arrival, manual=True, automatic=False,
                boundary='recording edge' if left or right else 'interior',
                left_censored=left, right_censored=right)


def review_candidates(payload, decisions):
    candidates = {c['id']: c for c in payload['candidates']}
    recordings = {r['recording']: r for r in payload['recordings']}
    for key, value in decisions.items():
        if not isinstance(value, dict) or value.get('manual') is not True:
            continue
        if not isinstance(key, str) or not re.fullmatch(r'manual-[a-zA-Z0-9-]{1,80}', key):
            raise ValueError('Invalid manual stop identifier')
        if key in candidates and not candidates[key].get('manual'):
            raise ValueError('Manual stops cannot replace detected candidates')
        name = value.get('recording')
        if name not in recordings:
            raise ValueError('Unknown recording for manual stop')
        r = recordings[name]
        candidates[key] = manual_candidate(key, name, value.get('time_s'), value.get('restart_s'), r['start_s'], r['end_s'])
    return list(candidates.values())


def manual_decisions_from_rows(rows):
    decisions = {}
    if rows is None or rows.empty or 'manual' not in rows:
        return decisions
    for row in rows.to_dict('records'):
        if not is_manual(row.get('manual')):
            continue
        key = row.get('candidate_id')
        if not isinstance(key, str) or not key.startswith('manual-'):
            raise ValueError('Manual CSV rows require their exported candidate_id')
        decisions[key] = dict(manual=True, recording=row['recording'], time_s=float(row['time_s']),
                              restart_s=float(row['restart_s']), note=str(row.get('note', '')),
                              decision='discarded' if str(row['accepted']).lower() in ('false','0','no') else 'accepted')
    return decisions


def initial_decisions_from_rows(payload, rows):
    """Embed CSV review state in regenerated static reports, retaining stable IDs."""
    decisions = manual_decisions_from_rows(rows)
    if rows is None or rows.empty:
        return decisions
    for row in rows.to_dict('records'):
        if is_manual(row.get('manual')):
            continue
        own = [c for c in payload['candidates'] if c['recording']==row['recording']]
        matches = [c for c in own if c['id']==row.get('candidate_id')]
        if not matches:
            matches = [c for c in own if math.isclose(c['start_s'],float(row['time_s']),abs_tol=0.051,rel_tol=0)]
        if len(matches)!=1:
            continue
        candidate = matches[0]
        restart = row.get('restart_s')
        if restart is None or restart == '':
            restart = candidate['end_s']
        decisions[candidate['id']] = dict(time_s=float(row['time_s']),restart_s=float(restart),
            decision='discarded' if str(row['accepted']).lower() in ('false','0','no') else 'accepted',note=str(row.get('note','')))
    return decisions
