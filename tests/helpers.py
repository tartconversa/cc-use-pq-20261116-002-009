import tempfile
import unittest
from pathlib import Path

from community_fitness.analytics import Analytics
from community_fitness.attendance import Attendance
from community_fitness.competition import Competition
from community_fitness.disputes import Disputes
from community_fitness.publication import Publication
from community_fitness.readiness import Readiness
from community_fitness.reconciliation import Reconciliation
from community_fitness.scoring_sessions import ScoringSessions
from community_fitness.services import Services
from community_fitness.storage import Store


START = '2026-10-26T08:00:00+08:00'
END = '2026-10-26T19:00:00+08:00'
TEN = '2026-10-26T10:00:00+08:00'
ELEVEN = '2026-10-26T11:00:00+08:00'


class Scenario(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'event.sqlite3')
        self.competition = Competition(self.store)
        self.services = Services(self.store)
        self.sessions = ScoringSessions(self.store)
        self.attendance = Attendance(self.store)
        self.disputes = Disputes(self.store)
        self.analytics = Analytics(self.store)
        self.publication = Publication(self.store)
        self.readiness = Readiness(self.store)
        self.reconciliation = Reconciliation(self.store)

    def revision(self):
        return self.store.inspect('evt')['revision']

    def seed(self, *, judges=2, teams=2, services=True):
        self.competition.create_event('evt', 'Community showcase', START, END, 'Asia/Shanghai')
        self.competition.add_group('evt', 'dance', 'Dance', {'form': 60, 'rhythm': 40}, 2, 3)
        for number in range(teams):
            self.competition.register_team('evt', f't{number}', 'dance', f'Team {number}',
                                           'Qingpu', 3)
        for number in range(judges):
            self.competition.assign_judge('evt', f'j{number}', f'Judge {number}', 'dance')
        if services:
            self.services.add_zone('evt', 'tea', 'Tea counter', 'tea',
                                   '2026-10-26T09:00:00+08:00', '2026-10-26T18:00:00+08:00')
        self.competition.change_state('evt', 'open', self.revision())
        self.sessions.open('evt', 'dance', self.revision())

    def score_all(self):
        for team in ('t0', 't1'):
            for judge in ('j0', 'j1'):
                self.competition.submit_score('evt', team, judge,
                                              {'form': 52, 'rhythm': 34}, f'{team}-{judge}')

    def close(self):
        self.sessions.close('evt', 'dance', self.revision())
        self.competition.change_state('evt', 'closed', self.revision())

    def publish(self):
        preview = self.publication.preview('evt')
        return self.publication.publish('evt', preview['head_version'],
                                        preview['event']['revision'])
