from community_fitness.errors import DomainError
from tests.helpers import Scenario, TEN


class PublicationTests(Scenario):
    def test_publish_complete_event(self):
        self.seed()
        self.score_all()
        self.close()
        snapshot = self.publish()
        self.assertEqual(snapshot['version'], 1)
        self.assertEqual(self.publication.get('evt'), snapshot)

    def test_unpublished_head_missing(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.publication.get('evt')
        self.assertEqual(caught.exception.code, 'snapshot_not_found')

    def test_publish_requires_closed_event(self):
        self.seed()
        self.score_all()
        preview = self.publication.preview('evt')
        self.assertFalse(preview['publishable'])
        with self.assertRaises(DomainError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, 'event_not_closed')

    def test_publish_requires_scores(self):
        self.seed()
        with self.store.transaction() as db:
            db.execute('UPDATE score_sessions SET state=? WHERE event_id=?', ('closed', 'evt'))
        self.competition.change_state('evt', 'closed', self.revision())
        with self.assertRaises(DomainError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, 'incomplete_scores')

    def test_publish_head_cas(self):
        self.seed()
        self.score_all()
        self.close()
        self.publish()
        with self.assertRaises(DomainError) as caught:
            self.publication.publish('evt', 0, self.revision())
        self.assertEqual(caught.exception.code, 'stale_head')

    def test_publish_revision_cas(self):
        self.seed()
        self.score_all()
        self.close()
        with self.assertRaises(DomainError) as caught:
            self.publication.publish('evt', 0, self.revision() - 1)
        self.assertEqual(caught.exception.code, 'stale_revision')

    def test_old_snapshot_remains_stable_after_new_encounter(self):
        self.seed()
        self.score_all()
        self.close()
        first = self.publish()
        self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 4)
        second = self.publish()
        self.assertEqual(first['services']['total_visits'], 0)
        self.assertEqual(second['services']['total_visits'], 4)
        self.assertEqual(self.publication.get('evt', 1), first)
        self.assertEqual(self.publication.get('evt', 2), second)

    def test_versions_and_difference(self):
        self.seed()
        self.score_all()
        self.close()
        self.publish()
        self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 4)
        self.publish()
        versions = self.publication.versions('evt')
        self.assertEqual([row['version'] for row in versions], [1, 2])
        diff = self.publication.difference('evt', 1, 2)
        self.assertEqual(diff['visits_delta'], 4)
        self.assertEqual(diff['zones'][0]['zone_code'], 'tea')

    def test_dispute_blocks_publication(self):
        self.seed()
        self.score_all()
        self.disputes.open('evt', 'd1', 't0', 'j0', 'Incorrect criterion')
        with self.assertRaises(DomainError) as caught:
            self.sessions.close('evt', 'dance', self.revision())
        self.assertEqual(caught.exception.code, 'unresolved_dispute')
        self.disputes.resolve('evt', 'd1', 'rejected', 'Reviewed original card')
        self.close()
        self.assertEqual(self.publish()['version'], 1)

    def test_standings_csv_uses_snapshot(self):
        self.seed()
        self.score_all()
        self.close()
        first = self.publish()
        csv_data = self.analytics.standings_csv(first)
        self.assertIn('team_id', csv_data)
        self.assertIn('t0', csv_data)
        self.assertIn('86.00', csv_data)

    def test_service_csv_uses_snapshot(self):
        self.seed()
        self.score_all()
        self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 2)
        self.close()
        first = self.publish()
        csv_data = self.analytics.service_csv(first)
        self.assertIn('date,encounters,visits', csv_data)
        self.assertIn('2026-10-26,1,2,1', csv_data)

    def test_same_version_difference_invalid(self):
        self.seed()
        self.score_all()
        self.close()
        self.publish()
        with self.assertRaises(DomainError) as caught:
            self.publication.difference('evt', 1, 1)
        self.assertEqual(caught.exception.code, 'same_version')

    def test_nonexistent_snapshot(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.publication.get('evt', 99)
        self.assertEqual(caught.exception.code, 'snapshot_not_found')

    def test_snapshot_contains_attendance(self):
        self.seed()
        self.score_all()
        self.attendance.check_in('evt', 't0', 'm1', 'north', TEN, 'arrival1')
        self.close()
        snapshot = self.publish()
        self.assertEqual(snapshot['attendance']['arrived_members'], 1)

    def test_readiness_mirrors_session_and_scores(self):
        self.seed()
        self.assertFalse(self.readiness.report('evt')['can_close'])
        self.score_all()
        self.sessions.close('evt', 'dance', self.revision())
        self.assertTrue(self.readiness.report('evt')['can_close'])
        self.competition.change_state('evt', 'closed', self.revision())
        self.assertTrue(self.readiness.report('evt')['can_publish'])
