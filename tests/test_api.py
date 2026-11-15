import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from community_fitness.api import create_app
from tests.helpers import Scenario, START, END, TEN


class ApiTests(Scenario):
    def setUp(self):
        super().setUp()
        self.client = TestClient(create_app(Path(self.temp.name) / 'api.sqlite3'))

    def test_create_and_get_event(self):
        body = {'id': 'api1', 'name': 'Open day', 'starts_at': START,
                'ends_at': END, 'timezone': 'Asia/Shanghai'}
        response = self.client.post('/events', json=body)
        self.assertEqual(response.status_code, 201)
        loaded = self.client.get('/events/api1')
        self.assertEqual(loaded.json()['name'], 'Open day')

    def test_error_payload(self):
        response = self.client.get('/events/not-there')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error'], 'not_found')

    def test_api_full_scoring_and_export(self):
        self.client.post('/events', json={'id': 'api1', 'name': 'Open day',
                                         'starts_at': START, 'ends_at': END,
                                         'timezone': 'Asia/Shanghai'})
        group = {'code': 'dance', 'label': 'Dance', 'criteria': {'form': 60, 'music': 40},
                 'min_judges': 2, 'max_judges': 2}
        self.assertEqual(self.client.post('/events/api1/groups', json=group).status_code, 201)
        team = {'id': 't1', 'group_code': 'dance', 'name': 'Local team',
                'district': 'Qingpu', 'member_count': 2}
        self.assertEqual(self.client.post('/events/api1/teams', json=team).status_code, 201)
        for judge in ('j1', 'j2'):
            body = {'id': judge, 'name': judge, 'group_code': 'dance'}
            self.assertEqual(self.client.post('/events/api1/judges', json=body).status_code, 201)
        revision = self.client.get('/events/api1').json()['revision']
        self.client.post('/events/api1/state', json={'target': 'open',
                                                     'expected_revision': revision})
        revision = self.client.get('/events/api1').json()['revision']
        self.client.post('/events/api1/groups/dance/session',
                         json={'expected_event_revision': revision})
        for judge in ('j1', 'j2'):
            response = self.client.post('/events/api1/scores', json={
                'team_id': 't1', 'judge_id': judge,
                'scores': {'form': 55, 'music': 35}, 'request_key': judge})
            self.assertEqual(response.status_code, 200)
        revision = self.client.get('/events/api1').json()['revision']
        self.client.post('/events/api1/groups/dance/session/close',
                         json={'expected_event_revision': revision})
        revision = self.client.get('/events/api1').json()['revision']
        self.client.post('/events/api1/state', json={'target': 'closed',
                                                     'expected_revision': revision})
        preview = self.client.get('/events/api1/preview').json()
        published = self.client.post('/events/api1/publish', json={
            'expected_head': 0, 'expected_revision': preview['event']['revision']})
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.json()['scores']['groups'][0]['teams'][0]['score'], '90.00')
        exported = self.client.get('/events/api1/export/standings.csv')
        self.assertEqual(exported.status_code, 200)
        self.assertIn('Local team', exported.text)

    def test_api_validation_error(self):
        response = self.client.post('/events', json={'id': 'x'})
        self.assertEqual(response.status_code, 422)

    def test_api_health(self):
        self.assertEqual(self.client.get('/health').json(), {'status': 'ok'})
