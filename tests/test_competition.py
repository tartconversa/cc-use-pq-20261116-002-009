from community_fitness.errors import DomainError
from tests.helpers import Scenario, START, END


class CompetitionTests(Scenario):
    def test_create_group_and_open(self):
        self.seed()
        event = self.store.inspect('evt')
        self.assertEqual(event['state'], 'open')
        self.assertEqual(len(event['teams']), 2)
        self.assertEqual(self.sessions.get('evt', 'dance')['state'], 'open')

    def test_duplicate_event(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.competition.create_event('evt', 'Again', START, END, 'Asia/Shanghai')
        self.assertEqual(caught.exception.code, 'duplicate_event')

    def test_invalid_event_window(self):
        with self.assertRaises(DomainError) as caught:
            self.competition.create_event('evt', 'Bad', END, START, 'Asia/Shanghai')
        self.assertEqual(caught.exception.code, 'invalid_interval')

    def test_unknown_timezone(self):
        with self.assertRaises(DomainError) as caught:
            self.competition.create_event('evt', 'Bad', START, END, 'Nowhere/Fake')
        self.assertEqual(caught.exception.code, 'invalid_timezone')

    def test_group_rubric_sum(self):
        self.competition.create_event('evt', 'Showcase', START, END, 'Asia/Shanghai')
        with self.assertRaises(DomainError) as caught:
            self.competition.add_group('evt', 'dance', 'Dance', {'a': 50, 'b': 40}, 2, 3)
        self.assertEqual(caught.exception.code, 'invalid_rubric')

    def test_group_requires_team_before_open(self):
        self.competition.create_event('evt', 'Showcase', START, END, 'Asia/Shanghai')
        self.competition.add_group('evt', 'dance', 'Dance', {'a': 50, 'b': 50}, 1, 2)
        with self.assertRaises(DomainError) as caught:
            self.competition.change_state('evt', 'open', self.revision())
        self.assertEqual(caught.exception.code, 'missing_team')
        self.assertEqual(self.store.inspect('evt')['state'], 'draft')

    def test_stale_event_revision(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.competition.change_state('evt', 'closed', 0)
        self.assertEqual(caught.exception.code, 'stale_revision')

    def test_score_requires_session(self):
        self.seed()
        with self.store.transaction() as db:
            db.execute('UPDATE score_sessions SET state=? WHERE event_id=?', ('closed', 'evt'))
        with self.assertRaises(DomainError) as caught:
            self.competition.submit_score('evt', 't0', 'j0',
                                          {'form': 50, 'rhythm': 30}, 'first')
        self.assertEqual(caught.exception.code, 'session_closed')

    def test_score_group_mismatch(self):
        self.seed()
        self.competition.create_event('other', 'Other', START, END, 'Asia/Shanghai')
        with self.assertRaises(DomainError) as caught:
            self.competition.submit_score('evt', 't0', 'missing',
                                          {'form': 50, 'rhythm': 30}, 'first')
        self.assertEqual(caught.exception.code, 'not_found')

    def test_score_criterion_mismatch(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.competition.submit_score('evt', 't0', 'j0', {'form': 50}, 'first')
        self.assertEqual(caught.exception.code, 'criterion_mismatch')

    def test_score_precision_rejected(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.competition.submit_score('evt', 't0', 'j0',
                                          {'form': '50.123', 'rhythm': 30}, 'first')
        self.assertEqual(caught.exception.code, 'invalid_score')

    def test_score_replay_survives_correction(self):
        self.seed()
        first = self.competition.submit_score('evt', 't0', 'j0',
                                              {'form': 50, 'rhythm': 30}, 'first')
        second = self.competition.submit_score('evt', 't0', 'j0',
                                               {'form': 52, 'rhythm': 32}, 'second', 1)
        replay = self.competition.submit_score('evt', 't0', 'j0',
                                               {'form': 50, 'rhythm': 30}, 'first')
        self.assertEqual(first, replay)
        self.assertEqual(second['revision'], 2)
        self.assertEqual(len(self.competition.score_history('evt', 't0', 'j0')), 2)

    def test_score_key_conflict(self):
        self.seed()
        self.competition.submit_score('evt', 't0', 'j0', {'form': 50, 'rhythm': 30}, 'first')
        with self.assertRaises(DomainError) as caught:
            self.competition.submit_score('evt', 't0', 'j0',
                                          {'form': 51, 'rhythm': 30}, 'first')
        self.assertEqual(caught.exception.code, 'request_conflict')

    def test_score_cas_conflict(self):
        self.seed()
        self.competition.submit_score('evt', 't0', 'j0', {'form': 50, 'rhythm': 30}, 'first')
        with self.assertRaises(DomainError) as caught:
            self.competition.submit_score('evt', 't0', 'j0',
                                          {'form': 51, 'rhythm': 30}, 'second', 0)
        self.assertEqual(caught.exception.code, 'stale_score')

    def test_cannot_close_with_open_session(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.competition.change_state('evt', 'closed', self.revision())
        self.assertEqual(caught.exception.code, 'sessions_not_closed')

    def test_register_after_open_rejected(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.competition.register_team('evt', 'late', 'dance', 'Late', 'Qingpu', 3)
        self.assertEqual(caught.exception.code, 'event_locked')

    def test_judge_limit(self):
        self.seed()
        self.competition.assign_judge('evt', 'j2', 'Judge 2', 'dance')
        with self.assertRaises(DomainError) as caught:
            self.competition.assign_judge('evt', 'j3', 'Judge 3', 'dance')
        self.assertEqual(caught.exception.code, 'judge_limit')

    def test_session_missing_cards(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.sessions.close('evt', 'dance', self.revision())
        self.assertEqual(caught.exception.code, 'incomplete_scores')
        self.assertEqual(self.sessions.get('evt', 'dance')['state'], 'open')

    def test_session_close_complete(self):
        self.seed()
        self.score_all()
        result = self.sessions.close('evt', 'dance', self.revision())
        self.assertEqual(result['state'], 'closed')
        self.assertEqual(self.sessions.get('evt', 'dance')['missing_cards'], [])

    def test_group_standings_tie(self):
        self.seed()
        self.score_all()
        standings = self.analytics.score_preview('evt')['groups'][0]['teams']
        self.assertEqual([row['rank'] for row in standings], [1, 1])
        self.assertTrue(all(row['score'] == '86.00' for row in standings))

    def test_incomplete_score_not_zero(self):
        self.seed()
        self.competition.submit_score('evt', 't0', 'j0',
                                      {'form': 50, 'rhythm': 35}, 'one')
        row = self.analytics.score_preview('evt')['groups'][0]['teams'][0]
        self.assertIsNone(row['score'])
        self.assertFalse(row['complete'])
