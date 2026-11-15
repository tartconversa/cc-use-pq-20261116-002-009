import json
from collections import defaultdict

from .errors import DomainError, require
from .storage import Store


class Provenance:
    def __init__(self, store: Store):
        self.store = store

    def team(self, event_id: str, team_id: str) -> dict:
        with self.store.connection() as db:
            team = self.store.one(db, 'SELECT * FROM teams WHERE event_id=? AND id=?',
                                  (event_id, team_id))
            cards = self.store.many(db, 'SELECT * FROM score_cards WHERE event_id=? AND team_id=? '
                                    'ORDER BY judge_id', (event_id, team_id))
            history = self.store.many(db, 'SELECT * FROM score_history WHERE event_id=? AND team_id=? '
                                      'ORDER BY judge_id,revision', (event_id, team_id))
            attendance = self.store.many(db, 'SELECT member_ref,gate,arrived_at FROM attendance '
                                         'WHERE event_id=? AND team_id=? ORDER BY arrived_at',
                                         (event_id, team_id))
            disputes = self.store.many(db, 'SELECT * FROM score_disputes WHERE event_id=? '
                                       'AND team_id=? ORDER BY opened_at', (event_id, team_id))
            for row in cards + history:
                row['scores'] = json.loads(row['scores'])
            by_judge = defaultdict(list)
            for row in history:
                by_judge[row['judge_id']].append(row)
            return {'team': team, 'current_cards': cards, 'history': dict(sorted(by_judge.items())),
                    'attendance': attendance, 'disputes': disputes}

    def encounter(self, event_id: str, encounter_id: str) -> dict:
        with self.store.connection() as db:
            encounter = self.store.one(db, 'SELECT * FROM encounters WHERE event_id=? AND id=?',
                                       (event_id, encounter_id))
            service = self.store.one(db, 'SELECT * FROM service_zones WHERE event_id=? AND code=?',
                                     (event_id, encounter['zone_code']))
            encounter['attributes'] = json.loads(encounter['attributes'])
            audit = self.store.many(db, 'SELECT seq,action,detail,at FROM audit WHERE event_id=? '
                                    'AND entity=? AND entity_id=? ORDER BY seq',
                                    (event_id, 'encounter', encounter_id))
            for row in audit:
                row['detail'] = json.loads(row['detail'])
            return {'encounter': encounter, 'zone': service, 'audit': audit}

    def snapshot(self, event_id: str, version: int) -> dict:
        with self.store.connection() as db:
            snapshot = self.store.one(db, 'SELECT * FROM snapshots WHERE event_id=? AND version=?',
                                      (event_id, version))
            payload = json.loads(snapshot['payload'])
            team_ids = [team['team_id'] for group in payload['scores']['groups']
                        for team in group['teams']]
            return {'event_id': event_id, 'version': version,
                    'source_revision': snapshot['source_revision'],
                    'published_at': snapshot['created_at'],
                    'team_ids': team_ids,
                    'zone_codes': [item['code'] for item in payload['services']['zones']],
                    'score_card_count': sum(team['card_count'] for group in payload['scores']['groups']
                                            for team in group['teams']),
                    'encounter_count': payload['services']['total_encounters'],
                    'current_event_revision': self.store.one(
                        db, 'SELECT revision FROM events WHERE id=?', (event_id,))['revision']}
