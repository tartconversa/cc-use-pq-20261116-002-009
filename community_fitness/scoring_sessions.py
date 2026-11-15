from .errors import DomainError, require
from .storage import Store
from .validation import identifier, utc_now


class ScoringSessions:
    def __init__(self, store: Store):
        self.store = store

    def open(self, event_id: str, group_code: str, expected_event_revision: int) -> dict:
        identifier(group_code, 'group code')
        now = utc_now()
        with self.store.transaction() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['state'] == 'open', 'event_not_open', 'Event must be open for scoring', 409)
            require(event['revision'] == expected_event_revision, 'stale_revision',
                    'Event changed since scoring preparation', 409)
            group = self.store.one(db, 'SELECT * FROM groups WHERE event_id=? AND code=?',
                                   (event_id, group_code))
            existing = db.execute('SELECT * FROM score_sessions WHERE event_id=? AND group_code=?',
                                  (event_id, group_code)).fetchone()
            require(existing is None, 'session_exists', 'Group scoring session already exists', 409)
            judges = db.execute('SELECT COUNT(*) FROM judges WHERE event_id=? AND group_code=? AND active=1',
                                (event_id, group_code)).fetchone()[0]
            teams = db.execute('SELECT COUNT(*) FROM teams WHERE event_id=? AND group_code=?',
                               (event_id, group_code)).fetchone()[0]
            require(judges >= group['min_judges'] and teams > 0,
                    'session_not_ready', 'Group needs teams and enough judges')
            db.execute('INSERT INTO score_sessions VALUES(?,?,?,?,?,?)',
                       (event_id, group_code, now, None, 'open', expected_event_revision))
            revision = self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'scoring_session_opened', 'group', group_code,
                             {'judges': judges, 'teams': teams}, now)
            return {'group': group_code, 'state': 'open', 'revision': revision}

    def close(self, event_id: str, group_code: str, expected_event_revision: int) -> dict:
        now = utc_now()
        with self.store.transaction() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['revision'] == expected_event_revision, 'stale_revision',
                    'Event changed since scoring review', 409)
            require(event['state'] == 'open', 'event_not_open', 'Event scoring is closed', 409)
            session = self.store.one(db, 'SELECT * FROM score_sessions WHERE event_id=? AND group_code=?',
                                     (event_id, group_code))
            require(session['state'] == 'open', 'session_closed', 'Session is already closed', 409)
            teams = self.store.many(db, 'SELECT id FROM teams WHERE event_id=? AND group_code=?',
                                    (event_id, group_code))
            judges = self.store.many(db, 'SELECT id FROM judges WHERE event_id=? AND group_code=? AND active=1',
                                     (event_id, group_code))
            assigned = {row['id'] for row in judges}
            for team in teams:
                submitted = {row['judge_id'] for row in self.store.many(
                    db, 'SELECT judge_id FROM score_cards WHERE event_id=? AND team_id=?',
                    (event_id, team['id']))}
                require(submitted == assigned, 'incomplete_scores',
                        'Every team requires exactly one card from each active judge')
            pending = db.execute('SELECT COUNT(*) FROM score_disputes WHERE event_id=? AND state=? '
                                 'AND team_id IN (SELECT id FROM teams WHERE event_id=? AND group_code=?)',
                                 (event_id, 'open', event_id, group_code)).fetchone()[0]
            require(pending == 0, 'unresolved_dispute', 'Resolve disputes before closing session')
            db.execute('UPDATE score_sessions SET state=?,closed_at=? WHERE event_id=? AND group_code=?',
                       ('closed', now, event_id, group_code))
            revision = self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'scoring_session_closed', 'group', group_code,
                             {'teams': len(teams), 'judges': len(judges)}, now)
            return {'group': group_code, 'state': 'closed', 'revision': revision}

    def get(self, event_id: str, group_code: str) -> dict:
        with self.store.connection() as db:
            session = self.store.one(db, 'SELECT * FROM score_sessions WHERE event_id=? AND group_code=?',
                                     (event_id, group_code))
            teams = self.store.many(db, 'SELECT id,name FROM teams WHERE event_id=? AND group_code=?',
                                    (event_id, group_code))
            judges = self.store.many(db, 'SELECT id,display_name FROM judges WHERE event_id=? '
                                     'AND group_code=? AND active=1', (event_id, group_code))
            cards = self.store.many(db, 'SELECT team_id,judge_id,revision,total FROM score_cards '
                                    'WHERE event_id=? AND team_id IN '
                                    '(SELECT id FROM teams WHERE event_id=? AND group_code=?)',
                                    (event_id, event_id, group_code))
            expected = {(team['id'], judge['id']) for team in teams for judge in judges}
            submitted = {(card['team_id'], card['judge_id']) for card in cards}
            return {**session, 'teams': teams, 'judges': judges,
                    'cards': cards, 'missing_cards': [{'team_id': team, 'judge_id': judge}
                                                      for team, judge in sorted(expected - submitted)]}

    def list(self, event_id: str) -> list[dict]:
        with self.store.connection() as db:
            self.store.one(db, 'SELECT id FROM events WHERE id=?', (event_id,))
            return self.store.many(db, 'SELECT * FROM score_sessions WHERE event_id=? ORDER BY group_code',
                                   (event_id,))
