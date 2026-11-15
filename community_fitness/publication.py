import json

from .analytics import Analytics
from .attendance import Attendance
from .disputes import Disputes
from .errors import DomainError, require
from .storage import Store
from .validation import utc_now


class Publication:
    def __init__(self, store: Store):
        self.store = store
        self.analytics = Analytics(store)
        self.attendance = Attendance(store)
        self.disputes = Disputes(store)

    def preview(self, event_id: str) -> dict:
        with self.store.connection() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            scores = self.analytics._scores(db, event_id)
            services = self.analytics._services(db, event)
            unresolved = self.disputes.unresolved(db, event_id)
            attendance = self.attendance.summary(event_id, db)
            head = db.execute('SELECT MAX(version) FROM snapshots WHERE event_id=? AND status=?',
                              (event_id, 'published')).fetchone()[0] or 0
            return {'event': event, 'head_version': head, 'scores': scores,
                    'services': services, 'attendance': attendance, 'unresolved_disputes': unresolved,
                    'publishable': event['state'] == 'closed' and scores['complete'] and not unresolved}

    def publish(self, event_id: str, expected_head: int, expected_revision: int) -> dict:
        require(type(expected_head) is int and expected_head >= 0,
                'invalid_head', 'Expected head must be nonnegative')
        require(type(expected_revision) is int and expected_revision >= 0,
                'invalid_revision', 'Expected revision must be nonnegative')
        now = utc_now()
        with self.store.transaction() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            head = db.execute('SELECT MAX(version) FROM snapshots WHERE event_id=? AND status=?',
                              (event_id, 'published')).fetchone()[0] or 0
            require(head == expected_head, 'stale_head', 'Published head changed', 409)
            require(event['revision'] == expected_revision, 'stale_revision',
                    'Event source changed', 409)
            require(event['state'] == 'closed', 'event_not_closed', 'Close scoring before publication', 409)
            scores = self.analytics._scores(db, event_id)
            require(scores['complete'], 'incomplete_scores',
                    'Every team needs complete active judge cards')
            require(not self.disputes.unresolved(db, event_id), 'unresolved_dispute',
                    'Resolve score disputes before publication')
            services = self.analytics._services(db, event)
            attendance = self.attendance.summary(event_id, db)
            payload = {'version': head + 1, 'event': event, 'scores': scores,
                       'services': services, 'attendance': attendance, 'published_at': now}
            db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?)',
                       (event_id, head + 1, event['revision'], now, 'published',
                        json.dumps(payload, ensure_ascii=False, sort_keys=True)))
            self.store.audit(db, event_id, 'snapshot_published', 'snapshot', str(head + 1),
                             {'source_revision': event['revision'], 'previous': head}, now)
            return payload

    def get(self, event_id: str, version: int | None = None) -> dict:
        with self.store.connection() as db:
            self.store.one(db, 'SELECT id FROM events WHERE id=?', (event_id,))
            if version is None:
                row = db.execute('SELECT payload FROM snapshots WHERE event_id=? AND status=? '
                                 'ORDER BY version DESC LIMIT 1', (event_id, 'published')).fetchone()
            else:
                require(type(version) is int and version > 0, 'invalid_version',
                        'Version must be positive')
                row = db.execute('SELECT payload FROM snapshots WHERE event_id=? AND version=? '
                                 'AND status=?', (event_id, version, 'published')).fetchone()
            if not row:
                raise DomainError('snapshot_not_found', 'Published snapshot does not exist', 404)
            return json.loads(row['payload'])

    def versions(self, event_id: str) -> list[dict]:
        with self.store.connection() as db:
            self.store.one(db, 'SELECT id FROM events WHERE id=?', (event_id,))
            return self.store.many(db, 'SELECT version,source_revision,created_at,status '
                                   'FROM snapshots WHERE event_id=? ORDER BY version', (event_id,))

    def difference(self, event_id: str, older: int, newer: int) -> dict:
        require(older != newer, 'same_version', 'Select two different versions')
        left = self.get(event_id, older)
        right = self.get(event_id, newer)
        old_teams = {row['team_id']: row for group in left['scores']['groups']
                     for row in group['teams']}
        new_teams = {row['team_id']: row for group in right['scores']['groups']
                     for row in group['teams']}
        changes = []
        for team_id in sorted(set(old_teams) | set(new_teams)):
            old, new = old_teams.get(team_id), new_teams.get(team_id)
            if old != new:
                changes.append({'team_id': team_id, 'before': old, 'after': new})
        old_zones = {row['code']: row for row in left['services']['zones']}
        new_zones = {row['code']: row for row in right['services']['zones']}
        zone_changes = []
        for code in sorted(set(old_zones) | set(new_zones)):
            old, new = old_zones.get(code), new_zones.get(code)
            if old != new:
                zone_changes.append({'zone_code': code, 'before': old, 'after': new})
        return {'event_id': event_id, 'older': older, 'newer': newer,
                'teams': changes, 'zones': zone_changes,
                'visits_delta': right['services']['total_visits'] - left['services']['total_visits']}
