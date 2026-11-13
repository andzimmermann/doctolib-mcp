"""Persistent monitoring of an explicitly chosen practice, motive and account patient.

No network is performed by create/list/stop. A separate `watch` process runs due jobs.
An active auto_book rule is a standing instruction for exactly its recorded criteria.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import date, datetime, time as wall_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .client import COUNTRY_HOSTS, DoctolibClient, DoctolibError

STATE_PATH = Path(__file__).resolve().parents[2] / '.state' / 'monitors.sqlite3'


def validate(rule: dict) -> dict:
    fields = {'country', 'profile_slug', 'profile_id', 'visit_motive_id', 'agenda_id',
              'practice_id', 'patient_id', 'date_from', 'date_to', 'time_from', 'time_to',
              'weekdays', 'timezone', 'interval_seconds', 'auto_book', 'new_patient', 'insurance_sector'}
    if set(rule) - fields:
        raise ValueError('Unknown monitor fields')
    result = dict(rule)
    required = fields - {'insurance_sector'}
    if required - set(result):
        raise ValueError('Missing monitor fields: ' + ', '.join(sorted(required - set(result))))
    if result['country'] not in COUNTRY_HOSTS:
        raise ValueError('Unsupported country')
    if not isinstance(result['profile_slug'], str) or not result['profile_slug'] or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in result['profile_slug']):
        raise ValueError('Use the exact practice profile slug, not a URL')
    for key in ('profile_id', 'visit_motive_id', 'agenda_id', 'practice_id'):
        if type(result[key]) is not int or result[key] <= 0:
            raise ValueError(key + ' must be a positive integer')
    if not isinstance(result['patient_id'], str) or not result['patient_id'].strip():
        raise ValueError('An exact patient UUID from list_patients is required')
    first, last = date.fromisoformat(result['date_from']), date.fromisoformat(result['date_to'])
    if last < first or (last - first).days >= 90:
        raise ValueError('A monitor date range must cover 1 to 90 days')
    if wall_time.fromisoformat(result['time_from']) >= wall_time.fromisoformat(result['time_to']):
        raise ValueError('time_from must precede time_to on the same day')
    if not isinstance(result['weekdays'], list) or not result['weekdays'] or any(type(x) is not int or not 0 <= x <= 6 for x in result['weekdays']):
        raise ValueError('weekdays uses Monday=0 through Sunday=6')
    ZoneInfo(result['timezone'])
    if type(result['interval_seconds']) is not int or not 300 <= result['interval_seconds'] <= 86400:
        raise ValueError('Polling interval must be 300 to 86400 seconds')
    if type(result['auto_book']) is not bool or type(result['new_patient']) is not bool:
        raise ValueError('auto_book and new_patient must be explicit booleans')
    if result.get('insurance_sector') not in (None, 'public', 'private'):
        raise ValueError('insurance_sector must be public or private')
    return result


class MonitorStore:
    def __init__(self, path: Path | None = None):
        self.path = path or STATE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.path.chmod(0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, rule TEXT NOT NULL, state TEXT NOT NULL, next_check REAL NOT NULL, result TEXT)')
        self.db.commit()

    def close(self):
        self.db.close()

    def create(self, rule: dict) -> dict:
        rule = validate(rule)
        job_id = str(uuid.uuid4())
        self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,NULL)', (job_id, json.dumps(rule), 'active', 0))
        self.db.commit()
        return {'id': job_id, 'state': 'active', 'rule': rule, 'scheduler_required': 'Run doctolib watch in a persistent process'}

    def list(self) -> list[dict]:
        return [{'id': r['id'], 'state': r['state'], 'next_check': r['next_check'], 'rule': json.loads(r['rule']),
                 'result': json.loads(r['result']) if r['result'] else None} for r in self.db.execute('SELECT * FROM jobs ORDER BY rowid')]

    def stop(self, job_id: str) -> dict:
        # A booking already dispatched cannot be retracted by stopping a monitor.
        r = self.db.execute("UPDATE jobs SET state='stopped' WHERE id=? AND state IN ('active','checking')", (job_id,))
        self.db.commit()
        return {'id': job_id, 'stopped': r.rowcount == 1, 'note': 'Stopping monitoring never cancels a booked or in-flight appointment'}

    def _finish(self, job_id: str, state: str, result: dict, next_check: float):
        self.db.execute("UPDATE jobs SET state=?,result=?,next_check=? WHERE id=? AND state IN ('checking','booking')",
                        (state, json.dumps(result), next_check, job_id))
        self.db.commit()

    def tick(self, *, now: datetime | None = None, read_client=None, book=None, patients=None) -> list[dict]:
        """Execute due jobs once. Dependency injection is for offline tests only.

        A durable 'booking' state is written BEFORE dispatch. Crashed/ambiguous bookings
        never run automatically again. Stale checking leases may retry reads after 5 min.
        """
        from . import session
        from .booking import DoctolibBookingClient
        instant = now or datetime.now(timezone.utc)
        timestamp = instant.timestamp()
        # Setting this for the whole scheduler also stops a login dialog blocking its loop.
        import os
        os.environ['DOCTOLIB_AUTO_LOGIN'] = '0'
        self.db.execute("UPDATE jobs SET state='active' WHERE state='checking' AND next_check<=?", (timestamp,))
        self.db.commit()
        ids = [r[0] for r in self.db.execute("SELECT id FROM jobs WHERE state='active' AND next_check<=?", (timestamp,))]
        output = []
        for job_id in ids:
            claimed = self.db.execute("UPDATE jobs SET state='checking',next_check=? WHERE id=? AND state='active' AND next_check<=?", (timestamp + 300, job_id, timestamp))
            self.db.commit()
            if claimed.rowcount != 1:
                continue
            rule = json.loads(self.db.execute('SELECT rule FROM jobs WHERE id=?', (job_id,)).fetchone()[0])
            try:
                local_now = instant.astimezone(ZoneInfo(rule['timezone']))
                if local_now.date() > date.fromisoformat(rule['date_to']):
                    self._finish(job_id, 'expired', {}, timestamp)
                    continue
                if patients:
                    people = patients(rule['country'])
                else:
                    people = session.authed_request('GET', '/account/master_patients.json', country=rule['country'])
                people = people if isinstance(people, list) else people.get('data', [])
                DoctolibBookingClient._pick_patient(people, None, rule['patient_id'])
                factory = read_client or (lambda country: DoctolibClient(country=country, cookie=session.require_cookie(country)))
                with factory(rule['country']) as client:
                    profile = client.practitioner(rule['profile_slug'])
                    if profile.get('profile_id') != rule['profile_id']:
                        raise ValueError('Practice profile identity changed')
                    agendas = [a for a in profile.get('agendas', []) if a.get('id') == rule['agenda_id'] and a.get('practice_id') == rule['practice_id'] and rule['visit_motive_id'] in (a.get('visit_motive_ids') or [])]
                    motives = [m for m in profile.get('visit_motives', []) if m.get('id') == rule['visit_motive_id']]
                    if not agendas or not motives or (rule['new_patient'] and motives[0].get('allow_new_patients') is False):
                        raise ValueError('Practice, agenda or visit motive no longer matches the instruction')
                    start = max(date.fromisoformat(rule['date_from']), local_now.date())
                    days = (date.fromisoformat(rule['date_to']) - start).days + 1
                    available = client.availabilities(rule['visit_motive_id'], [rule['agenda_id']], rule['practice_id'], start_date=start.isoformat(), days=days)
                eligible = []
                for day in available.get('days_with_slots', []):
                    for slot in day.get('slots', []):
                        raw = slot.get('start_date') if isinstance(slot, dict) else slot
                        when = datetime.fromisoformat(raw)
                        if when.tzinfo is None:
                            raise ValueError('Slot timestamp has no timezone')
                        local = when.astimezone(ZoneInfo(rule['timezone']))
                        if instant < when and date.fromisoformat(rule['date_from']) <= local.date() <= date.fromisoformat(rule['date_to']) and local.weekday() in rule['weekdays'] and wall_time.fromisoformat(rule['time_from']) <= local.time() < wall_time.fromisoformat(rule['time_to']):
                            eligible.append((when, raw))
                if not eligible:
                    self._finish(job_id, 'active', {'matching_slots': 0}, timestamp + rule['interval_seconds'])
                    continue
                selected = min(eligible)[1]
                if not rule['auto_book']:
                    self._finish(job_id, 'found', {'start_date': selected, 'booked': False}, timestamp)
                    output.append({'id': job_id, 'state': 'found'})
                    continue
                # Concurrent schedulers AND concurrent jobs with identical target/person cannot
                # dispatch the same logical booking. SQLite serializes this transaction.
                self.db.execute('BEGIN IMMEDIATE')
                duplicates = self.db.execute("SELECT id,rule FROM jobs WHERE id<>? AND state IN ('booking','booked','needs_review')", (job_id,)).fetchall()
                duplicate = any(all(json.loads(r['rule'])[k] == rule[k] for k in ('country','profile_id','practice_id','visit_motive_id','patient_id')) for r in duplicates)
                claim = self.db.execute("UPDATE jobs SET state='booking',result=? WHERE id=? AND state='checking'", (json.dumps({'start_date': selected}), job_id)) if not duplicate else None
                self.db.commit()
                if duplicate:
                    self._finish(job_id, 'needs_review', {'reason': 'Another job already dispatched a booking for this patient and target'}, timestamp)
                    continue
                if claim.rowcount != 1:
                    continue
                args = dict(start_date=selected, profile_id=rule['profile_id'], visit_motive_ids=[rule['visit_motive_id']], agenda_ids=[rule['agenda_id']], practice_ids=[rule['practice_id']], patient_id=rule['patient_id'], new_patient=rule['new_patient'], insurance_sector=rule.get('insurance_sector'))
                if book:
                    result = book(rule['country'], args)
                else:
                    with DoctolibBookingClient(rule['country']) as client:
                        result = client.book(**args)
                state = 'booked' if result.get('booked') is True else 'needs_review'
                self._finish(job_id, state, {'booked': result.get('booked'), 'outcome': result.get('outcome'), 'start_date': selected, 'retry_allowed': False}, timestamp)
                output.append({'id': job_id, 'state': state})
            except Exception as exc:
                # Do not record raw upstream bodies, credentials, or medical intake data.
                self._finish(job_id, 'needs_review', {'reason': type(exc).__name__, 'retry_allowed': False}, timestamp)
                output.append({'id': job_id, 'state': 'needs_review'})
        return output
