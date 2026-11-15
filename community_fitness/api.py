import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .analytics import Analytics
from .attendance import Attendance
from .competition import Competition
from .disputes import Disputes
from .errors import DomainError
from .publication import Publication
from .provenance import Provenance
from .reconciliation import Reconciliation
from .readiness import Readiness
from .scoring_sessions import ScoringSessions
from .services import Services
from .storage import Store


class EventInput(BaseModel):
    id: str
    name: str
    starts_at: str
    ends_at: str
    timezone: str


class GroupInput(BaseModel):
    code: str
    label: str
    criteria: dict[str, Any]
    min_judges: int
    max_judges: int


class TeamInput(BaseModel):
    id: str
    group_code: str
    name: str
    district: str
    member_count: int


class JudgeInput(BaseModel):
    id: str
    name: str
    group_code: str


class StateInput(BaseModel):
    target: str
    expected_revision: int


class ScoreInput(BaseModel):
    team_id: str
    judge_id: str
    scores: dict[str, Any]
    request_key: str
    expected_revision: int | None = None


class ZoneInput(BaseModel):
    code: str
    label: str
    kind: str
    opens_at: str
    closes_at: str


class EncounterInput(BaseModel):
    id: str
    zone_code: str
    occurred_at: str
    visitor_token: str
    count: int
    attributes: dict = Field(default_factory=dict)


class BatchInput(BaseModel):
    request_key: str
    rows: list[dict]


class PublishInput(BaseModel):
    expected_head: int
    expected_revision: int


class CheckInInput(BaseModel):
    team_id: str
    member_ref: str
    gate: str
    arrived_at: str
    request_key: str


class DisputeInput(BaseModel):
    id: str
    team_id: str
    judge_id: str
    reason: str


class ResolutionInput(BaseModel):
    resolution: str
    note: str


