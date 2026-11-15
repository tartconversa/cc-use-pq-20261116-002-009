from community_fitness.errors import DomainError
from tests.helpers import Scenario, TEN


class AttendanceDisputeTests(Scenario):
    def test_check_in_and_summary(self):
        self.seed()
        result = self.attendance.check_in('evt', 't0', 'member1', 'north', TEN, 'r1')
        self.assertFalse(result['duplicate'])
        self.assertEqual(self.attendance.team('evt', 't0')['arrived_members'], 1)
        self.assertEqual(self.attendance.summary('evt')['registered_members'], 6)

    def test_check_in_replay(self):
        self.seed()
        self.attendance.check_in('evt', 't0', 'member1', 'north', TEN, 'r1')
        result = self.attendance.check_in('evt', 't0', 'member1', 'north', TEN, 'r1')
        self.assertTrue(result['duplicate'])
        self.assertEqual(self.attendance.summary('evt')['arrived_members'], 1)

    def test_check_in_key_conflict(self):
        self.seed()
        self.attendance.check_in('evt', 't0', 'member1', 'north', TEN, 'r1')
        with self.assertRaises(DomainError) as caught:
            self.attendance.check_in('evt', 't0', 'member2', 'north', TEN, 'r1')
        self.assertEqual(caught.exception.code, 'request_conflict')

    def test_member_duplicate_different_request(self):
        self.seed()
        self.attendance.check_in('evt', 't0', 'member1', 'north', TEN, 'r1')
        with self.assertRaises(DomainError) as caught:
            self.attendance.check_in('evt', 't0', 'member1', 'north', TEN, 'r2')
        self.assertEqual(caught.exception.code, 'member_already_checked_in')

    def test_check_in_capacity(self):
        self.seed()
        for i in range(3):
            self.attendance.check_in('evt', 't0', f'm{i}', 'north', TEN, f'r{i}')
        with self.assertRaises(DomainError) as caught:
            self.attendance.check_in('evt', 't0', 'm3', 'north', TEN, 'r3')
        self.assertEqual(caught.exception.code, 'attendance_capacity')

    def test_check_in_outside_event(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.attendance.check_in('evt', 't0', 'm1', 'north',
                                     '2026-10-26T19:00:00+08:00', 'r1')
        self.assertEqual(caught.exception.code, 'outside_event')

    def test_dispute_open_and_resolve(self):
        self.seed()
        self.competition.submit_score('evt', 't0', 'j0',
                                      {'form': 50, 'rhythm': 30}, 'score1')
        dispute = self.disputes.open('evt', 'd1', 't0', 'j0', 'Wrong form score')
        self.assertEqual(dispute['state'], 'open')
        result = self.disputes.resolve('evt', 'd1', 'rejected', 'Card verified')
        self.assertEqual(result['state'], 'rejected')

    def test_duplicate_open_dispute(self):
        self.seed()
        self.competition.submit_score('evt', 't0', 'j0',
                                      {'form': 50, 'rhythm': 30}, 'score1')
        self.disputes.open('evt', 'd1', 't0', 'j0', 'Wrong score')
        with self.assertRaises(DomainError) as caught:
            self.disputes.open('evt', 'd2', 't0', 'j0', 'Wrong score again')
        self.assertEqual(caught.exception.code, 'dispute_exists')

    def test_dispute_blocks_card_edit(self):
        self.seed()
        self.competition.submit_score('evt', 't0', 'j0',
                                      {'form': 50, 'rhythm': 30}, 'score1')
        self.disputes.open('evt', 'd1', 't0', 'j0', 'Wrong score')
        with self.assertRaises(DomainError) as caught:
            self.competition.submit_score('evt', 't0', 'j0',
                                          {'form': 51, 'rhythm': 30}, 'score2', 1)
        self.assertEqual(caught.exception.code, 'dispute_open')

    def test_dispute_resolution_only_once(self):
        self.seed()
        self.competition.submit_score('evt', 't0', 'j0',
                                      {'form': 50, 'rhythm': 30}, 'score1')
        self.disputes.open('evt', 'd1', 't0', 'j0', 'Wrong score')
        self.disputes.resolve('evt', 'd1', 'rejected', 'Verified')
        with self.assertRaises(DomainError) as caught:
            self.disputes.resolve('evt', 'd1', 'upheld', 'Changed mind')
        self.assertEqual(caught.exception.code, 'dispute_closed')

    def test_dispute_requires_score(self):
        self.seed()
        with self.assertRaises(DomainError) as caught:
            self.disputes.open('evt', 'd1', 't0', 'j0', 'Missing card')
        self.assertEqual(caught.exception.code, 'not_found')

    def test_dispute_invalid_resolution(self):
        self.seed()
        self.competition.submit_score('evt', 't0', 'j0',
                                      {'form': 50, 'rhythm': 30}, 'score1')
        self.disputes.open('evt', 'd1', 't0', 'j0', 'Wrong score')
        with self.assertRaises(DomainError) as caught:
            self.disputes.resolve('evt', 'd1', 'unknown', 'No review')
        self.assertEqual(caught.exception.code, 'invalid_resolution')

    def test_audit_tracks_state_changes(self):
        self.seed()
        self.attendance.check_in('evt', 't0', 'm1', 'north', TEN, 'r1')
        trail = self.store.audit_trail('evt')
        actions = [row['action'] for row in trail]
        self.assertIn('member_checked_in', actions)
        self.assertIn('scoring_session_opened', actions)
