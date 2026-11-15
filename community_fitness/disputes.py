import sqlite3

from .errors import DomainError, require
from .storage import Store
from .validation import identifier, nonempty, utc_now


class Disputes:
    def __init__(self, store: Store):
        self.store = store

    def open(self, event_id: str, dispute_id: str, team_id: str,
             judge_id: str, reason: str) -> dict:
        identifier(dispute_id, 'dispute id')
        reason = nonempty(reason, 'reason', 1000)
        now = utc_now()
        with self.store.transaction() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['state'] in {'open', 'closed'}, 'event_not_open',
                    'Dispute requires an opened event', 409)
            self.store.one(db, 'SELECT id FROM teams WHERE event_id=? AND id=?',
                           (event_id, team_id))
            card = self.store.one(db, 'SELECT * FROM score_cards WHERE event_id=? AND team_id=? AND judge_id=?',
                                  (event_id, team_id, judge_id))
            pending = db.execute('SELECT id FROM score_disputes WHERE event_id=? AND team_id=? '
                                 'AND card_judge_id=? AND state=?',
                                 (event_id, team_id, judge_id, 'open')).fetchone()
            require(not pending, 'dispute_exists', 'Score card already has an open dispute', 409)
            try:
                db.execute('INSERT INTO score_disputes(event_id,id,team_id,card_judge_id,'
                           'card_revision,reason,state,opened_at) VALUES(?,?,?,?,?,?,?,?)',
                           (event_id, dispute_id, team_id, judge_id, card['revision'],
                            reason, 'open', now))
            except sqlite3.IntegrityError as exc:
                raise DomainError('duplicate_dispute', 'Dispute ID already exists', 409) from exc
            self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'dispute_opened', 'dispute', dispute_id,
                             {'team': team_id, 'judge': judge_id,
                              'card_revision': card['revision']}, now)
        return {'id': dispute_id, 'state': 'open', 'team_id': team_id,
                'judge_id': judge_id, 'card_revision': card['revision']}

    def resolve(self, event_id: str, dispute_id: str,
                resolution: str, note: str) -> dict:
        require(resolution in {'upheld', 'rejected'}, 'invalid_resolution',
                'Resolution must be upheld or rejected')
        note = nonempty(note, 'resolution note', 1000)
        now = utc_now()
        with self.store.transaction() as db:
            dispute = self.store.one(db, 'SELECT * FROM score_disputes WHERE event_id=? AND id=?',
                                     (event_id, dispute_id))
            require(dispute['state'] == 'open', 'dispute_closed', 'Dispute is already resolved', 409)
            card = self.store.one(db, 'SELECT revision FROM score_cards WHERE event_id=? '
                                  'AND team_id=? AND judge_id=?',
                                  (event_id, dispute['team_id'], dispute['card_judge_id']))
            require(card['revision'] == dispute['card_revision'], 'stale_dispute',
                    'Score changed after dispute opened; inspect before resolving', 409)
            db.execute('UPDATE score_disputes SET state=?,resolution=?,closed_at=? '
                       'WHERE event_id=? AND id=?',
                       (resolution, note, now, event_id, dispute_id))
            self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'dispute_resolved', 'dispute', dispute_id,
                             {'resolution': resolution, 'note': note}, now)
            return {'id': dispute_id, 'state': resolution, 'resolution': note}

    def list_disputes(self, event_id: str) -> list[dict]:
        with self.store.connection() as db:
            self.store.one(db, 'SELECT id FROM events WHERE id=?', (event_id,))
            return self.store.many(db, 'SELECT * FROM score_disputes WHERE event_id=? '
                                   'ORDER BY opened_at,id', (event_id,))

    def unresolved(self, db, event_id: str) -> list[dict]:
        return self.store.many(db, 'SELECT id,team_id,card_judge_id FROM score_disputes '
                               'WHERE event_id=? AND state=? ORDER BY id', (event_id, 'open'))
