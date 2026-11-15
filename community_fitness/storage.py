import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import DomainError


SCHEMA = '''
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, starts_at TEXT NOT NULL,
 ends_at TEXT NOT NULL, timezone TEXT NOT NULL, state TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
 event_id TEXT NOT NULL, code TEXT NOT NULL, label TEXT NOT NULL,
 rubric TEXT NOT NULL, min_judges INTEGER NOT NULL, max_judges INTEGER NOT NULL,
 PRIMARY KEY(event_id,code), FOREIGN KEY(event_id) REFERENCES events(id)
);
CREATE TABLE IF NOT EXISTS teams (
 event_id TEXT NOT NULL, id TEXT NOT NULL, group_code TEXT NOT NULL,
 name TEXT NOT NULL, district TEXT NOT NULL, member_count INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'registered', registered_at TEXT NOT NULL,
 PRIMARY KEY(event_id,id), UNIQUE(event_id,group_code,name),
 FOREIGN KEY(event_id,group_code) REFERENCES groups(event_id,code)
);
CREATE TABLE IF NOT EXISTS judges (
 event_id TEXT NOT NULL, id TEXT NOT NULL, display_name TEXT NOT NULL,
 group_code TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(event_id,id), FOREIGN KEY(event_id,group_code) REFERENCES groups(event_id,code)
);
CREATE TABLE IF NOT EXISTS score_cards (
 event_id TEXT NOT NULL, team_id TEXT NOT NULL, judge_id TEXT NOT NULL,
 revision INTEGER NOT NULL, scores TEXT NOT NULL, total TEXT NOT NULL,
 submitted_at TEXT NOT NULL, request_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
 PRIMARY KEY(event_id,team_id,judge_id), UNIQUE(event_id,request_key),
 FOREIGN KEY(event_id,team_id) REFERENCES teams(event_id,id),
 FOREIGN KEY(event_id,judge_id) REFERENCES judges(event_id,id)
);
CREATE TABLE IF NOT EXISTS score_history (
 event_id TEXT NOT NULL, team_id TEXT NOT NULL, judge_id TEXT NOT NULL,
 revision INTEGER NOT NULL, scores TEXT NOT NULL, total TEXT NOT NULL,
 submitted_at TEXT NOT NULL, request_key TEXT NOT NULL,
 PRIMARY KEY(event_id,team_id,judge_id,revision)
);
CREATE TABLE IF NOT EXISTS score_requests (
 event_id TEXT NOT NULL, request_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
 result TEXT NOT NULL, PRIMARY KEY(event_id,request_key)
);
CREATE TABLE IF NOT EXISTS score_sessions (
 event_id TEXT NOT NULL, group_code TEXT NOT NULL, opened_at TEXT NOT NULL,
 closed_at TEXT, state TEXT NOT NULL, expected_event_revision INTEGER NOT NULL,
 PRIMARY KEY(event_id,group_code), FOREIGN KEY(event_id,group_code) REFERENCES groups(event_id,code)
);
CREATE TABLE IF NOT EXISTS service_zones (
 event_id TEXT NOT NULL, code TEXT NOT NULL, label TEXT NOT NULL,
 kind TEXT NOT NULL, opens_at TEXT NOT NULL, closes_at TEXT NOT NULL,
 PRIMARY KEY(event_id,code), FOREIGN KEY(event_id) REFERENCES events(id)
);
CREATE TABLE IF NOT EXISTS attendance (
 event_id TEXT NOT NULL, team_id TEXT NOT NULL, member_ref TEXT NOT NULL,
 gate TEXT NOT NULL, arrived_at TEXT NOT NULL, request_key TEXT NOT NULL,
 PRIMARY KEY(event_id,team_id,member_ref), UNIQUE(event_id,request_key),
 FOREIGN KEY(event_id,team_id) REFERENCES teams(event_id,id)
);
CREATE TABLE IF NOT EXISTS score_disputes (
 event_id TEXT NOT NULL, id TEXT NOT NULL, team_id TEXT NOT NULL,
 card_judge_id TEXT NOT NULL, card_revision INTEGER NOT NULL, reason TEXT NOT NULL,
 state TEXT NOT NULL, resolution TEXT, opened_at TEXT NOT NULL, closed_at TEXT,
 PRIMARY KEY(event_id,id), FOREIGN KEY(event_id,team_id) REFERENCES teams(event_id,id)
);
CREATE TABLE IF NOT EXISTS encounters (
 event_id TEXT NOT NULL, id TEXT NOT NULL, zone_code TEXT NOT NULL,
 occurred_at TEXT NOT NULL, visitor_token TEXT NOT NULL, count INTEGER NOT NULL,
 attributes TEXT NOT NULL, recorded_at TEXT NOT NULL,
 PRIMARY KEY(event_id,id), FOREIGN KEY(event_id,zone_code) REFERENCES service_zones(event_id,code)
);
CREATE TABLE IF NOT EXISTS ingest_requests (
 event_id TEXT NOT NULL, request_key TEXT NOT NULL, kind TEXT NOT NULL,
 payload_hash TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(event_id,request_key)
);
CREATE TABLE IF NOT EXISTS snapshots (
 event_id TEXT NOT NULL, version INTEGER NOT NULL, source_revision INTEGER NOT NULL,
 created_at TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(event_id,version), FOREIGN KEY(event_id) REFERENCES events(id)
);
CREATE TABLE IF NOT EXISTS audit (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL,
 action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
 detail TEXT NOT NULL, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_encounters_zone_time ON encounters(event_id,zone_code,occurred_at);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit(event_id,seq);
CREATE INDEX IF NOT EXISTS idx_attendance_team ON attendance(event_id,team_id);
CREATE INDEX IF NOT EXISTS idx_disputes_card ON score_disputes(event_id,team_id,card_judge_id);
'''


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ':memory:':
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    @staticmethod
    def one(db: sqlite3.Connection, query: str, values: tuple = ()) -> dict:
        row = db.execute(query, values).fetchone()
        if row is None:
            raise DomainError('not_found', 'Requested resource does not exist', 404)
        return dict(row)

    @staticmethod
    def many(db: sqlite3.Connection, query: str, values: tuple = ()) -> list[dict]:
        return [dict(row) for row in db.execute(query, values)]

    @staticmethod
    def audit(db: sqlite3.Connection, event_id: str, action: str, entity: str,
              entity_id: str, detail: dict, at: str) -> None:
        db.execute('INSERT INTO audit(event_id,action,entity,entity_id,detail,at) VALUES(?,?,?,?,?,?)',
                   (event_id, action, entity, entity_id, json.dumps(detail, sort_keys=True), at))

    @staticmethod
    def bump(db: sqlite3.Connection, event_id: str) -> int:
        db.execute('UPDATE events SET revision=revision+1 WHERE id=?', (event_id,))
        return Store.one(db, 'SELECT revision FROM events WHERE id=?', (event_id,))['revision']

    def inspect(self, event_id: str) -> dict:
        with self.connection() as db:
            event = self.one(db, 'SELECT * FROM events WHERE id=?', (event_id,))
            event['groups'] = self.many(db, 'SELECT * FROM groups WHERE event_id=? ORDER BY code', (event_id,))
            event['teams'] = self.many(db, 'SELECT * FROM teams WHERE event_id=? ORDER BY id', (event_id,))
            event['zones'] = self.many(db, 'SELECT * FROM service_zones WHERE event_id=? ORDER BY code', (event_id,))
            return event

    def audit_trail(self, event_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        with self.connection() as db:
            self.one(db, 'SELECT id FROM events WHERE id=?', (event_id,))
            rows = self.many(db, 'SELECT * FROM audit WHERE event_id=? AND seq>? ORDER BY seq LIMIT ?',
                             (event_id, after, limit))
            for row in rows:
                row['detail'] = json.loads(row['detail'])
            return rows