class SessionInput(BaseModel):
    expected_event_revision: int


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = db_path or os.environ.get('FITNESS_DB', 'fitness.sqlite3')
    store = Store(path)
    competition = Competition(store)
    services = Services(store)
    analytics = Analytics(store)
    publication = Publication(store)
    provenance = Provenance(store)
    attendance = Attendance(store)
    disputes = Disputes(store)
    reconciliation = Reconciliation(store)
    readiness = Readiness(store)
    sessions = ScoringSessions(store)
    application = FastAPI(title='Community Fitness Event Data', version='1.0.0')
    application.state.store = store

    @application.exception_handler(DomainError)
    async def domain_error(_, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={'error': exc.code, 'message': exc.message})

    @application.get('/health')
    def health():
        return {'status': 'ok'}

    @application.post('/events', status_code=201)
    def create_event(body: EventInput):
        return competition.create_event(body.id, body.name, body.starts_at,
                                        body.ends_at, body.timezone)

    @application.get('/events/{event_id}')
    def get_event(event_id: str):
        return store.inspect(event_id)

    @application.post('/events/{event_id}/groups', status_code=201)
    def add_group(event_id: str, body: GroupInput):
        return competition.add_group(event_id, body.code, body.label, body.criteria,
                                     body.min_judges, body.max_judges)

    @application.post('/events/{event_id}/teams', status_code=201)
    def register_team(event_id: str, body: TeamInput):
        return competition.register_team(event_id, body.id, body.group_code, body.name,
                                         body.district, body.member_count)

    @application.post('/events/{event_id}/judges', status_code=201)
    def assign_judge(event_id: str, body: JudgeInput):
        return competition.assign_judge(event_id, body.id, body.name, body.group_code)

    @application.post('/events/{event_id}/state')
    def change_state(event_id: str, body: StateInput):
        return competition.change_state(event_id, body.target, body.expected_revision)

    @application.post('/events/{event_id}/scores')
    def submit_score(event_id: str, body: ScoreInput):
        return competition.submit_score(event_id, body.team_id, body.judge_id,
                                        body.scores, body.request_key, body.expected_revision)

    @application.post('/events/{event_id}/groups/{group_code}/session')
    def open_scoring(event_id: str, group_code: str, body: SessionInput):
        return sessions.open(event_id, group_code, body.expected_event_revision)

    @application.post('/events/{event_id}/groups/{group_code}/session/close')
    def close_scoring(event_id: str, group_code: str, body: SessionInput):
        return sessions.close(event_id, group_code, body.expected_event_revision)

    @application.get('/events/{event_id}/groups/{group_code}/session')
    def scoring_session(event_id: str, group_code: str):
        return sessions.get(event_id, group_code)

    @application.get('/events/{event_id}/sessions')
    def scoring_sessions(event_id: str):
        return sessions.list(event_id)

    @application.get('/events/{event_id}/scores')
    def list_scores(event_id: str, team_id: str | None = None):
        return competition.cards(event_id, team_id)

    @application.get('/events/{event_id}/scores/{team_id}/{judge_id}/history')
    def score_history(event_id: str, team_id: str, judge_id: str):
        return competition.score_history(event_id, team_id, judge_id)

    @application.get('/events/{event_id}/teams/{team_id}/provenance')
    def team_provenance(event_id: str, team_id: str):
        return provenance.team(event_id, team_id)

    @application.get('/events/{event_id}/encounters/{encounter_id}/provenance')
    def encounter_provenance(event_id: str, encounter_id: str):
        return provenance.encounter(event_id, encounter_id)

    @application.get('/events/{event_id}/snapshots/{version}/provenance')
    def snapshot_provenance(event_id: str, version: int):
        return provenance.snapshot(event_id, version)

    @application.post('/events/{event_id}/zones', status_code=201)
    def add_zone(event_id: str, body: ZoneInput):
        return services.add_zone(event_id, body.code, body.label, body.kind,
                                 body.opens_at, body.closes_at)

    @application.post('/events/{event_id}/encounters', status_code=201)
    def record_encounter(event_id: str, body: EncounterInput):
        return services.record_encounter(event_id, body.id, body.zone_code,
                                         body.occurred_at, body.visitor_token,
                                         body.count, body.attributes)

    @application.post('/events/{event_id}/encounter-batches')
    def ingest_batch(event_id: str, body: BatchInput):
        return services.ingest_batch(event_id, body.request_key, body.rows)

    @application.get('/events/{event_id}/encounters')
    def encounters(event_id: str, zone_code: str | None = None, after: str | None = None,
                   limit: int = Query(100, ge=1, le=500)):
        return services.encounters(event_id, zone_code, after, limit)

    @application.post('/events/{event_id}/attendance', status_code=201)
    def check_in(event_id: str, body: CheckInInput):
        return attendance.check_in(event_id, body.team_id, body.member_ref,
                                   body.gate, body.arrived_at, body.request_key)

    @application.get('/events/{event_id}/attendance')
    def attendance_summary(event_id: str):
        return attendance.summary(event_id)

    @application.get('/events/{event_id}/attendance/{team_id}')
    def team_attendance(event_id: str, team_id: str):
        return attendance.team(event_id, team_id)

    @application.post('/events/{event_id}/disputes', status_code=201)
    def open_dispute(event_id: str, body: DisputeInput):
        return disputes.open(event_id, body.id, body.team_id, body.judge_id, body.reason)

    @application.post('/events/{event_id}/disputes/{dispute_id}/resolution')
    def resolve_dispute(event_id: str, dispute_id: str, body: ResolutionInput):
        return disputes.resolve(event_id, dispute_id, body.resolution, body.note)

    @application.get('/events/{event_id}/disputes')
    def list_disputes(event_id: str):
        return disputes.list_disputes(event_id)

    @application.get('/events/{event_id}/preview')
    def preview(event_id: str):
        return publication.preview(event_id)

    @application.get('/events/{event_id}/quality')
    def quality(event_id: str):
        return analytics.quality(event_id)

    @application.get('/events/{event_id}/reconciliation')
    def reconciliation_report(event_id: str):
        return reconciliation.event_health(event_id)

    @application.get('/events/{event_id}/readiness')
    def readiness_report(event_id: str):
        return readiness.report(event_id)

    @application.get('/events/{event_id}/audit/verify')
    def audit_verify(event_id: str):
        return reconciliation.audit_verify(event_id)

    @application.get('/events/{event_id}/timeline')
    def local_timeline(event_id: str):
        return reconciliation.local_timeline(event_id)

    @application.post('/events/{event_id}/publish')
    def publish(event_id: str, body: PublishInput):
        return publication.publish(event_id, body.expected_head, body.expected_revision)

    @application.get('/events/{event_id}/snapshots')
    def snapshots(event_id: str):
        return publication.versions(event_id)

    @application.get('/events/{event_id}/snapshots/head')
    def head(event_id: str):
        return publication.get(event_id)

    @application.get('/events/{event_id}/snapshots/{version}')
    def snapshot(event_id: str, version: int):
        return publication.get(event_id, version)

    @application.get('/events/{event_id}/snapshots-compare')
    def difference(event_id: str, older: int, newer: int):
        return publication.difference(event_id, older, newer)

    @application.get('/events/{event_id}/export/standings.csv')
    def standings_csv(event_id: str, version: int | None = None):
        payload = publication.get(event_id, version)
        return PlainTextResponse(analytics.standings_csv(payload), media_type='text/csv; charset=utf-8')

    @application.get('/events/{event_id}/export/services.csv')
    def service_csv(event_id: str, version: int | None = None):
        payload = publication.get(event_id, version)
        return PlainTextResponse(analytics.service_csv(payload), media_type='text/csv; charset=utf-8')

    @application.get('/events/{event_id}/audit')
    def audit(event_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return store.audit_trail(event_id, after, limit)

    return application


app = create_app()
