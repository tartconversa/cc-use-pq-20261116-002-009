import json
import tempfile
from pathlib import Path

from .competition import Competition
from .publication import Publication
from .services import Services
from .scoring_sessions import ScoringSessions
from .storage import Store


def main() -> None:
    fixture = json.loads(Path('fixtures/community_event.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / 'demo.sqlite3')
        competition, services, publication = Competition(store), Services(store), Publication(store)
        sessions = ScoringSessions(store)
        event = fixture['event']
        competition.create_event(event['id'], event['name'], event['starts_at'],
                                 event['ends_at'], event['timezone'])
        for group in fixture['groups']:
            competition.add_group(event['id'], **group)
        for team in fixture['teams']:
            competition.register_team(event['id'], team['team_id'], team['group_code'],
                                      team['name'], team['district'], team['member_count'])
        for judge in fixture['judges']:
            competition.assign_judge(event['id'], judge['judge_id'], judge['name'],
                                     judge['group_code'])
        for service in fixture['zones']:
            services.add_zone(event['id'], **service)
        revision = store.inspect(event['id'])['revision']
        competition.change_state(event['id'], 'open', revision)
        for group in fixture['groups']:
            sessions.open(event['id'], group['code'], store.inspect(event['id'])['revision'])
        for score in fixture['scores']:
            competition.submit_score(event['id'], **score)
        result = services.ingest_batch(event['id'], 'demo-encounters', fixture['encounters'])
        for group in fixture['groups']:
            sessions.close(event['id'], group['code'], store.inspect(event['id'])['revision'])
        revision = store.inspect(event['id'])['revision']
        competition.change_state(event['id'], 'closed', revision)
        preview = publication.preview(event['id'])
        snapshot = publication.publish(event['id'], preview['head_version'],
                                       preview['event']['revision'])
        assert snapshot['scores']['complete']
        assert result['accepted']
        assert publication.get(event['id']) == snapshot
        print(json.dumps({'event_id': event['id'], 'version': snapshot['version'],
                          'groups': len(snapshot['scores']['groups']),
                          'visits': snapshot['services']['total_visits'],
                          'accepted': len(result['accepted']),
                          'rejected': len(result['rejected'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
