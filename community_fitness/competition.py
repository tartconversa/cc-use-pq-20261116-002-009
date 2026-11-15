import json
import sqlite3
from datetime import datetime
from decimal import Decimal

from .errors import DomainError, require
from .storage import Store
from .validation import (decimal_score, identifier, instant, nonempty, order,
                         payload_digest, positive, rubric, utc_now, zone)


class Competition:
    def __init__(self, store: Store):
        self.store = store

    def create_event(self, event_id: str, name: str, starts_at: str, ends_at: str,
                     timezone: str) -> dict:
        identifier(event_id)
        name = nonempty(name, 'name')
        order(starts_at, ends_at)
        zone(timezone)
        now = utc_now()
        try:
            with self.store.transaction() as db:
                db.execute('INSERT INTO events(id,name,starts_at,ends_at,timezone,state,created_at) '
                           'VALUES(?,?,?,?,?,?,?)', (event_id, name, starts_at, ends_at,
                                                    timezone, 'draft', now))
                self.store.audit(db, event_id, 'event_created', 'event', event_id,
                                 {'name': name}, now)
        except sqlite3.IntegrityError as exc:
            raise DomainError('duplicate_event', 'Event ID already exists', 409) from exc
        return self.store.inspect(event_id)

    def add_group(self, event_id: str, code: str, label: str, criteria: dict,
                  min_judges: int, max_judges: int) -> dict:
        identifier(code, 'group code')
        label = nonempty(label, 'group label')
        criteria = rubric(criteria)
        positive(min_judges, 'min_judges', 30)
        positive(max_judges, 'max_judges', 30)
        require(min_judges <= max_judges, 'invalid_judge_range', 'Minimum exceeds maximum')
        now = utc_now()
        with self.store.transaction() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['state'] == 'draft', 'event_locked', 'Groups lock after opening', 409)
            try:
                db.execute('INSERT INTO groups VALUES(?,?,?,?,?,?)',
                           (event_id, code, label, json.dumps(criteria, sort_keys=True),
                            min_judges, max_judges))
            except sqlite3.IntegrityError as exc:
                raise DomainError('duplicate_group', 'Group already exists', 409) from exc
            self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'group_added', 'group', code,
                             {'criteria': criteria, 'min_judges': min_judges,
                              'max_judges': max_judges}, now)
        return {'code': code, 'label': label, 'criteria': criteria,
                'min_judges': min_judges, 'max_judges': max_judges}

    def register_team(self, event_id: str, team_id: str, group_code: str,
                      name: str, district: str, member_count: int) -> dict:
        identifier(team_id, 'team id')
        identifier(group_code, 'group code')
        name = nonempty(name, 'team name')
        district = nonempty(district, 'district', 80)
        positive(member_count, 'member_count', 200)
        now = utc_now()
        with self.store.transaction() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['state'] == 'draft', 'event_locked', 'Registration closes on opening', 409)
            self.store.one(db, 'SELECT code FROM groups WHERE event_id=? AND code=?',
                           (event_id, group_code))
            try:
                db.execute('INSERT INTO teams(event_id,id,group_code,name,district,member_count,'
                           'registered_at) VALUES(?,?,?,?,?,?,?)',
                           (event_id, team_id, group_code, name, district, member_count, now))
            except sqlite3.IntegrityError as exc:
                raise DomainError('duplicate_team', 'Team ID or group name already exists', 409) from exc
            self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'team_registered', 'team', team_id,
                             {'group': group_code, 'members': member_count}, now)
        return {'id': team_id, 'group': group_code, 'name': name, 'district': district,
                'member_count': member_count}

    def assign_judge(self, event_id: str, judge_id: str, name: str,
                     group_code: str) -> dict:
        identifier(judge_id, 'judge id')
        identifier(group_code, 'group code')
        name = nonempty(name, 'judge name')
        now = utc_now()
        with self.store.transaction() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['state'] in {'draft', 'open'}, 'event_locked',
                    'Judges cannot change after closing', 409)
            group = self.store.one(db, 'SELECT * FROM groups WHERE event_id=? AND code=?',
                                   (event_id, group_code))
            count = db.execute('SELECT COUNT(*) FROM judges WHERE event_id=? AND group_code=? AND active=1',
                               (event_id, group_code)).fetchone()[0]
            require(count < group['max_judges'], 'judge_limit', 'Group judge limit reached', 409)
            try:
                db.execute('INSERT INTO judges(event_id,id,display_name,group_code) VALUES(?,?,?,?)',
                           (event_id, judge_id, name, group_code))
            except sqlite3.IntegrityError as exc:
                raise DomainError('duplicate_judge', 'Judge ID already exists', 409) from exc
            self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'judge_assigned', 'judge', judge_id,
                             {'group': group_code}, now)
        return {'id': judge_id, 'name': name, 'group': group_code}

    def change_state(self, event_id: str, target: str, expected_revision: int) -> dict:
        transitions = {'draft': 'open', 'open': 'closed'}
        now = utc_now()
        with self.store.transaction() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['revision'] == expected_revision, 'stale_revision',
                    'Event changed since it was inspected', 409)
            require(transitions.get(event['state']) == target, 'invalid_transition',
                    'Event transition is not permitted', 409)
            if target == 'open':
                groups = self.store.many(db, 'SELECT * FROM groups WHERE event_id=?', (event_id,))
                require(bool(groups), 'missing_group', 'At least one group is required')
                for group in groups:
                    teams = db.execute('SELECT COUNT(*) FROM teams WHERE event_id=? AND group_code=?',
                                       (event_id, group['code'])).fetchone()[0]
                    require(teams > 0, 'missing_team', 'Every group needs a team')
            if target == 'closed':
                sessions = self.store.many(db, 'SELECT group_code,state FROM score_sessions WHERE event_id=?',
                                           (event_id,))
                groups = self.store.many(db, 'SELECT code FROM groups WHERE event_id=?', (event_id,))
                require({row['group_code'] for row in sessions if row['state'] == 'closed'} ==
                        {row['code'] for row in groups}, 'sessions_not_closed',
                        'Close all group scoring sessions before event closing', 409)
            db.execute('UPDATE events SET state=? WHERE id=?', (target, event_id))
            revision = self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'state_changed', 'event', event_id,
                             {'from': event['state'], 'to': target}, now)
        return {'state': target, 'revision': revision}

    def submit_score(self, event_id: str, team_id: str, judge_id: str,
                     scores: dict, request_key: str, expected_revision: int | None = None) -> dict:
        identifier(request_key, 'request key')
        require(isinstance(scores, dict), 'invalid_score', 'Scores must be an object')
        now = utc_now()
        digest = payload_digest({'team_id': team_id, 'judge_id': judge_id,
                                 'scores': scores, 'expected_revision': expected_revision})
        with self.store.transaction() as db:
            existing_request = db.execute('SELECT * FROM score_requests WHERE event_id=? AND request_key=?',
                                          (event_id, request_key)).fetchone()
            if existing_request:
                require(existing_request['payload_hash'] == digest, 'request_conflict',
                        'Request key was used for different score content', 409)
                return json.loads(existing_request['result'])
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            require(event['state'] == 'open', 'event_locked', 'Scoring requires open event', 409)
            team = self.store.one(db, 'SELECT * FROM teams WHERE event_id=? AND id=?',
                                  (event_id, team_id))
            judge = self.store.one(db, 'SELECT * FROM judges WHERE event_id=? AND id=?',
                                   (event_id, judge_id))
            require(team['status'] == 'registered' and judge['active'], 'inactive_participant',
                    'Team or judge is inactive', 409)
            require(team['group_code'] == judge['group_code'], 'wrong_group',
                    'Judge is assigned to another group')
            group = self.store.one(db, 'SELECT * FROM groups WHERE event_id=? AND code=?',
                                   (event_id, team['group_code']))
            session = self.store.one(db, 'SELECT * FROM score_sessions WHERE event_id=? AND group_code=?',
                                     (event_id, team['group_code']))
            require(session['state'] == 'open', 'session_closed',
                    'Group scoring session is not open', 409)
            pending = db.execute('SELECT id FROM score_disputes WHERE event_id=? AND team_id=? '
                                 'AND card_judge_id=? AND state=?',
                                 (event_id, team_id, judge_id, 'open')).fetchone()
            require(not pending, 'dispute_open', 'Resolve open dispute before editing this card', 409)
            criteria = json.loads(group['rubric'])
            require(set(scores) == set(criteria), 'criterion_mismatch',
                    'Scores must exactly match the group rubric')
            validated = {key: decimal_score(scores[key], Decimal(maximum))
                         for key, maximum in criteria.items()}
            total = sum(validated.values(), Decimal('0'))
            previous = db.execute('SELECT revision FROM score_cards WHERE event_id=? AND team_id=? AND judge_id=?',
                                  (event_id, team_id, judge_id)).fetchone()
            revision = previous['revision'] if previous else 0
            if expected_revision is not None:
                require(type(expected_revision) is int and expected_revision == revision,
                        'stale_score', 'Score card changed since inspection', 409)
            new_revision = revision + 1
            scores_json = json.dumps({key: str(value) for key, value in validated.items()}, sort_keys=True)
            db.execute('INSERT INTO score_cards(event_id,team_id,judge_id,revision,scores,total,submitted_at,'
                       'request_key,payload_hash) VALUES(?,?,?,?,?,?,?,?,?) '
                       'ON CONFLICT(event_id,team_id,judge_id) DO UPDATE SET '
                       'revision=excluded.revision,scores=excluded.scores,total=excluded.total,'
                       'submitted_at=excluded.submitted_at,request_key=excluded.request_key,'
                       'payload_hash=excluded.payload_hash',
                       (event_id, team_id, judge_id, new_revision, scores_json, str(total),
                        now, request_key, digest))
            db.execute('INSERT INTO score_history VALUES(?,?,?,?,?,?,?,?)',
                       (event_id, team_id, judge_id, new_revision, scores_json,
                        str(total), now, request_key))
            self.store.bump(db, event_id)
            self.store.audit(db, event_id, 'score_submitted', 'team', team_id,
                             {'judge': judge_id, 'revision': new_revision, 'total': str(total)}, now)
            row = self.store.one(db, 'SELECT * FROM score_cards WHERE event_id=? AND team_id=? AND judge_id=?',
                                 (event_id, team_id, judge_id))
            result = self._card(row)
            db.execute('INSERT INTO score_requests VALUES(?,?,?,?)',
                       (event_id, request_key, digest, json.dumps(result, sort_keys=True)))
            return result

    @staticmethod
    def _card(row) -> dict:
        return {'team_id': row['team_id'], 'judge_id': row['judge_id'],
                'revision': row['revision'], 'scores': json.loads(row['scores']),
                'total': row['total'], 'submitted_at': row['submitted_at']}

    def cards(self, event_id: str, team_id: str | None = None) -> list[dict]:
        with self.store.connection() as db:
            self.store.one(db, 'SELECT id FROM events WHERE id=?', (event_id,))
            if team_id:
                rows = self.store.many(db, 'SELECT * FROM score_cards WHERE event_id=? AND team_id=? '
                                       'ORDER BY judge_id', (event_id, team_id))
            else:
                rows = self.store.many(db, 'SELECT * FROM score_cards WHERE event_id=? '
                                       'ORDER BY team_id,judge_id', (event_id,))
            return [self._card(row) for row in rows]

    def score_history(self, event_id: str, team_id: str, judge_id: str) -> list[dict]:
        with self.store.connection() as db:
            rows = self.store.many(db, 'SELECT * FROM score_history WHERE event_id=? AND team_id=? '
                                   'AND judge_id=? ORDER BY revision', (event_id, team_id, judge_id))
            for row in rows:
                row['scores'] = json.loads(row['scores'])
            return rows
