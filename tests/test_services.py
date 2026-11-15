from community_fitness.errors import DomainError
from tests.helpers import Scenario, TEN, ELEVEN


class ServiceTests(Scenario):
    def test_zone_and_encounter(self):
        self.seed()
        result = self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 2,
                                                {'topic': 'tea'})
        self.assertFalse(result['duplicate'])
        self.assertEqual(self.analytics.service_preview('evt')['total_visits'], 2)

    def test_encounter_replay(self):
        self.seed()
        self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 2)
        replay = self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 2)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(len(self.services.encounters('evt')), 1)

    def test_encounter_conflict(self):
        self.seed()
        self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 2)
        with self.assertRaises(DomainError) as caught:
            self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 3)
        self.assertEqual(caught.exception.code, 'encounter_conflict')

    def test_encounter_outside_zone_hours(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.services.record_encounter('evt', 'e1', 'tea',
                                           '2026-10-26T18:00:00+08:00', 'v1', 1)
        self.assertEqual(caught.exception.code, 'outside_zone_hours')

    def test_encounter_count_bounds(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 0)
        self.assertEqual(caught.exception.code, 'invalid_count')

    def test_batch_partial_acceptance(self):
        self.seed()
        rows = [
            {'id': 'e1', 'zone_code': 'tea', 'occurred_at': TEN,
             'visitor_token': 'v1', 'count': 2},
            {'id': 'e2', 'zone_code': 'tea', 'occurred_at': TEN,
             'visitor_token': 'v2', 'count': -1},
            {'id': 'e3', 'zone_code': 'tea', 'occurred_at': ELEVEN,
             'visitor_token': 'v3', 'count': 1},
        ]
        result = self.services.ingest_batch('evt', 'batch1', rows)
        self.assertEqual([row['index'] for row in result['accepted']], [0, 2])
        self.assertEqual(result['rejected'][0]['code'], 'invalid_count')
        self.assertEqual(self.analytics.service_preview('evt')['total_visits'], 3)

    def test_batch_replay_and_conflict(self):
        self.seed()
        rows = [{'id': 'e1', 'zone_code': 'tea', 'occurred_at': TEN,
                 'visitor_token': 'v1', 'count': 1}]
        first = self.services.ingest_batch('evt', 'batch1', rows)
        second = self.services.ingest_batch('evt', 'batch1', rows)
        self.assertFalse(first['replayed'])
        self.assertTrue(second['replayed'])
        with self.assertRaises(DomainError) as caught:
            self.services.ingest_batch('evt', 'batch1', rows + rows)
        self.assertEqual(caught.exception.code, 'request_conflict')

    def test_batch_row_conflict_isolated(self):
        self.seed()
        rows = [
            {'id': 'e1', 'zone_code': 'tea', 'occurred_at': TEN,
             'visitor_token': 'v1', 'count': 1},
            {'id': 'e1', 'zone_code': 'tea', 'occurred_at': TEN,
             'visitor_token': 'v1', 'count': 2},
            {'id': 'e2', 'zone_code': 'tea', 'occurred_at': ELEVEN,
             'visitor_token': 'v2', 'count': 1},
        ]
        result = self.services.ingest_batch('evt', 'batch1', rows)
        self.assertEqual(len(result['accepted']), 2)
        self.assertEqual(result['rejected'][0]['code'], 'encounter_conflict')
        self.assertEqual(len(self.services.encounters('evt')), 2)

    def test_unique_tokens_are_not_visit_counts(self):
        self.seed()
        self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 3)
        self.services.record_encounter('evt', 'e2', 'tea', ELEVEN, 'v1', 2)
        summary = self.analytics.service_preview('evt')
        self.assertEqual(summary['unique_visitors'], 1)
        self.assertEqual(summary['total_visits'], 5)

    def test_encounter_attribute_whitelist(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 1,
                                           {'private_name': 'Alice'})
        self.assertEqual(caught.exception.code, 'invalid_attributes')

    def test_service_hours_within_event(self):
        self.competition.create_event('evt', 'Showcase',
                                      '2026-10-26T08:00:00+08:00',
                                      '2026-10-26T19:00:00+08:00', 'Asia/Shanghai')
        with self.assertRaises(DomainError) as caught:
            self.services.add_zone('evt', 'tea', 'Tea', 'tea',
                                   '2026-10-26T07:00:00+08:00',
                                   '2026-10-26T18:00:00+08:00')
        self.assertEqual(caught.exception.code, 'outside_event')

    def test_zone_kind(self):
        self.competition.create_event('evt', 'Showcase',
                                      '2026-10-26T08:00:00+08:00',
                                      '2026-10-26T19:00:00+08:00', 'Asia/Shanghai')
        with self.assertRaises(DomainError) as caught:
            self.services.add_zone('evt', 'tea', 'Tea', 'unknown',
                                   '2026-10-26T09:00:00+08:00',
                                   '2026-10-26T18:00:00+08:00')
        self.assertEqual(caught.exception.code, 'invalid_zone_kind')

    def test_quality_counts_bad_rows(self):
        self.seed()
        rows = [{'id': 'e1', 'zone_code': 'tea', 'occurred_at': TEN,
                 'visitor_token': 'v1', 'count': 1},
                {'id': 'e2', 'zone_code': 'tea', 'occurred_at': TEN,
                 'visitor_token': 'v2', 'count': 0}]
        self.services.ingest_batch('evt', 'batch1', rows)
        quality = self.analytics.quality('evt')
        self.assertEqual(quality['batch_accepted'], 1)
        self.assertEqual(quality['batch_rejected'], 1)

    def test_encounter_list_filter(self):
        self.seed()
        self.services.record_encounter('evt', 'e1', 'tea', TEN, 'v1', 1)
        self.assertEqual(self.services.encounters('evt', zone_code='tea')[0]['id'], 'e1')
        self.assertEqual(self.services.encounters('evt', zone_code='other'), [])

    def test_batch_duplicate_within_request(self):
        self.seed()
        row = {'id': 'e1', 'zone_code': 'tea', 'occurred_at': TEN,
               'visitor_token': 'v1', 'count': 1}
        result = self.services.ingest_batch('evt', 'batch1', [row, row])
        self.assertEqual(len(result['accepted']), 2)
        self.assertTrue(result['accepted'][1]['duplicate'])
        self.assertEqual(self.analytics.service_preview('evt')['total_encounters'], 1)
