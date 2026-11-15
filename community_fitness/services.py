import json
import sqlite3
from datetime import datetime

from .errors import DomainError, require
from .storage import Store
from .validation import (identifier, instant, nonempty, normalized_attributes,
                         order, payload_digest, positive, utc_now, zone)


class Services:
    def __init__(self, store: Store):
        self.store = store

    def add_zone(self, event_id: str, code: str, label: str, kind: str,
                 opens_at: str, closes_at: str) -> dict:
        identifier(code, 'zone code')
        label = nonempty(label, 'zone label')
        require(kind in {'tea', 'tourism', 'convenience', 'industry', 'other'},
                'invalid_zone_kind', 'Unsupported service zone kind')
        order(opens_at, closes_at)
        now = utc_now()
        with self.store.transaction() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['state'] == 'draft', 'event_locked', 'Zones lock after event opening', 409)
            require(instant(event['starts_at']) <= instant(opens_at) and
                    instant(closes_at) <= instant(event['ends_at']),
                    'outside_event', 'Service hours must be inside event hours')
            try:
                db.execute('INSERT INTO service_zones VALUES(?,?,?,?,?,?)',
                           (event_id, code, label, kind, opens_at, closes_at))
            except sqlite3.IntegrityError as exc:
                raise DomainError('duplicate_zone', 'Service zone already exists', 409) from exc
            self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'zone_added', 'zone', code,
                             {'kind': kind, 'opens_at': opens_at, 'closes_at': closes_at}, now)
        return {'code': code, 'label': label, 'kind': kind,
                'opens_at': opens_at, 'closes_at': closes_at}

    def record_encounter(self, event_id: str, encounter_id: str, zone_code: str,
                         occurred_at: str, visitor_token: str, count: int,
                         attributes: dict | None = None) -> dict:
        identifier(encounter_id, 'encounter id')
        identifier(zone_code, 'zone code')
        visitor_token = nonempty(visitor_token, 'visitor token', 128)
        positive(count, 'count', 1000)
        attributes = normalized_attributes(attributes or {})
        occurrence = instant(occurred_at, 'occurred_at')
        now = utc_now()
        with self.store.transaction() as db:
            existing = db.execute('SELECT * FROM encounters WHERE event_id=? AND id=?',
                                  (event_id, encounter_id)).fetchone()
            if existing:
                prior = {'zone_code': existing['zone_code'], 'occurred_at': existing['occurred_at'],
                         'visitor_token': existing['visitor_token'], 'count': existing['count'],
                         'attributes': json.loads(existing['attributes'])}
                incoming = {'zone_code': zone_code, 'occurred_at': occurred_at,
                            'visitor_token': visitor_token, 'count': count, 'attributes': attributes}
                require(prior == incoming, 'encounter_conflict',
                        'Encounter ID was used with different contents', 409)
                return {'id': encounter_id, **prior, 'duplicate': True}
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['state'] in {'open', 'closed'}, 'event_not_open',
                    'Event has not opened', 409)
            zone_row = self.store.one(db, 'SELECT * FROM service_zones WHERE event_id=? AND code=?',
                                      (event_id, zone_code))
            require(instant(zone_row['opens_at']) <= occurrence < instant(zone_row['closes_at']),
                    'outside_zone_hours', 'Encounter lies outside zone hours')
            db.execute('INSERT INTO encounters VALUES(?,?,?,?,?,?,?,?)',
                       (event_id, encounter_id, zone_code, occurred_at, visitor_token,
                        count, json.dumps(attributes, sort_keys=True), now))
            self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'encounter_recorded', 'encounter', encounter_id,
                             {'zone': zone_code, 'count': count}, now)
        return {'id': encounter_id, 'zone_code': zone_code, 'occurred_at': occurred_at,
                'visitor_token': visitor_token, 'count': count, 'attributes': attributes,
                'duplicate': False}

    def ingest_batch(self, event_id: str, request_key: str, rows: list[dict]) -> dict:
        identifier(request_key, 'request key')
        require(isinstance(rows, list) and 1 <= len(rows) <= 500,
                'invalid_batch', 'Batch must contain 1-500 encounter rows')
        digest = payload_digest(rows)
        now = utc_now()
        with self.store.transaction() as db:
            previous = db.execute('SELECT * FROM ingest_requests WHERE event_id=? AND request_key=?',
                                  (event_id, request_key)).fetchone()
            if previous:
                require(previous['kind'] == 'encounters' and previous['payload_hash'] == digest,
                        'request_conflict', 'Request key was used for another batch', 409)
                result = json.loads(previous['result'])
                result['replayed'] = True
                return result
            self.store.one(db, 'SELECT id FROM events WHERE id=?', (event_id,))
            accepted = []
            rejected = []
            for index, row in enumerate(rows):
                db.execute('SAVEPOINT encounter_row')
                try:
                    require(isinstance(row, dict), 'invalid_row', 'Encounter row must be an object')
                    result = self.record_encounter_in_transaction(db, event_id, row, now)
                    accepted.append({'index': index, **result})
                    db.execute('RELEASE SAVEPOINT encounter_row')
                except (DomainError, sqlite3.IntegrityError) as exc:
                    db.execute('ROLLBACK TO SAVEPOINT encounter_row')
                    db.execute('RELEASE SAVEPOINT encounter_row')
                    code = exc.code if isinstance(exc, DomainError) else 'integrity_error'
                    rejected.append({'index': index, 'code': code, 'message': str(exc)})
            result = {'accepted': accepted, 'rejected': rejected, 'replayed': False}
            db.execute('INSERT INTO ingest_requests VALUES(?,?,?,?,?,?)',
                       (event_id, request_key, 'encounters', digest,
                        json.dumps(result, ensure_ascii=False), now))
            self.store.audit(db, event_id, 'encounter_batch', 'request', request_key,
                             {'accepted': len(accepted), 'rejected': len(rejected)}, now)
            return result

    def record_encounter_in_transaction(self, db, event_id: str, row: dict, now: str) -> dict:
        encounter_id = identifier(row.get('id'), 'encounter id')
        zone_code = identifier(row.get('zone_code'), 'zone code')
        occurred_at = row.get('occurred_at')
        occurrence = instant(occurred_at, 'occurred_at')
        token = nonempty(row.get('visitor_token'), 'visitor token', 128)
        count = positive(row.get('count'), 'count', 1000)
        attrs = normalized_attributes(row.get('attributes') or {})
        existing = db.execute('SELECT * FROM encounters WHERE event_id=? AND id=?',
                              (event_id, encounter_id)).fetchone()
        if existing:
            require((existing['zone_code'], existing['occurred_at'], existing['visitor_token'],
                     existing['count'], json.loads(existing['attributes'])) ==
                    (zone_code, occurred_at, token, count, attrs), 'encounter_conflict',
                    'Encounter ID was used with different contents', 409)
            return {'id': encounter_id, 'duplicate': True}
        event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
        require(event['state'] in {'open', 'closed'}, 'event_not_open',
                'Event has not opened', 409)
        service = self.store.one(db, 'SELECT * FROM service_zones WHERE event_id=? AND code=?',
                                 (event_id, zone_code))
        require(instant(service['opens_at']) <= occurrence < instant(service['closes_at']),
                'outside_zone_hours', 'Encounter lies outside zone hours')
        db.execute('INSERT INTO encounters VALUES(?,?,?,?,?,?,?,?)',
                   (event_id, encounter_id, zone_code, occurred_at, token, count,
                    json.dumps(attrs, sort_keys=True), now))
        self.store.bump(db, event_id)
        self.store.audit(db, event_id, 'encounter_recorded', 'encounter', encounter_id,
                         {'zone': zone_code, 'count': count}, now)
        return {'id': encounter_id, 'duplicate': False}

    def encounters(self, event_id: str, zone_code: str | None = None,
                   after: str | None = None, limit: int = 100) -> list[dict]:
        require(type(limit) is int and 1 <= limit <= 500, 'invalid_limit', 'Limit must be 1-500')
        with self.store.connection() as db:
            self.store.one(db, 'SELECT id FROM events WHERE id=?', (event_id,))
            query = 'SELECT * FROM encounters WHERE event_id=?'
            values: list = [event_id]
            if zone_code:
                query += ' AND zone_code=?'
                values.append(zone_code)
            if after:
                instant(after, 'after')
                query += ' AND recorded_at>?'
                values.append(after)
            query += ' ORDER BY recorded_at,id LIMIT ?'
            values.append(limit)
            rows = self.store.many(db, query, tuple(values))
            for row in rows:
                row['attributes'] = json.loads(row['attributes'])
            return rows
