import csv
import io
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from .errors import DomainError, require
from .storage import Store
from .validation import instant, zone


def _display(value: Decimal, places: str = '0.01') -> str:
    return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def _rank(teams: list[dict]) -> list[dict]:
    ordered = sorted(teams, key=lambda row: (-Decimal(row['score']), -Decimal(row['median']),
                                             row['team_id']))
    position = 0
    last_key = None
    for number, row in enumerate(ordered, 1):
        key = (row['score'], row['median'])
        if key != last_key:
            position = number
            last_key = key
        row['rank'] = position
    return ordered


class Analytics:
    def __init__(self, store: Store):
        self.store = store

    def score_preview(self, event_id: str) -> dict:
        with self.store.connection() as db:
            self.store.one(db, 'SELECT id FROM events WHERE id=?', (event_id,))
            return self._scores(db, event_id)

    def _scores(self, db, event_id: str) -> dict:
        groups = self.store.many(db, 'SELECT * FROM groups WHERE event_id=? ORDER BY code',
                                 (event_id,))
        teams = self.store.many(db, 'SELECT * FROM teams WHERE event_id=? ORDER BY id',
                                (event_id,))
        judges = self.store.many(db, 'SELECT * FROM judges WHERE event_id=? AND active=1',
                                 (event_id,))
        cards = self.store.many(db, 'SELECT * FROM score_cards WHERE event_id=?', (event_id,))
        judges_by_group = defaultdict(list)
        cards_by_team = defaultdict(list)
        teams_by_group = defaultdict(list)
        for judge in judges:
            judges_by_group[judge['group_code']].append(judge['id'])
        for card in cards:
            cards_by_team[card['team_id']].append(card)
        for team in teams:
            teams_by_group[team['group_code']].append(team)
        result = []
        for group in groups:
            code = group['code']
            assigned = set(judges_by_group[code])
            eligible = len(assigned) >= group['min_judges']
            entries = []
            for team in teams_by_group[code]:
                found = cards_by_team[team['id']]
                submitted = {card['judge_id'] for card in found}
                missing = sorted(assigned - submitted)
                unexpected = sorted(submitted - assigned)
                complete = eligible and not missing and not unexpected
                row = {'team_id': team['id'], 'team_name': team['name'],
                       'district': team['district'], 'member_count': team['member_count'],
                       'judge_count': len(assigned), 'card_count': len(found),
                       'missing_judges': missing, 'unexpected_judges': unexpected,
                       'complete': complete}
                if complete:
                    values = sorted(Decimal(card['total']) for card in found)
                    average = sum(values) / Decimal(len(values))
                    median = (values[(len(values) - 1) // 2] + values[len(values) // 2]) / 2
                    row['score'] = _display(average)
                    row['median'] = _display(median)
                    criterion_totals = defaultdict(Decimal)
                    for card in found:
                        for criterion, value in json.loads(card['scores']).items():
                            criterion_totals[criterion] += Decimal(value)
                    row['criteria'] = {key: _display(value / len(values))
                                       for key, value in sorted(criterion_totals.items())}
                else:
                    row['score'] = None
                    row['median'] = None
                    row['criteria'] = None
                    row['rank'] = None
                entries.append(row)
            ranked = _rank([row for row in entries if row['complete']])
            incomplete = sorted((row for row in entries if not row['complete']),
                                key=lambda row: row['team_id'])
            result.append({'code': code, 'label': group['label'],
                           'min_judges': group['min_judges'], 'assigned_judges': sorted(assigned),
                           'eligible': eligible, 'teams': ranked + incomplete})
        return {'groups': result, 'complete': bool(result) and all(
            group['eligible'] and bool(group['teams']) and all(team['complete'] for team in group['teams'])
            for group in result)}

    def service_preview(self, event_id: str) -> dict:
        with self.store.connection() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            return self._services(db, event)

    def _services(self, db, event: dict) -> dict:
        zones = self.store.many(db, 'SELECT * FROM service_zones WHERE event_id=? ORDER BY code',
                                (event['id'],))
        rows = self.store.many(db, 'SELECT * FROM encounters WHERE event_id=? ORDER BY id',
                               (event['id'],))
        local_zone = zone(event['timezone'])
        grouped: dict[str, dict] = {}
        by_day: dict[str, dict] = {}
        for service in zones:
            grouped[service['code']] = {'code': service['code'], 'label': service['label'],
                                        'kind': service['kind'], 'encounters': 0,
                                        'visits': 0, 'unique_tokens': set()}
        for row in rows:
            item = grouped[row['zone_code']]
            item['encounters'] += 1
            item['visits'] += row['count']
            item['unique_tokens'].add(row['visitor_token'])
            day = instant(row['occurred_at']).astimezone(local_zone).date().isoformat()
            current = by_day.setdefault(day, {'date': day, 'encounters': 0, 'visits': 0,
                                              'unique_tokens': set(), 'zones': defaultdict(int)})
            current['encounters'] += 1
            current['visits'] += row['count']
            current['unique_tokens'].add(row['visitor_token'])
            current['zones'][row['zone_code']] += row['count']
        rendered_zones = []
        for item in grouped.values():
            item['unique_visitors'] = len(item.pop('unique_tokens'))
            rendered_zones.append(item)
        rendered_days = []
        for item in sorted(by_day.values(), key=lambda row: row['date']):
            item['unique_visitors'] = len(item.pop('unique_tokens'))
            item['zones'] = dict(sorted(item['zones'].items()))
            rendered_days.append(item)
        return {'zones': rendered_zones, 'days': rendered_days,
                'total_encounters': len(rows), 'total_visits': sum(row['count'] for row in rows),
                'unique_visitors': len({row['visitor_token'] for row in rows}),
                'visitor_measure': 'tokens are counted per event; visits sum row counts'}

    def quality(self, event_id: str) -> dict:
        with self.store.connection() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            scores = self._scores(db, event_id)
            services = self._services(db, event)
            requests = self.store.many(db, 'SELECT result FROM ingest_requests WHERE event_id=?',
                                       (event_id,))
            rejected = sum(len(json.loads(row['result'])['rejected']) for row in requests)
            accepted = sum(len(json.loads(row['result'])['accepted']) for row in requests)
            return {'event_id': event_id, 'revision': event['revision'],
                    'scores_complete': scores['complete'],
                    'incomplete_teams': [team['team_id'] for group in scores['groups']
                                         for team in group['teams'] if not team['complete']],
                    'service_encounters': services['total_encounters'],
                    'batch_accepted': accepted, 'batch_rejected': rejected}

    def standings_csv(self, payload: dict) -> str:
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['event_id', 'version', 'group', 'rank', 'team_id', 'team_name',
                         'district', 'score', 'median', 'judges'])
        for group in payload['scores']['groups']:
            for team in group['teams']:
                writer.writerow([payload['event']['id'], payload['version'], group['code'],
                                 team['rank'], team['team_id'], team['team_name'],
                                 team['district'], team['score'], team['median'],
                                 team['card_count']])
        return stream.getvalue()

    def service_csv(self, payload: dict) -> str:
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['event_id', 'version', 'date', 'encounters', 'visits', 'unique_visitors'])
        for day in payload['services']['days']:
            writer.writerow([payload['event']['id'], payload['version'], day['date'],
                             day['encounters'], day['visits'], day['unique_visitors']])
        return stream.getvalue()
