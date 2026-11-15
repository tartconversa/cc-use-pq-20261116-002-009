import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from .errors import DomainError, require
from .storage import Store
from .validation import instant, zone


class Reconciliation:
    def __init__(self, store: Store):
        self.store = store

    def event_health(self, event_id: str) -> dict:
        with self.store.connection() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            groups = self.store.many(db, 'SELECT * FROM groups WHERE event_id=?', (event_id,))
            teams = self.store.many(db, 'SELECT * FROM teams WHERE event_id=?', (event_id,))
            judges = self.store.many(db, 'SELECT * FROM judges WHERE event_id=? AND active=1',
                                     (event_id,))
            cards = self.store.many(db, 'SELECT * FROM score_cards WHERE event_id=?', (event_id,))
            disputes = self.store.many(db, 'SELECT * FROM score_disputes WHERE event_id=?', (event_id,))
            arrivals = self.store.many(db, 'SELECT * FROM attendance WHERE event_id=?', (event_id,))
            encounters = self.store.many(db, 'SELECT * FROM encounters WHERE event_id=?', (event_id,))
            zones = self.store.many(db, 'SELECT * FROM service_zones WHERE event_id=?', (event_id,))
            alerts = []
            judge_groups = defaultdict(set)
            team_groups = defaultdict(list)
            cards_by_team = defaultdict(set)
            attendance_by_team = defaultdict(int)
            for judge in judges:
                judge_groups[judge['group_code']].add(judge['id'])
            for team in teams:
                team_groups[team['group_code']].append(team)
            for card in cards:
                cards_by_team[card['team_id']].add(card['judge_id'])
            for row in arrivals:
                attendance_by_team[row['team_id']] += 1
            for group in groups:
                assigned = judge_groups[group['code']]
                if len(assigned) < group['min_judges']:
                    alerts.append({'code': 'insufficient_judges', 'entity': group['code'],
                                   'expected': group['min_judges'], 'actual': len(assigned)})
                if not team_groups[group['code']]:
                    alerts.append({'code': 'empty_group', 'entity': group['code'],
                                   'expected': 1, 'actual': 0})
                for team in team_groups[group['code']]:
                    missing = sorted(assigned - cards_by_team[team['id']])
                    if missing:
                        alerts.append({'code': 'missing_scores', 'entity': team['id'],
                                       'judges': missing})
                    if attendance_by_team[team['id']] > team['member_count']:
                        alerts.append({'code': 'attendance_exceeds_registration',
                                       'entity': team['id'], 'expected': team['member_count'],
                                       'actual': attendance_by_team[team['id']]})
            for dispute in disputes:
                if dispute['state'] == 'open':
                    alerts.append({'code': 'open_dispute', 'entity': dispute['id'],
                                   'team_id': dispute['team_id']})
            zone_by_code = {item['code']: item for item in zones}
            for encounter in encounters:
                service = zone_by_code.get(encounter['zone_code'])
                if service is None:
                    alerts.append({'code': 'unknown_zone', 'entity': encounter['id']})
                elif not (instant(service['opens_at']) <= instant(encounter['occurred_at'])
                          < instant(service['closes_at'])):
                    alerts.append({'code': 'out_of_hours', 'entity': encounter['id']})
            return {'event_id': event_id, 'revision': event['revision'],
                    'event_state': event['state'], 'alerts': alerts,
                    'counts': {'groups': len(groups), 'teams': len(teams),
                               'judges': len(judges), 'cards': len(cards),
                               'attendance': len(arrivals), 'encounters': len(encounters),
                               'disputes': len(disputes)}}

    def audit_verify(self, event_id: str) -> dict:
        with self.store.connection() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            history = self.store.many(db, 'SELECT * FROM audit WHERE event_id=? ORDER BY seq',
                                      (event_id,))
            by_action = defaultdict(int)
            malformed = []
            for row in history:
                by_action[row['action']] += 1
                try:
                    detail = json.loads(row['detail'])
                    require(isinstance(detail, dict), 'invalid_audit', 'Audit detail must be an object')
                except (ValueError, DomainError):
                    malformed.append(row['seq'])
            cards = db.execute('SELECT COUNT(*) FROM score_history WHERE event_id=?',
                               (event_id,)).fetchone()[0]
            encounter_count = db.execute('SELECT COUNT(*) FROM encounters WHERE event_id=?',
                                         (event_id,)).fetchone()[0]
            checkins = db.execute('SELECT COUNT(*) FROM attendance WHERE event_id=?',
                                  (event_id,)).fetchone()[0]
            mismatches = {}
            for action, count in [('score_submitted', cards),
                                  ('encounter_recorded', encounter_count),
                                  ('member_checked_in', checkins)]:
                if by_action[action] != count:
                    mismatches[action] = {'audit': by_action[action], 'rows': count}
            return {'event_id': event_id, 'event_revision': event['revision'],
                    'audit_count': len(history), 'by_action': dict(sorted(by_action.items())),
                    'malformed_sequences': malformed, 'mismatches': mismatches,
                    'consistent': not malformed and not mismatches}

    def local_timeline(self, event_id: str) -> list[dict]:
        with self.store.connection() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            local = zone(event['timezone'])
            arrivals = self.store.many(db, 'SELECT arrived_at,team_id,gate FROM attendance '
                                       'WHERE event_id=?', (event_id,))
            encounters = self.store.many(db, 'SELECT occurred_at,zone_code,count FROM encounters '
                                         'WHERE event_id=?', (event_id,))
            cards = self.store.many(db, 'SELECT submitted_at,team_id FROM score_history '
                                    'WHERE event_id=?', (event_id,))
            bins = defaultdict(lambda: {'arrivals': 0, 'service_visits': 0,
                                        'encounters': 0, 'score_submissions': 0})
            for row in arrivals:
                hour = instant(row['arrived_at']).astimezone(local).strftime('%Y-%m-%dT%H:00')
                bins[hour]['arrivals'] += 1
            for row in encounters:
                hour = instant(row['occurred_at']).astimezone(local).strftime('%Y-%m-%dT%H:00')
                bins[hour]['service_visits'] += row['count']
                bins[hour]['encounters'] += 1
            for row in cards:
                hour = instant(row['submitted_at']).astimezone(local).strftime('%Y-%m-%dT%H:00')
                bins[hour]['score_submissions'] += 1
            return [{'hour': hour, **bins[hour]} for hour in sorted(bins)]
