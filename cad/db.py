"""SQLite storage layer: schema, connections, transactions, tamper-evident audit log."""
import hashlib
import os
import sqlite3
import threading
from contextlib import contextmanager

from .util import jdump, now_iso

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Every record carries agency_id so the same database/schema can later host
-- multiple agencies (regional or statewide deployment).
CREATE TABLE IF NOT EXISTS agencies (
    id         INTEGER PRIMARY KEY,
    ori        TEXT,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,
    agency_id       INTEGER NOT NULL REFERENCES agencies(id),
    username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name       TEXT NOT NULL,
    badge           TEXT,
    role            TEXT NOT NULL CHECK (role IN ('admin','supervisor','dispatcher','officer','records')),
    pw_hash         TEXT NOT NULL,
    must_change_pw  INTEGER NOT NULL DEFAULT 1,
    totp_secret     TEXT,
    totp_pending    TEXT,
    totp_enabled    INTEGER NOT NULL DEFAULT 0,
    totp_last_step  INTEGER NOT NULL DEFAULT 0,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    last_login_at   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    csrf       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    ip         TEXT,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS call_types (
    id               INTEGER PRIMARY KEY,
    agency_id        INTEGER NOT NULL REFERENCES agencies(id),
    code             TEXT NOT NULL,
    description      TEXT NOT NULL,
    default_priority INTEGER NOT NULL DEFAULT 3,
    active           INTEGER NOT NULL DEFAULT 1,
    UNIQUE (agency_id, code)
);

CREATE TABLE IF NOT EXISTS dispositions (
    id          INTEGER PRIMARY KEY,
    agency_id   INTEGER NOT NULL REFERENCES agencies(id),
    code        TEXT NOT NULL,
    description TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (agency_id, code)
);

CREATE TABLE IF NOT EXISTS units (
    id              INTEGER PRIMARY KEY,
    agency_id       INTEGER NOT NULL REFERENCES agencies(id),
    unit_code       TEXT NOT NULL,
    description     TEXT,
    unit_type       TEXT NOT NULL DEFAULT 'PATROL',
    status          TEXT NOT NULL DEFAULT 'OFF_DUTY',
    status_at       TEXT,
    current_call_id INTEGER REFERENCES calls(id),
    officer_user_id INTEGER REFERENCES users(id),
    officer_name    TEXT,
    location_note   TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    UNIQUE (agency_id, unit_code)
);

CREATE TABLE IF NOT EXISTS calls (
    id               INTEGER PRIMARY KEY,
    agency_id        INTEGER NOT NULL REFERENCES agencies(id),
    call_number      TEXT NOT NULL UNIQUE,
    case_number      TEXT,
    status           TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','ACTIVE','CLOSED')),
    received_at      TEXT NOT NULL,
    call_type        TEXT NOT NULL,
    priority         INTEGER NOT NULL DEFAULT 3,
    source           TEXT NOT NULL DEFAULT 'PHONE',
    location_address TEXT,
    location_city    TEXT,
    location_cross   TEXT,
    location_common  TEXT,
    location_lat     REAL,
    location_lon     REAL,
    location_notes   TEXT,
    caller_name      TEXT,
    caller_phone     TEXT,
    caller_address   TEXT,
    summary          TEXT,
    disposition      TEXT,
    closed_at        TEXT,
    closed_by        INTEGER REFERENCES users(id),
    created_by       INTEGER NOT NULL REFERENCES users(id),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_received ON calls(received_at);
CREATE INDEX IF NOT EXISTS idx_calls_addr ON calls(location_address);

-- Append-only chronological log for each call (dispatcher notes + system events).
CREATE TABLE IF NOT EXISTS call_narrative (
    id         INTEGER PRIMARY KEY,
    call_id    INTEGER NOT NULL REFERENCES calls(id),
    at         TEXT NOT NULL,
    user_id    INTEGER REFERENCES users(id),
    username   TEXT,
    entry_type TEXT NOT NULL DEFAULT 'NOTE' CHECK (entry_type IN ('NOTE','SYSTEM')),
    text       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_narr_call ON call_narrative(call_id);

CREATE TABLE IF NOT EXISTS call_units (
    id            INTEGER PRIMARY KEY,
    call_id       INTEGER NOT NULL REFERENCES calls(id),
    unit_id       INTEGER NOT NULL REFERENCES units(id),
    unit_code     TEXT NOT NULL,
    officer_name  TEXT,
    is_primary    INTEGER NOT NULL DEFAULT 0,
    dispatched_at TEXT,
    enroute_at    TEXT,
    onscene_at    TEXT,
    transport_at  TEXT,
    cleared_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_cu_call ON call_units(call_id);

CREATE TABLE IF NOT EXISTS unit_status_log (
    id        INTEGER PRIMARY KEY,
    unit_id   INTEGER NOT NULL REFERENCES units(id),
    unit_code TEXT NOT NULL,
    call_id   INTEGER REFERENCES calls(id),
    status    TEXT NOT NULL,
    at        TEXT NOT NULL,
    user_id   INTEGER REFERENCES users(id),
    note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_usl_unit ON unit_status_log(unit_id);

CREATE TABLE IF NOT EXISTS call_persons (
    id          INTEGER PRIMARY KEY,
    call_id     INTEGER NOT NULL REFERENCES calls(id),
    role        TEXT NOT NULL,
    last_name   TEXT,
    first_name  TEXT,
    middle_name TEXT,
    suffix      TEXT,
    dob         TEXT,
    sex         TEXT,
    race        TEXT,
    address     TEXT,
    city        TEXT,
    state       TEXT,
    zip         TEXT,
    phone       TEXT,
    dl_number   TEXT,
    dl_state    TEXT,
    height      TEXT,
    weight      TEXT,
    notes       TEXT,
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_persons_call ON call_persons(call_id);
CREATE INDEX IF NOT EXISTS idx_persons_name ON call_persons(last_name, first_name);

CREATE TABLE IF NOT EXISTS call_vehicles (
    id          INTEGER PRIMARY KEY,
    call_id     INTEGER NOT NULL REFERENCES calls(id),
    role        TEXT NOT NULL,
    plate       TEXT,
    plate_state TEXT,
    year        TEXT,
    make        TEXT,
    model       TEXT,
    style       TEXT,
    color       TEXT,
    vin         TEXT,
    notes       TEXT,
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_veh_call ON call_vehicles(call_id);
CREATE INDEX IF NOT EXISTS idx_veh_plate ON call_vehicles(plate);

-- Tracks which version of each call has been copied to the archive folder.
CREATE TABLE IF NOT EXISTS archive_state (
    call_id     INTEGER PRIMARY KEY REFERENCES calls(id),
    version     INTEGER NOT NULL,
    archived_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
    agency_id INTEGER NOT NULL,
    year      INTEGER NOT NULL,
    kind      TEXT NOT NULL,
    value     INTEGER NOT NULL,
    PRIMARY KEY (agency_id, year, kind)
);

-- Tamper-evident audit trail: each row's hash covers the previous row's hash.
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY,
    at        TEXT NOT NULL,
    user_id   INTEGER,
    username  TEXT,
    ip        TEXT,
    action    TEXT NOT NULL,
    entity    TEXT,
    entity_id TEXT,
    detail    TEXT,
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity, entity_id);

CREATE TRIGGER IF NOT EXISTS narrative_no_update BEFORE UPDATE ON call_narrative
BEGIN SELECT RAISE(ABORT, 'call narrative is append-only'); END;
CREATE TRIGGER IF NOT EXISTS narrative_no_delete BEFORE DELETE ON call_narrative
BEGIN SELECT RAISE(ABORT, 'call narrative is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS calls_no_delete BEFORE DELETE ON calls
BEGIN SELECT RAISE(ABORT, 'calls cannot be deleted'); END;
"""

GENESIS_HASH = "0" * 64


def audit_hash(prev_hash, at, user_id, username, ip, action, entity, entity_id, detail):
    material = "|".join(
        "" if v is None else str(v)
        for v in (prev_hash, at, user_id, username, ip, action, entity, entity_id, detail)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.RLock()

    def connect(self):
        c = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA busy_timeout = 10000")
        return c

    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self.connect()
            self._local.conn = c
        return c

    def close_thread_conn(self):
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    @contextmanager
    def tx(self):
        """Serialized write transaction. Bumps the change sequence used for live refresh."""
        with self.write_lock:
            c = self.conn()
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'seq'")
                c.execute("COMMIT")
            except BaseException as e:
                # Errors flagged commit_anyway (e.g. failed-login counters + audit) keep their writes.
                if getattr(e, "commit_anyway", False):
                    c.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'seq'")
                    c.execute("COMMIT")
                else:
                    c.execute("ROLLBACK")
                raise

    def init_schema(self):
        c = self.conn()
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('seq', '0')")
        self._migrate(c)

    @staticmethod
    def _migrate(c):
        """Additive column migrations for databases created by earlier versions."""
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        if "enabled_at" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN enabled_at TEXT")

    def seq(self):
        return int(self.conn().execute("SELECT value FROM meta WHERE key='seq'").fetchone()[0])

    # ---- audit -------------------------------------------------------------
    @staticmethod
    def audit(c, user, ip, action, entity=None, entity_id=None, detail=None):
        """Append an audit record. Must be called inside tx() (write lock held)."""
        prev = c.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = prev[0] if prev else GENESIS_HASH
        at = now_iso()
        user_id = user["id"] if user else None
        username = user["username"] if user else None
        detail_s = jdump(detail) if detail is not None else None
        eid = None if entity_id is None else str(entity_id)
        h = audit_hash(prev_hash, at, user_id, username, ip, action, entity, eid, detail_s)
        c.execute(
            "INSERT INTO audit_log(at,user_id,username,ip,action,entity,entity_id,detail,prev_hash,hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (at, user_id, username, ip, action, entity, eid, detail_s, prev_hash, h),
        )

    def verify_audit(self):
        """Walk the audit chain. Returns (ok, rows_checked, first_bad_id)."""
        prev = GENESIS_HASH
        n = 0
        for r in self.conn().execute("SELECT * FROM audit_log ORDER BY id"):
            n += 1
            expect = audit_hash(prev, r["at"], r["user_id"], r["username"], r["ip"], r["action"],
                                r["entity"], r["entity_id"], r["detail"])
            if r["prev_hash"] != prev or r["hash"] != expect:
                return False, n, r["id"]
            prev = r["hash"]
        return True, n, None

    def backup(self, dest_path):
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        dest = sqlite3.connect(dest_path)
        try:
            self.conn().backup(dest)
        finally:
            dest.close()
