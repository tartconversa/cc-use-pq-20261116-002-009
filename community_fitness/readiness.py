from collections import defaultdict

from .analytics import Analytics
from .attendance import Attendance
from .disputes import Disputes
from .storage import Store


class Readiness:
    def __init__(self, store: Store):
        self.store = store
        self.analytics = Analytics(store)
        self.attendance = Attendance(store)
        self.disputes = Disputes(store)

    def report(self, event_id: str) -> dict:
        with self.store.connection() as db:
            event = self.store.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            scores = self.analytics._scores(db, event_id)
            attendance = self.attendance._summary(db, event_id)
            disputes = self.disputes.unresolved(db, event_id)
            sessions = self.store.many(db, 'SELECT group_code,state FROM score_sessions WHERE event_id=?',
                                       (event_id,))
            session_state = {item['group_code']: item['state'] for item in sessions}
            issues = []
            for group in scores['groups']:
                if not group['eligible']:
                    issues.append({'code': 'insufficient_judges', 'group': group['code']})
                for team in group['teams']:
                    if not team['complete']:
                        issues.append({'code': 'missing_cards', 'group': group['code'],
                                       'team_id': team['team_id'],
                                       'judges': team['missing_judges']})
                if session_state.get(group['code']) != 'closed':
                    issues.append({'code': 'session_not_closed', 'group': group['code']})
            for dispute in disputes:
                issues.append({'code': 'open_dispute', 'id': dispute['id']})
            return {'event_id': event_id, 'revision': event['revision'],
                    'event_state': event['state'], 'scores_complete': scores['complete'],
                    'attendance': attendance, 'issues': issues,
                    'can_close': event['state'] == 'open' and not issues,
                    'can_publish': event['state'] == 'closed' and not issues}
