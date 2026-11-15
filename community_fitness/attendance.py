import sqlite3
from collections import defaultdict

from .errors import DomainError, require
from .storage import Store
from .validation import identifier, instant, nonempty, utc_now


class Attendance:
    def __init__(self, store: Store):
        self.store = store

    def check_in(self, event_id: str, team_id: str, member_ref: str,
                 gate: str, arrived_at: str, request_key: str) -> dict:
        identifier(team_id, 'team id')
        identifier(member_ref, 'member reference')
        identifier(request_key, 'request key')
        gate = nonempty(gate, 'gate', 80)
        arrival = instant(arrived_at, 'arrived_at')
        now = utc_now()
        with self.store.transaction() as db:
            previous = db.execute('SELECT * FROM attendance WHERE event_id=? AND request_key=?',
                                  (event_id, request_key)).fetchone()
            if previous:
                require((previous['team_id'], previous['member_ref'], previous['gate'],
                         previous['arrived_at']) == (team_id, member_ref, gate, arrived_at),
                        'request_conflict', 'Check-in request key has different contents', 409)
                return {'team_id': team_id, 'member_ref': member_ref, 'gate': gate,
                        'arrived_at': arrived_at, 'duplicate': True}
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['state'] in {'open', 'closed'}, 'event_not_open',
                    'Attendance requires an opened event', 409)
            self.store.one(db, 'SELECT id FROM teams WHERE event_id=? AND id=?',
                           (event_id, team_id))
            require(instant(event['starts_at']) <= arrival < instant(event['ends_at']),
                    'outside_event', 'Arrival lies outside event hours')
            current = db.execute('SELECT COUNT(*) FROM attendance WHERE event_id=? AND team_id=?',
                                 (event_id, team_id)).fetchone()[0]
            team = self.store.one(db, 'SELECT member_count FROM teams WHERE event_id=? AND id=?',
                                  (event_id, team_id))
            require(current < team['member_count'], 'attendance_capacity',
                    'Team check-in capacity reached', 409)
            try:
                db.execute('INSERT INTO attendance VALUES(?,?,?,?,?,?)',
                           (event_id, team_id, member_ref, gate, arrived_at, request_key))
            except sqlite3.IntegrityError as exc:
                raise DomainError('member_already_checked_in',
                                  'Member reference already checked in for team', 409) from exc
            self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'member_checked_in', 'team', team_id,
                             {'member_ref': member_ref, 'gate': gate}, now)
            return {'team_id': team_id, 'member_ref': member_ref, 'gate': gate,
                    'arrived_at': arrived_at, 'duplicate': False}

    def team(self, event_id: str, team_id: str) -> dict:
        with self.store.connection() as db:
            team = self.store.one(db, 'SELECT * FROM teams WHERE event_id=? AND id=?',
                                  (event_id, team_id))
            records = self.store.many(db, 'SELECT member_ref,gate,arrived_at FROM attendance '
                                      'WHERE event_id=? AND team_id=? ORDER BY arrived_at,member_ref',
                                      (event_id, team_id))
            return {'team_id': team_id, 'registered_members': team['member_count'],
                    'arrived_members': len(records), 'remaining': team['member_count'] - len(records),
                    'records': records}

    def summary(self, event_id: str, db=None) -> dict:
        if db is not None:
            return self._summary(db, event_id)
        with self.store.connection() as db:
            return self._summary(db, event_id)

    def _summary(self, db, event_id: str) -> dict:
            self.store.one(db, 'SELECT id FROM events WHERE id=?', (event_id,))
            teams = self.store.many(db, 'SELECT id,group_code,member_count FROM teams WHERE event_id=?',
                                    (event_id,))
            arrivals = self.store.many(db, 'SELECT team_id,gate,arrived_at FROM attendance WHERE event_id=?',
                                       (event_id,))
            by_team = defaultdict(int)
            by_gate = defaultdict(int)
            for arrival in arrivals:
                by_team[arrival['team_id']] += 1
                by_gate[arrival['gate']] += 1
            return {'registered_members': sum(row['member_count'] for row in teams),
                    'arrived_members': len(arrivals), 'by_gate': dict(sorted(by_gate.items())),
                    'teams': [{'team_id': team['id'], 'group': team['group_code'],
                               'registered': team['member_count'], 'arrived': by_team[team['id']]}
                              for team in sorted(teams, key=lambda row: row['id'])]}
