"""API handlers: authentication, calls for service, units, records, admin, export."""
import csv
import io
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone

from . import __version__, auth, seed
from .db import SCHEMA_VERSION
from .util import now_iso, parse_iso, row_to_dict, rows_to_list


class ApiError(Exception):
    def __init__(self, status, message, extra=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
_BASE_DISPATCH = {"view", "call.create", "call.edit", "call.close", "narrative",
                  "unit.status.any", "unit.crew", "entity.edit", "time.edit", "alert.send"}
PERMS = {
    "admin": _BASE_DISPATCH | {"call.reopen", "unit.manage", "export", "audit", "admin"},
    "supervisor": _BASE_DISPATCH | {"call.reopen", "unit.manage", "export", "audit"},
    "dispatcher": set(_BASE_DISPATCH),
    "officer": {"view", "call.create", "narrative", "unit.status.own", "entity.edit"},
    "records": {"view", "export"},
}


def can(user, perm):
    return user is not None and perm in PERMS.get(user["role"], set())


def require(req, perm):
    if not can(req.user, perm):
        raise ApiError(403, "You do not have permission for this action.")


# ---------------------------------------------------------------------------
# Field validation helpers
# ---------------------------------------------------------------------------
def s(body, key, maxlen=500, required=False, upper=False):
    v = body.get(key)
    if v is None or (isinstance(v, str) and v.strip() == ""):
        if required:
            raise ApiError(400, f"'{key}' is required.")
        return None
    if not isinstance(v, (str, int, float)):
        raise ApiError(400, f"'{key}' must be text.")
    v = str(v).strip()
    if len(v) > maxlen:
        raise ApiError(400, f"'{key}' is too long (max {maxlen}).")
    return v.upper() if upper else v


def num(body, key, lo=None, hi=None, required=False, integer=False):
    v = body.get(key)
    if v is None or v == "":
        if required:
            raise ApiError(400, f"'{key}' is required.")
        return None
    try:
        v = int(v) if integer else float(v)
    except (TypeError, ValueError):
        raise ApiError(400, f"'{key}' must be a number.")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ApiError(400, f"'{key}' is out of range.")
    return v


def ts(body, key, required=False):
    v = body.get(key)
    if v in (None, ""):
        if required:
            raise ApiError(400, f"'{key}' is required.")
        return None
    try:
        out = parse_iso(v)
    except (TypeError, ValueError):
        raise ApiError(400, f"'{key}' must be a date/time.")
    # Reject timestamps more than 10 minutes in the future (clock / typo guard).
    if datetime.fromisoformat(out.replace("Z", "+00:00")) > datetime.now(timezone.utc) + timedelta(minutes=10):
        raise ApiError(400, f"'{key}' cannot be in the future.")
    return out


def one_of(value, allowed, key):
    if value not in allowed:
        raise ApiError(400, f"'{key}' must be one of: {', '.join(map(str, allowed))}.")
    return value


def like(v):
    return "%" + v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def local_year(iso):
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return dt.year


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------
def public_user(u):
    return {k: u[k] for k in ("id", "username", "full_name", "badge", "role", "active",
                              "totp_enabled", "must_change_pw", "last_login_at", "created_at")} | {
        "locked": bool(u["locked_until"] and u["locked_until"] > now_iso())}


def get_call(c, req, call_id):
    r = c.execute("SELECT * FROM calls WHERE id=? AND agency_id=?", (call_id, req.app.agency_id)).fetchone()
    if not r:
        raise ApiError(404, "Call not found.")
    return row_to_dict(r)


def get_unit(c, req, unit_id):
    r = c.execute("SELECT * FROM units WHERE id=? AND agency_id=?", (unit_id, req.app.agency_id)).fetchone()
    if not r:
        raise ApiError(404, "Unit not found.")
    return row_to_dict(r)


def add_narrative(c, req, call_id, text, entry_type="NOTE", at=None):
    c.execute(
        "INSERT INTO call_narrative(call_id, at, user_id, username, entry_type, text) VALUES (?,?,?,?,?,?)",
        (call_id, at or now_iso(), req.user["id"] if req.user else None,
         req.user["username"] if req.user else None, entry_type, text),
    )


def touch_call(c, call_id):
    c.execute("UPDATE calls SET updated_at=?, version=version+1 WHERE id=?", (now_iso(), call_id))


def next_number(c, agency_id, year, kind):
    c.execute(
        "INSERT INTO counters(agency_id, year, kind, value) VALUES (?,?,?,1)"
        " ON CONFLICT(agency_id, year, kind) DO UPDATE SET value = value + 1",
        (agency_id, year, kind),
    )
    return c.execute("SELECT value FROM counters WHERE agency_id=? AND year=? AND kind=?",
                     (agency_id, year, kind)).fetchone()[0]


def format_number(config, kind, year, n):
    """Render a call/case number from the configured pattern, e.g. '{yy}MPD{seq:04d}' -> 26MPD0001.
    Past 9999 a 4-digit pattern simply grows to 5 digits (26MPD10000); numbers are never truncated."""
    return config.get("agency", f"{kind}_number_format").format(
        yy=f"{year % 100:02d}", yyyy=str(year), seq=n,
        call_prefix=config.get("agency", "call_prefix"), case_prefix=config.get("agency", "case_prefix"))


def validate_number_formats(config):
    for kind in ("call", "case"):
        try:
            a, b = format_number(config, kind, 2026, 1), format_number(config, kind, 2026, 2)
        except (KeyError, ValueError, IndexError) as e:
            raise SystemExit(f"Invalid {kind}_number_format in config.ini: {e}. "
                             "Allowed placeholders: {yy} {yyyy} {seq} {call_prefix} {case_prefix}")
        if a == b:
            raise SystemExit(f"{kind}_number_format must include {{seq}}.")


def issue_number(c, app, year, kind):
    """Next unused number of this kind. Skips any number already in use (e.g. after the counter was
    set manually), so a duplicate can never be issued."""
    col = "call_number" if kind == "call" else "case_number"
    while True:
        num_ = format_number(app.config, kind, year, next_number(c, app.agency_id, year, kind))
        if not c.execute(f"SELECT 1 FROM calls WHERE {col}=?", (num_,)).fetchone():
            return num_


def ref_codes(c, req, table):
    return {r[0] for r in c.execute(f"SELECT code FROM {table} WHERE agency_id=?", (req.app.agency_id,))}


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------
def login(req):
    cfg = req.app.config
    username = s(req.body, "username", 64, required=True)
    password = req.body.get("password") or ""
    code = s(req.body, "totp", 10)
    db = req.app.db
    with db.tx() as c:
        u = c.execute("SELECT * FROM users WHERE username=? AND agency_id=?",
                      (username, req.app.agency_id)).fetchone()
        if not u or not u["active"]:
            auth.dummy_verify(password)
            db.audit(c, None, req.ip, "LOGIN_FAIL", "user", None, {"username": username, "reason": "unknown/inactive"})
            raise _Deferred(401, "Invalid username or password.")
        u = row_to_dict(u)
        days = cfg.getint("security", "inactive_disable_days")
        if days > 0:
            last_use = max(x for x in (u["last_login_at"], u.get("enabled_at"), u["created_at"]) if x)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
            if last_use < cutoff:
                c.execute("UPDATE users SET active=0, updated_at=? WHERE id=?", (now_iso(), u["id"]))
                c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
                db.audit(c, u, req.ip, "USER_AUTO_DISABLED", "user", u["id"], {"inactive_days": days})
                raise _Deferred(401, "Invalid username or password.")
        if u["locked_until"] and u["locked_until"] > now_iso():
            db.audit(c, u, req.ip, "LOGIN_FAIL", "user", u["id"], {"reason": "locked"})
            raise _Deferred(423, "Account is locked due to failed attempts. Try later or contact an administrator.")

        def fail(reason):
            attempts = u["failed_attempts"] + 1
            locked = None
            if attempts >= cfg.getint("security", "lockout_attempts"):
                locked = (datetime.now(timezone.utc) + timedelta(minutes=cfg.getint("security", "lockout_minutes"))
                          ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                attempts = 0
            c.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?", (attempts, locked, u["id"]))
            db.audit(c, u, req.ip, "LOGIN_FAIL", "user", u["id"], {"reason": reason, "locked": bool(locked)})

        if not auth.verify_password(password, u["pw_hash"]):
            fail("bad password")
            raise _Deferred(401, "Invalid username or password.")
        if u["totp_enabled"]:
            if not code:
                return {"mfa_required": True}
            step = auth.verify_totp(u["totp_secret"], code, u["totp_last_step"])
            if step is None:
                fail("bad mfa code")
                raise _Deferred(401, "Invalid authentication code.")
            c.execute("UPDATE users SET totp_last_step=? WHERE id=?", (step, u["id"]))
        token = auth.new_session_token()
        csrf = auth.new_session_token()
        now = now_iso()
        c.execute("INSERT INTO sessions(token_hash,user_id,csrf,created_at,last_seen,ip,user_agent) VALUES (?,?,?,?,?,?,?)",
                  (auth.token_hash(token), u["id"], csrf, now, now, req.ip, (req.user_agent or "")[:300]))
        c.execute("UPDATE users SET failed_attempts=0, locked_until=NULL, last_login_at=? WHERE id=?", (now, u["id"]))
        db.audit(c, u, req.ip, "LOGIN", "user", u["id"], {"mfa": bool(u["totp_enabled"])})
    req.set_cookie = token
    return {"ok": True}


class _Deferred(ApiError):
    """An error raised inside a transaction whose writes (failure counters, audit) must still commit."""
    commit_anyway = True


def logout(req):
    with req.app.db.tx() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (req.session["token_hash"],))
        req.app.db.audit(c, req.user, req.ip, "LOGOUT", "user", req.user["id"])
    req.clear_cookie = True
    return {"ok": True}


def me(req):
    u = req.user
    cfg = req.app.config
    return {
        "user": public_user(u),
        "csrf": req.session["csrf"],
        "permissions": sorted(PERMS.get(u["role"], set())),
        "must_change_pw": bool(u["must_change_pw"]),
        "mfa_setup_required": cfg.getbool("security", "require_mfa") and not u["totp_enabled"],
        "agency": {"name": cfg.get("agency", "name"), "ori": cfg.get("agency", "ori")},
        "idle_minutes": cfg.getint("security", "session_idle_minutes"),
        "password_min_length": cfg.getint("security", "password_min_length"),
        "version": __version__,
        "previous_logon": previous_logon(req.app.db.conn(), u["id"]),
    }


def previous_logon(c, user_id):
    """CJIS previous-logon notification: last successful sign-in and failed attempts since then."""
    logins = c.execute("SELECT id, at, ip FROM audit_log WHERE action='LOGIN' AND entity='user' AND entity_id=?"
                       " ORDER BY id DESC LIMIT 2", (str(user_id),)).fetchall()
    if not logins:
        return None
    current = logins[0]["id"]
    prev = logins[1] if len(logins) > 1 else None
    fails = c.execute("SELECT count(*) FROM audit_log WHERE action='LOGIN_FAIL' AND entity='user' AND entity_id=?"
                      " AND id > ? AND id < ?", (str(user_id), prev["id"] if prev else 0, current)).fetchone()[0]
    return {"at": prev["at"] if prev else None, "ip": prev["ip"] if prev else None, "failed_since": fails}


def change_password(req):
    current = req.body.get("current_password") or ""
    new = req.body.get("new_password") or ""
    u = req.user
    if not auth.verify_password(current, u["pw_hash"]):
        raise ApiError(400, "Current password is incorrect.")
    if current == new:
        raise ApiError(400, "New password must be different.")
    problems = auth.password_problems(new, req.app.config.getint("security", "password_min_length"), u["username"])
    if problems:
        raise ApiError(400, "Password " + "; ".join(problems) + ".")
    with req.app.db.tx() as c:
        c.execute("UPDATE users SET pw_hash=?, must_change_pw=0, updated_at=? WHERE id=?",
                  (auth.hash_password(new), now_iso(), u["id"]))
        # End all other sessions for this user.
        c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (u["id"], req.session["token_hash"]))
        req.app.db.audit(c, u, req.ip, "PASSWORD_CHANGE", "user", u["id"])
    return {"ok": True}


def mfa_begin(req):
    secret = auth.new_totp_secret()
    with req.app.db.tx() as c:
        c.execute("UPDATE users SET totp_pending=? WHERE id=?", (secret, req.user["id"]))
    issuer = req.app.config.get("agency", "name") + " CAD"
    return {"secret": secret, "uri": auth.totp_uri(secret, req.user["username"], issuer)}


def mfa_confirm(req):
    code = s(req.body, "code", 10, required=True)
    u = req.user
    if not u["totp_pending"]:
        raise ApiError(400, "Start MFA setup first.")
    step = auth.verify_totp(u["totp_pending"], code)
    if step is None:
        raise ApiError(400, "Code did not match. Check the time on your phone and try again.")
    with req.app.db.tx() as c:
        c.execute("UPDATE users SET totp_secret=totp_pending, totp_pending=NULL, totp_enabled=1, totp_last_step=?,"
                  " updated_at=? WHERE id=?", (step, now_iso(), u["id"]))
        req.app.db.audit(c, u, req.ip, "MFA_ENABLED", "user", u["id"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Reference data & board
# ---------------------------------------------------------------------------
def reference(req):
    c = req.app.db.conn()
    aid = req.app.agency_id
    return {
        "call_types": rows_to_list(c.execute(
            "SELECT * FROM call_types WHERE agency_id=? ORDER BY code", (aid,))),
        "dispositions": rows_to_list(c.execute(
            "SELECT * FROM dispositions WHERE agency_id=? ORDER BY code", (aid,))),
        "priorities": seed.PRIORITIES,
        "sources": seed.SOURCES,
        "unit_statuses": seed.UNIT_STATUSES,
        "call_statuses": sorted(seed.CALL_STATUSES),
        "unit_types": seed.UNIT_TYPES,
        "unit_type_discipline": seed.UNIT_TYPE_DISCIPLINE,
        "disciplines": seed.DISCIPLINES,
        "active911": req.app.alerter.public_status() if getattr(req.app, "alerter", None) else {"enabled": False},
        "person_roles": seed.PERSON_ROLES,
        "vehicle_roles": seed.VEHICLE_ROLES,
        "roles": seed.ROLES,
    }


BOARD_UNITS_SQL = """
SELECT u.*, c.call_number AS current_call_number, c.call_type AS current_call_type,
       c.location_address AS current_call_address
FROM units u LEFT JOIN calls c ON c.id = u.current_call_id
WHERE u.agency_id=? AND u.active=1
ORDER BY CASE u.status WHEN 'OFF_DUTY' THEN 1 ELSE 0 END, u.unit_code
"""


def board(req):
    require(req, "view")
    db = req.app.db
    seq = db.seq()
    since = req.query.get("since")
    if since is not None and since.isdigit() and int(since) == seq:
        return {"seq": seq, "changed": False}
    c = db.conn()
    aid = req.app.agency_id
    hours = 12
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    calls = rows_to_list(c.execute(
        """SELECT c.id, c.call_number, c.case_number, c.status, c.received_at, c.call_type, c.priority,
                  c.source, c.location_address, c.location_city, c.location_common, c.caller_name,
                  c.disposition, c.closed_at, c.updated_at, c.discipline,
                  (SELECT group_concat(unit_code, ' ') FROM call_units cu
                    WHERE cu.call_id = c.id AND cu.cleared_at IS NULL) AS units
           FROM calls c
           WHERE c.agency_id=? AND (c.status <> 'CLOSED' OR c.closed_at >= ?)
           ORDER BY CASE c.status WHEN 'PENDING' THEN 0 WHEN 'ACTIVE' THEN 1 ELSE 2 END,
                    CASE WHEN c.status='CLOSED' THEN NULL ELSE c.priority END, c.received_at DESC""",
        (aid, cutoff)))
    units = rows_to_list(c.execute(BOARD_UNITS_SQL, (aid,)))
    return {"seq": seq, "changed": True, "calls": calls, "units": units, "closed_window_hours": hours}


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------
CALL_TEXT_FIELDS = {
    "location_address": 200, "location_city": 80, "location_cross": 200, "location_common": 200,
    "location_notes": 1000, "caller_name": 150, "caller_phone": 40, "caller_address": 200,
    "summary": 4000, "case_number": 40, "chief_complaint": 200, "destination": 100,
}


def call_type_discipline(c, req, code):
    r = c.execute("SELECT discipline FROM call_types WHERE agency_id=? AND code=?", (req.app.agency_id, code)).fetchone()
    return r[0] if r else "POLICE"


def _call_fields(c, req, body, partial):
    out = {}
    if not partial or "call_type" in body:
        ct = s(body, "call_type", 20, required=True, upper=True)
        if ct not in ref_codes(c, req, "call_types"):
            raise ApiError(400, f"Unknown call type '{ct}'.")
        out["call_type"] = ct
    if not partial or "priority" in body:
        p = num(body, "priority", 1, 5, integer=True)
        if p is not None:
            out["priority"] = p
    if not partial or "source" in body:
        src = s(body, "source", 20, upper=True) or "PHONE"
        out["source"] = one_of(src, seed.SOURCES, "source")
    for f, mx in CALL_TEXT_FIELDS.items():
        if not partial or f in body:
            out[f] = s(body, f, mx)
    for f, lo, hi in (("location_lat", -90, 90), ("location_lon", -180, 180)):
        if not partial or f in body:
            out[f] = num(body, f, lo, hi)
    if not partial or "patient_count" in body:
        out["patient_count"] = num(body, "patient_count", 0, 999, integer=True)
    if body.get("discipline"):
        out["discipline"] = one_of(s(body, "discipline", 10, upper=True), seed.DISCIPLINES, "discipline")
    elif "call_type" in out:
        # Default the discipline from the call type (on create, or when the type changes).
        out["discipline"] = call_type_discipline(c, req, out["call_type"])
    if "received_at" in body and body.get("received_at"):
        if partial:
            require(req, "time.edit")
        out["received_at"] = ts(body, "received_at")
    return out


def create_call(req):
    require(req, "call.create")
    db = req.app.db
    with db.tx() as c:
        f = _call_fields(c, req, req.body, partial=False)
        if f.get("priority") is None:
            f["priority"] = c.execute("SELECT default_priority FROM call_types WHERE agency_id=? AND code=?",
                                      (req.app.agency_id, f["call_type"])).fetchone()[0]
        now = now_iso()
        received = f.pop("received_at", None) or now
        if req.user["role"] == "officer":
            f["source"] = "OFFICER" if not req.body.get("source") else f["source"]
        year = local_year(received)
        call_number = issue_number(c, req.app, year, "call")
        cols = ["agency_id", "call_number", "received_at", "created_by", "created_at", "updated_at"] + list(f)
        vals = [req.app.agency_id, call_number, received, req.user["id"], now, now] + list(f.values())
        cur = c.execute(f"INSERT INTO calls({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        call_id = cur.lastrowid
        add_narrative(c, req, call_id, f"Call created: {f['call_type']} P{f['priority']}"
                      + (f" [{f['discipline']}]" if f.get("discipline", "POLICE") != "POLICE" else "")
                      + (f" (back-entered, received {received})" if received != now else ""), "SYSTEM")
        if s(req.body, "narrative", 8000):
            add_narrative(c, req, call_id, s(req.body, "narrative", 8000))
        db.audit(c, req.user, req.ip, "CALL_CREATE", "call", call_id, {"call_number": call_number, **f})
        # Optional: immediately dispatch units supplied on creation.
        for uid in req.body.get("assign_units") or []:
            unit = get_unit(c, req, int(uid))
            _set_unit_status(c, req, unit, "DISPATCHED", call_id, None)
    return {"id": call_id, "call_number": call_number}


def call_detail(req, call_id):
    require(req, "view")
    db = req.app.db
    c = db.conn()
    call = get_call(c, req, call_id)
    out = {
        "call": call,
        "narrative": rows_to_list(c.execute("SELECT * FROM call_narrative WHERE call_id=? ORDER BY at, id", (call_id,))),
        "units": rows_to_list(c.execute("SELECT * FROM call_units WHERE call_id=? ORDER BY dispatched_at, id", (call_id,))),
        "persons": rows_to_list(c.execute(
            "SELECT * FROM call_persons WHERE call_id=? AND deleted=0 ORDER BY id", (call_id,))),
        "vehicles": rows_to_list(c.execute(
            "SELECT * FROM call_vehicles WHERE call_id=? AND deleted=0 ORDER BY id", (call_id,))),
        "prior_at_address": [],
        "alerts": rows_to_list(c.execute(
            "SELECT id, discipline, kind, status, created_at, sent_at, attempts, last_error, note FROM alerts"
            " WHERE call_id=? ORDER BY id", (call_id,))),
    }
    if call["location_address"]:
        out["prior_at_address"] = rows_to_list(c.execute(
            "SELECT id, call_number, received_at, call_type, disposition FROM calls"
            " WHERE agency_id=? AND id<>? AND location_address = ? COLLATE NOCASE"
            " ORDER BY received_at DESC LIMIT 15", (req.app.agency_id, call_id, call["location_address"])))
    with db.tx() as wc:
        db.audit(wc, req.user, req.ip, "CALL_VIEW", "call", call_id)
    return out


def update_call(req, call_id):
    require(req, "call.edit")
    db = req.app.db
    with db.tx() as c:
        call = get_call(c, req, call_id)
        if call["status"] == "CLOSED" and not can(req.user, "call.reopen"):
            raise ApiError(409, "Call is closed. A supervisor must make changes.")
        ver = req.body.get("version")
        if ver is not None and int(ver) != call["version"]:
            raise ApiError(409, "This call was changed by someone else. Reload and try again.")
        f = _call_fields(c, req, req.body, partial=True)
        changes = {k: {"from": call[k], "to": v} for k, v in f.items() if call[k] != v}
        if not changes:
            return {"ok": True, "version": call["version"]}
        sets = ", ".join(f"{k}=?" for k in changes)
        c.execute(f"UPDATE calls SET {sets} WHERE id=?", [f[k] for k in changes] + [call_id])
        touch_call(c, call_id)
        visible = [k for k in ("call_type", "priority", "discipline", "location_address", "received_at") if k in changes]
        if visible:
            add_narrative(c, req, call_id, "Changed " + "; ".join(
                f"{k.replace('location_', '')}: {changes[k]['from'] or '—'} → {changes[k]['to'] or '—'}"
                for k in visible), "SYSTEM")
        db.audit(c, req.user, req.ip, "CALL_UPDATE", "call", call_id, changes)
        return {"ok": True, "version": call["version"] + 1}


def add_call_note(req, call_id):
    require(req, "narrative")
    text = s(req.body, "text", 8000, required=True)
    db = req.app.db
    with db.tx() as c:
        get_call(c, req, call_id)
        add_narrative(c, req, call_id, text)
        touch_call(c, call_id)
        db.audit(c, req.user, req.ip, "NARRATIVE_ADD", "call", call_id, {"length": len(text)})
    return {"ok": True}


def assign_case_number(req, call_id):
    require(req, "narrative")  # officers request case numbers too
    db = req.app.db
    with db.tx() as c:
        call = get_call(c, req, call_id)
        if call["case_number"]:
            raise ApiError(409, f"Call already has case number {call['case_number']}.")
        year = local_year(call["received_at"])
        cn = issue_number(c, req.app, year, "case")
        c.execute("UPDATE calls SET case_number=? WHERE id=?", (cn, call_id))
        touch_call(c, call_id)
        add_narrative(c, req, call_id, f"Case number assigned: {cn}", "SYSTEM")
        db.audit(c, req.user, req.ip, "CASE_NUMBER", "call", call_id, {"case_number": cn})
    return {"case_number": cn}


def close_call(req, call_id):
    require(req, "call.close")
    disp = s(req.body, "disposition", 20, required=True, upper=True)
    note = s(req.body, "note", 4000)
    db = req.app.db
    with db.tx() as c:
        call = get_call(c, req, call_id)
        if call["status"] == "CLOSED":
            raise ApiError(409, "Call is already closed.")
        if disp not in ref_codes(c, req, "dispositions"):
            raise ApiError(400, f"Unknown disposition '{disp}'.")
        for u in c.execute("SELECT * FROM units WHERE current_call_id=?", (call_id,)).fetchall():
            _set_unit_status(c, req, row_to_dict(u), "AVAILABLE", None, "cleared when call closed")
        now = now_iso()
        c.execute("UPDATE calls SET status='CLOSED', disposition=?, closed_at=?, closed_by=? WHERE id=?",
                  (disp, now, req.user["id"], call_id))
        touch_call(c, call_id)
        if note:
            add_narrative(c, req, call_id, note)
        add_narrative(c, req, call_id, f"Call closed. Disposition: {disp}", "SYSTEM")
        db.audit(c, req.user, req.ip, "CALL_CLOSE", "call", call_id, {"disposition": disp})
    req.app.archiver.poke()
    return {"ok": True}


def reopen_call(req, call_id):
    require(req, "call.reopen")
    reason = s(req.body, "reason", 1000, required=True)
    db = req.app.db
    with db.tx() as c:
        call = get_call(c, req, call_id)
        if call["status"] != "CLOSED":
            raise ApiError(409, "Call is not closed.")
        c.execute("UPDATE calls SET status='ACTIVE', closed_at=NULL, closed_by=NULL WHERE id=?", (call_id,))
        touch_call(c, call_id)
        add_narrative(c, req, call_id, f"Call reopened (was {call['disposition']}): {reason}", "SYSTEM")
        db.audit(c, req.user, req.ip, "CALL_REOPEN", "call", call_id, {"reason": reason,
                                                                       "prior_disposition": call["disposition"]})
    return {"ok": True}


def update_call_unit_times(req, cu_id):
    """Correct unit timestamps on a call (e.g. back-entering from paper logs)."""
    require(req, "time.edit")
    db = req.app.db
    with db.tx() as c:
        cu = c.execute("SELECT cu.* FROM call_units cu JOIN calls k ON k.id=cu.call_id"
                       " WHERE cu.id=? AND k.agency_id=?", (cu_id, req.app.agency_id)).fetchone()
        if not cu:
            raise ApiError(404, "Not found.")
        cu = row_to_dict(cu)
        changes = {}
        for f in ("dispatched_at", "enroute_at", "onscene_at", "transport_at", "cleared_at"):
            if f in req.body:
                v = ts(req.body, f)
                if f == "cleared_at" and v is None and cu["cleared_at"]:
                    raise ApiError(400, "Cannot un-clear a unit; dispatch it again instead.")
                if cu[f] != v:
                    changes[f] = {"from": cu[f], "to": v}
        if not changes:
            return {"ok": True}
        c.execute(f"UPDATE call_units SET {', '.join(k + '=?' for k in changes)} WHERE id=?",
                  [v["to"] for v in changes.values()] + [cu_id])
        touch_call(c, cu["call_id"])
        add_narrative(c, req, cu["call_id"], f"{cu['unit_code']} times corrected: " + "; ".join(
            f"{k.replace('_at', '')} {v['from'] or '—'} → {v['to'] or '—'}" for k, v in changes.items()), "SYSTEM")
        db.audit(c, req.user, req.ip, "CALL_UNIT_TIMES", "call_unit", cu_id, changes)
    return {"ok": True}


def search_calls(req):
    require(req, "view")
    q = req.query
    where = ["c.agency_id=?"]
    args = [req.app.agency_id]
    if q.get("q"):
        v = like(q["q"])
        where.append("(c.call_number LIKE ? ESCAPE '\\' OR c.case_number LIKE ? ESCAPE '\\'"
                     " OR c.location_address LIKE ? ESCAPE '\\' OR c.location_common LIKE ? ESCAPE '\\'"
                     " OR c.caller_name LIKE ? ESCAPE '\\' OR c.summary LIKE ? ESCAPE '\\'"
                     " OR EXISTS (SELECT 1 FROM call_narrative n WHERE n.call_id=c.id AND n.text LIKE ? ESCAPE '\\'))")
        args += [v] * 7
    if q.get("address"):
        where.append("(c.location_address LIKE ? ESCAPE '\\' OR c.location_cross LIKE ? ESCAPE '\\'"
                     " OR c.location_common LIKE ? ESCAPE '\\')")
        args += [like(q["address"])] * 3
    if q.get("name"):
        v = like(q["name"])
        where.append("(c.caller_name LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM call_persons p WHERE p.call_id=c.id"
                     " AND p.deleted=0 AND (p.last_name LIKE ? ESCAPE '\\' OR p.first_name LIKE ? ESCAPE '\\'"
                     " OR (COALESCE(p.first_name,'') || ' ' || COALESCE(p.last_name,'')) LIKE ? ESCAPE '\\')))")
        args += [v] * 4
    if q.get("plate"):
        where.append("EXISTS (SELECT 1 FROM call_vehicles v WHERE v.call_id=c.id AND v.deleted=0"
                     " AND REPLACE(REPLACE(v.plate,' ',''),'-','') LIKE ? ESCAPE '\\')")
        args.append(like(q["plate"].upper().replace(" ", "").replace("-", "")))
    if q.get("call_type"):
        where.append("c.call_type=?")
        args.append(q["call_type"].upper())
    if q.get("status"):
        where.append("c.status=?")
        args.append(q["status"].upper())
    if q.get("discipline"):
        where.append("c.discipline=?")
        args.append(q["discipline"].upper())
    if q.get("unit"):
        where.append("EXISTS (SELECT 1 FROM call_units cu WHERE cu.call_id=c.id AND cu.unit_code=?)")
        args.append(q["unit"].upper())
    for key, op in (("from", ">="), ("to", "<=")):
        if q.get(key):
            try:
                where.append(f"c.received_at {op} ?")
                args.append(parse_iso(q[key]))
            except ValueError:
                raise ApiError(400, f"Invalid '{key}' date.")
    limit = min(int(q.get("limit") or 200), 1000)
    rows = rows_to_list(req.app.db.conn().execute(
        f"""SELECT c.id, c.call_number, c.case_number, c.status, c.received_at, c.call_type, c.priority, c.discipline,
                   c.location_address, c.location_city, c.caller_name, c.disposition,
                   (SELECT group_concat(DISTINCT unit_code) FROM call_units cu WHERE cu.call_id=c.id) AS units
            FROM calls c WHERE {' AND '.join(where)} ORDER BY c.received_at DESC LIMIT ?""", args + [limit]))
    with req.app.db.tx() as wc:
        req.app.db.audit(wc, req.user, req.ip, "SEARCH", "call", None,
                         {k: v for k, v in q.items() if v and k != "limit"} | {"results": len(rows)})
    return {"results": rows, "limit": limit}


# ---------------------------------------------------------------------------
# Persons & vehicles attached to calls
# ---------------------------------------------------------------------------
PERSON_FIELDS = {"last_name": 80, "first_name": 80, "middle_name": 80, "suffix": 10, "dob": 10, "sex": 10,
                 "race": 20, "address": 200, "city": 80, "state": 2, "zip": 10, "phone": 40,
                 "dl_number": 40, "dl_state": 2, "height": 10, "weight": 10, "notes": 2000}
VEHICLE_FIELDS = {"plate": 15, "plate_state": 2, "year": 4, "make": 30, "model": 40, "style": 20,
                  "color": 30, "vin": 17, "notes": 2000}
ENTITY = {
    "persons": ("call_persons", PERSON_FIELDS, seed.PERSON_ROLES, "person"),
    "vehicles": ("call_vehicles", VEHICLE_FIELDS, seed.VEHICLE_ROLES, "vehicle"),
}
UPPER_FIELDS = {"state", "dl_state", "plate", "plate_state", "vin", "sex", "dl_number"}


def _entity_fields(body, fields, roles, partial):
    out = {}
    if not partial or "role" in body:
        out["role"] = one_of(s(body, "role", 20, required=True, upper=True), roles, "role")
    for f, mx in fields.items():
        if not partial or f in body:
            out[f] = s(body, f, mx, upper=f in UPPER_FIELDS)
    if out.get("dob") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", out["dob"]):
        raise ApiError(400, "Date of birth must be YYYY-MM-DD.")
    return out


def add_entity(req, call_id, kind):
    require(req, "entity.edit")
    table, fields, roles, label = ENTITY[kind]
    db = req.app.db
    with db.tx() as c:
        get_call(c, req, call_id)
        f = _entity_fields(req.body, fields, roles, partial=False)
        now = now_iso()
        cols = ["call_id", "created_by", "created_at", "updated_at"] + list(f)
        vals = [call_id, req.user["id"], now, now] + list(f.values())
        eid = c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals).lastrowid
        touch_call(c, call_id)
        desc = (f"{f.get('last_name') or ''}, {f.get('first_name') or ''}".strip(", ") if kind == "persons"
                else f"{f.get('plate') or 'no plate'} {f.get('plate_state') or ''} {f.get('color') or ''} "
                     f"{f.get('make') or ''} {f.get('model') or ''}".strip())
        add_narrative(c, req, call_id, f"{label.title()} added ({f['role']}): {desc}", "SYSTEM")
        db.audit(c, req.user, req.ip, f"{label.upper()}_ADD", label, eid, {"call_id": call_id, **f})
    return {"id": eid}


def update_entity(req, eid, kind):
    require(req, "entity.edit")
    table, fields, roles, label = ENTITY[kind]
    db = req.app.db
    with db.tx() as c:
        row = c.execute(f"SELECT e.* FROM {table} e JOIN calls k ON k.id=e.call_id WHERE e.id=? AND k.agency_id=?"
                        " AND e.deleted=0", (eid, req.app.agency_id)).fetchone()
        if not row:
            raise ApiError(404, "Not found.")
        row = row_to_dict(row)
        if req.method == "DELETE":
            c.execute(f"UPDATE {table} SET deleted=1, updated_at=? WHERE id=?", (now_iso(), eid))
            touch_call(c, row["call_id"])
            add_narrative(c, req, row["call_id"], f"{label.title()} record #{eid} removed", "SYSTEM")
            db.audit(c, req.user, req.ip, f"{label.upper()}_DELETE", label, eid, {"call_id": row["call_id"]})
            return {"ok": True}
        f = _entity_fields(req.body, fields, roles, partial=True)
        changes = {k: {"from": row[k], "to": v} for k, v in f.items() if row[k] != v}
        if changes:
            c.execute(f"UPDATE {table} SET {', '.join(k + '=?' for k in changes)}, updated_at=? WHERE id=?",
                      [f[k] for k in changes] + [now_iso(), eid])
            touch_call(c, row["call_id"])
            db.audit(c, req.user, req.ip, f"{label.upper()}_UPDATE", label, eid, changes)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
TIME_COL = {"DISPATCHED": "dispatched_at", "ENROUTE": "enroute_at", "ONSCENE": "onscene_at",
            "TRANSPORT": "transport_at", "AT_HOSPITAL": "at_hospital_at"}


def _clear_unit_from_call(c, req, unit, reason, at):
    call_id = unit["current_call_id"]
    if not call_id:
        return
    c.execute("UPDATE call_units SET cleared_at=? WHERE call_id=? AND unit_id=? AND cleared_at IS NULL",
              (at, call_id, unit["id"]))
    add_narrative(c, req, call_id, f"{unit['unit_code']} cleared" + (f" ({reason})" if reason else ""), "SYSTEM", at)
    touch_call(c, call_id)


def _set_unit_status(c, req, unit, status, call_id, note):
    status = one_of(status, seed.UNIT_STATUSES, "status")
    at = now_iso()
    if status in seed.CALL_STATUSES:
        target = call_id or unit["current_call_id"]
        if not target:
            raise ApiError(400, f"{status} requires a call.")
        call = get_call(c, req, target)
        if call["status"] == "CLOSED":
            raise ApiError(409, f"Call {call['call_number']} is closed.")
        if unit["current_call_id"] and unit["current_call_id"] != target:
            _clear_unit_from_call(c, req, unit, f"reassigned to {call['call_number']}", at)
        cu = c.execute("SELECT * FROM call_units WHERE call_id=? AND unit_id=? AND cleared_at IS NULL",
                       (target, unit["id"])).fetchone()
        if cu is None:
            has_units = c.execute("SELECT 1 FROM call_units WHERE call_id=? AND cleared_at IS NULL LIMIT 1",
                                  (target,)).fetchone()
            c.execute("INSERT INTO call_units(call_id, unit_id, unit_code, officer_name, is_primary, dispatched_at)"
                      " VALUES (?,?,?,?,?,?)",
                      (target, unit["id"], unit["unit_code"], unit["officer_name"], 0 if has_units else 1, at))
            cu = c.execute("SELECT * FROM call_units WHERE call_id=? AND unit_id=? AND cleared_at IS NULL",
                           (target, unit["id"])).fetchone()
        col = TIME_COL[status]
        if cu[col] is None:
            c.execute(f"UPDATE call_units SET {col}=? WHERE id=?", (at, cu["id"]))
        if call["status"] == "PENDING":
            c.execute("UPDATE calls SET status='ACTIVE' WHERE id=?", (target,))
        label = {"DISPATCHED": "dispatched", "ENROUTE": "en route", "ONSCENE": "on scene",
                 "TRANSPORT": "transporting", "AT_HOSPITAL": "at hospital"}[status]
        add_narrative(c, req, target, f"{unit['unit_code']} {label}" + (f": {note}" if note else ""), "SYSTEM", at)
        touch_call(c, target)
        new_call = target
        if status == "DISPATCHED":
            # Page Fire/EMS crews through Active911 (once per call per discipline; units are gathered at send time).
            alerter = getattr(req.app, "alerter", None)
            if alerter:
                alerter.queue(c, target, unit.get("discipline") or "POLICE", "AUTO", None)
    else:
        if unit["current_call_id"]:
            _clear_unit_from_call(c, req, unit, note or status.replace("_", " ").lower(), at)
        new_call = None
    c.execute("UPDATE units SET status=?, status_at=?, current_call_id=? WHERE id=?",
              (status, at, new_call, unit["id"]))
    c.execute("INSERT INTO unit_status_log(unit_id, unit_code, call_id, status, at, user_id, note) VALUES (?,?,?,?,?,?,?)",
              (unit["id"], unit["unit_code"], new_call or unit["current_call_id"], status, at, req.user["id"], note))
    req.app.db.audit(c, req.user, req.ip, "UNIT_STATUS", "unit", unit["id"],
                     {"unit": unit["unit_code"], "from": unit["status"], "to": status, "call_id": new_call, "note": note})


def set_unit_status(req, unit_id):
    status = s(req.body, "status", 20, required=True, upper=True)
    call_id = num(req.body, "call_id", integer=True)
    note = s(req.body, "note", 500)
    with req.app.db.tx() as c:
        unit = get_unit(c, req, unit_id)
        if not can(req.user, "unit.status.any"):
            if not (can(req.user, "unit.status.own") and unit["officer_user_id"] == req.user["id"]):
                raise ApiError(403, "You can only change the status of the unit you are signed on to.")
            if call_id and status == "DISPATCHED":
                raise ApiError(403, "Officers cannot dispatch; use ENROUTE or ONSCENE to attach to a call.")
        _set_unit_status(c, req, unit, status, call_id, note)
    return {"ok": True}


def unit_signon(req, unit_id):
    with req.app.db.tx() as c:
        unit = get_unit(c, req, unit_id)
        if can(req.user, "unit.crew") and "officer_name" in req.body:
            name = s(req.body, "officer_name", 100)
            officer_id = None
        else:
            if req.user["role"] != "officer" and not can(req.user, "unit.crew"):
                raise ApiError(403, "Not permitted.")
            # Officer signs themselves on; leave any previous unit first.
            for prev in c.execute("SELECT * FROM units WHERE officer_user_id=? AND id<>?",
                                  (req.user["id"], unit_id)).fetchall():
                c.execute("UPDATE units SET officer_user_id=NULL, officer_name=NULL WHERE id=?", (prev["id"],))
            name = req.user["full_name"] + (f" #{req.user['badge']}" if req.user["badge"] else "")
            officer_id = req.user["id"]
        c.execute("UPDATE units SET officer_name=?, officer_user_id=? WHERE id=?", (name, officer_id, unit_id))
        req.app.db.audit(c, req.user, req.ip, "UNIT_CREW", "unit", unit_id, {"officer_name": name})
        if name and unit["status"] == "OFF_DUTY":
            unit["officer_name"] = name
            _set_unit_status(c, req, unit, "AVAILABLE", None, "on duty")
    return {"ok": True}


def unit_signoff(req, unit_id):
    with req.app.db.tx() as c:
        unit = get_unit(c, req, unit_id)
        if not can(req.user, "unit.crew") and unit["officer_user_id"] != req.user["id"]:
            raise ApiError(403, "Not permitted.")
        if unit["status"] != "OFF_DUTY":
            _set_unit_status(c, req, unit, "OFF_DUTY", None, "off duty")
        c.execute("UPDATE units SET officer_name=NULL, officer_user_id=NULL WHERE id=?", (unit_id,))
        req.app.db.audit(c, req.user, req.ip, "UNIT_CREW", "unit", unit_id, {"officer_name": None})
    return {"ok": True}


def unit_history(req, unit_id):
    require(req, "view")
    c = req.app.db.conn()
    get_unit(c, req, unit_id)
    return {"history": rows_to_list(c.execute(
        "SELECT l.*, k.call_number FROM unit_status_log l LEFT JOIN calls k ON k.id=l.call_id"
        " WHERE l.unit_id=? ORDER BY l.id DESC LIMIT 200", (unit_id,)))}


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
def admin_users(req):
    require(req, "admin")
    rows = req.app.db.conn().execute("SELECT * FROM users WHERE agency_id=? ORDER BY username",
                                     (req.app.agency_id,)).fetchall()
    return {"users": [public_user(row_to_dict(r)) for r in rows]}


def _temp_password_check(req, pw, username):
    problems = auth.password_problems(pw or "", req.app.config.getint("security", "password_min_length"), username)
    if problems:
        raise ApiError(400, "Temporary password " + "; ".join(problems) + ".")


def admin_create_user(req):
    require(req, "admin")
    b = req.body
    username = s(b, "username", 64, required=True)
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,64}", username):
        raise ApiError(400, "Username may contain letters, digits, '.', '_' and '-'.")
    role = one_of(s(b, "role", 20, required=True), seed.ROLES, "role")
    pw = b.get("password") or ""
    _temp_password_check(req, pw, username)
    now = now_iso()
    with req.app.db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ApiError(409, "Username already exists.")
        uid = c.execute(
            "INSERT INTO users(agency_id, username, full_name, badge, role, pw_hash, must_change_pw, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,1,?,?)",
            (req.app.agency_id, username, s(b, "full_name", 100, required=True), s(b, "badge", 20), role,
             auth.hash_password(pw), now, now)).lastrowid
        req.app.db.audit(c, req.user, req.ip, "USER_CREATE", "user", uid, {"username": username, "role": role})
    return {"id": uid}


def admin_update_user(req, uid):
    require(req, "admin")
    b = req.body
    with req.app.db.tx() as c:
        u = c.execute("SELECT * FROM users WHERE id=? AND agency_id=?", (uid, req.app.agency_id)).fetchone()
        if not u:
            raise ApiError(404, "User not found.")
        u = row_to_dict(u)
        action = b.get("action")
        detail = {}
        if action == "reset_password":
            pw = b.get("password") or ""
            _temp_password_check(req, pw, u["username"])
            c.execute("UPDATE users SET pw_hash=?, must_change_pw=1, failed_attempts=0, locked_until=NULL WHERE id=?",
                      (auth.hash_password(pw), uid))
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        elif action == "unlock":
            c.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (uid,))
        elif action == "reset_mfa":
            c.execute("UPDATE users SET totp_secret=NULL, totp_pending=NULL, totp_enabled=0 WHERE id=?", (uid,))
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        elif action == "update":
            f = {}
            if "full_name" in b:
                f["full_name"] = s(b, "full_name", 100, required=True)
            if "badge" in b:
                f["badge"] = s(b, "badge", 20)
            if "role" in b:
                f["role"] = one_of(b["role"], seed.ROLES, "role")
            if "active" in b:
                f["active"] = 1 if b["active"] else 0
                if f["active"] and not u["active"]:
                    c.execute("UPDATE users SET enabled_at=? WHERE id=?", (now_iso(), uid))
            if uid == req.user["id"] and (f.get("active") == 0 or f.get("role", "admin") != "admin"):
                raise ApiError(400, "You cannot deactivate or demote your own account.")
            detail = {k: {"from": u[k], "to": v} for k, v in f.items() if u[k] != v}
            if detail:
                c.execute(f"UPDATE users SET {', '.join(k + '=?' for k in detail)} WHERE id=?",
                          [f[k] for k in detail] + [uid])
            if f.get("active") == 0:
                c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        else:
            raise ApiError(400, "Unknown action.")
        c.execute("UPDATE users SET updated_at=? WHERE id=?", (now_iso(), uid))
        req.app.db.audit(c, req.user, req.ip, "USER_" + action.upper(), "user", uid, detail or None)
    return {"ok": True}


def admin_units(req):
    require(req, "unit.manage")
    return {"units": rows_to_list(req.app.db.conn().execute(
        "SELECT * FROM units WHERE agency_id=? ORDER BY active DESC, unit_code", (req.app.agency_id,)))}


def _unit_fields(b, partial):
    f = {}
    if not partial or "unit_code" in b:
        code = s(b, "unit_code", 15, required=True, upper=True)
        if not re.fullmatch(r"[A-Z0-9-]+", code):
            raise ApiError(400, "Unit code may contain letters, digits and '-'.")
        f["unit_code"] = code
    if not partial or "description" in b:
        f["description"] = s(b, "description", 100)
    if not partial or "unit_type" in b:
        f["unit_type"] = one_of(s(b, "unit_type", 20, upper=True) or "PATROL", seed.UNIT_TYPES, "unit_type")
        f["discipline"] = seed.UNIT_TYPE_DISCIPLINE[f["unit_type"]]
    if "active" in b:
        f["active"] = 1 if b["active"] else 0
    return f


def admin_create_unit(req):
    require(req, "unit.manage")
    f = _unit_fields(req.body, False)
    with req.app.db.tx() as c:
        if c.execute("SELECT 1 FROM units WHERE agency_id=? AND unit_code=?",
                     (req.app.agency_id, f["unit_code"])).fetchone():
            raise ApiError(409, "Unit code already exists.")
        cols = ["agency_id", "status_at"] + list(f)
        uid = c.execute(f"INSERT INTO units({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [req.app.agency_id, now_iso()] + list(f.values())).lastrowid
        req.app.db.audit(c, req.user, req.ip, "UNIT_CREATE", "unit", uid, f)
    return {"id": uid}


def admin_update_unit(req, unit_id):
    require(req, "unit.manage")
    f = _unit_fields(req.body, True)
    with req.app.db.tx() as c:
        unit = get_unit(c, req, unit_id)
        if f.get("active") == 0 and unit["current_call_id"]:
            raise ApiError(409, "Clear the unit from its call before deactivating it.")
        changes = {k: {"from": unit[k], "to": v} for k, v in f.items() if unit[k] != v}
        if changes:
            c.execute(f"UPDATE units SET {', '.join(k + '=?' for k in changes)} WHERE id=?",
                      [f[k] for k in changes] + [unit_id])
            req.app.db.audit(c, req.user, req.ip, "UNIT_UPDATE", "unit", unit_id, changes)
    return {"ok": True}


def admin_ref_list(req, table):
    require(req, "admin")
    return {"items": rows_to_list(req.app.db.conn().execute(
        f"SELECT * FROM {table} WHERE agency_id=? ORDER BY code", (req.app.agency_id,)))}


def admin_ref_save(req, table):
    require(req, "admin")
    b = req.body
    code = s(b, "code", 20, required=True, upper=True)
    if not re.fullmatch(r"[A-Z0-9_-]+", code):
        raise ApiError(400, "Code may contain letters, digits, '_' and '-'.")
    desc = s(b, "description", 100, required=True)
    active = 0 if b.get("active") is False else 1
    with req.app.db.tx() as c:
        if table == "call_types":
            prio = num(b, "default_priority", 1, 5, integer=True) or 3
            disc = one_of(s(b, "discipline", 10, upper=True) or "POLICE", seed.DISCIPLINES, "discipline")
            c.execute("INSERT INTO call_types(agency_id, code, description, default_priority, active, discipline)"
                      " VALUES (?,?,?,?,?,?)"
                      " ON CONFLICT(agency_id, code) DO UPDATE SET description=excluded.description,"
                      " default_priority=excluded.default_priority, active=excluded.active, discipline=excluded.discipline",
                      (req.app.agency_id, code, desc, prio, active, disc))
        else:
            c.execute("INSERT INTO dispositions(agency_id, code, description, active) VALUES (?,?,?,?)"
                      " ON CONFLICT(agency_id, code) DO UPDATE SET description=excluded.description, active=excluded.active",
                      (req.app.agency_id, code, desc, active))
        req.app.db.audit(c, req.user, req.ip, "REF_SAVE", table, code, {"description": desc, "active": active})
    return {"ok": True}


def audit_list(req):
    require(req, "audit")
    q = req.query
    where, args = ["1=1"], []
    for key in ("entity", "entity_id", "action"):
        if q.get(key):
            where.append(f"{key}=?")
            args.append(q[key])
    if q.get("username"):
        where.append("username=? COLLATE NOCASE")
        args.append(q["username"])
    if q.get("before_id", "").isdigit():
        where.append("id<?")
        args.append(int(q["before_id"]))
    limit = min(int(q.get("limit") or 200), 1000)
    rows = rows_to_list(req.app.db.conn().execute(
        f"SELECT id, at, user_id, username, ip, action, entity, entity_id, detail FROM audit_log"
        f" WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", args + [limit]))
    ok, n, bad = req.app.db.verify_audit()
    return {"entries": rows, "chain_ok": ok, "chain_rows": n, "chain_first_bad_id": bad}


# ---------------------------------------------------------------------------
# Export (for migration into the restored state system or a replacement)
# ---------------------------------------------------------------------------
EXPORT_TABLES = {
    # table: (columns or None for all, how to filter by date range)
    "calls": (None, "received_at"),
    "call_narrative": (None, "call"),
    "call_units": (None, "call"),
    "call_persons": (None, "call"),
    "call_vehicles": (None, "call"),
    "unit_status_log": (None, "at"),
    "units": (None, None),
    "call_types": (None, None),
    "dispositions": (None, None),
    "users": (["id", "username", "full_name", "badge", "role", "active", "created_at"], None),
    "audit_log": (None, "at"),
}


def build_export(app, date_from=None, date_to=None):
    """Return a dict of table -> list of rows, honoring an optional received_at date range."""
    c = app.db.conn()
    aid = app.agency_id
    tables = {}
    call_where = "agency_id=?"
    call_args = [aid]
    if date_from:
        call_where += " AND received_at>=?"
        call_args.append(date_from)
    if date_to:
        call_where += " AND received_at<=?"
        call_args.append(date_to)
    for t, (cols, filt) in EXPORT_TABLES.items():
        sel = ", ".join(cols) if cols else "*"
        if t == "calls":
            sql, args = f"SELECT {sel} FROM calls WHERE {call_where} ORDER BY id", call_args
        elif filt == "call":
            sql = f"SELECT {sel} FROM {t} WHERE call_id IN (SELECT id FROM calls WHERE {call_where}) ORDER BY id"
            args = call_args
        elif filt == "at":
            sql, args = f"SELECT {sel} FROM {t} WHERE 1=1", []
            if date_from:
                sql += " AND at>=?"
                args.append(date_from)
            if date_to:
                sql += " AND at<=?"
                args.append(date_to)
            sql += " ORDER BY id"
        elif t == "users":
            sql, args = f"SELECT {sel} FROM users WHERE agency_id=? ORDER BY id", [aid]
        else:
            sql, args = f"SELECT {sel} FROM {t} WHERE agency_id=? ORDER BY id", [aid]
        tables[t] = rows_to_list(c.execute(sql, args))
    return tables


EXPORT_README = """Interim CAD data export
=======================

This package contains every record in the interim CAD for the selected range.

Files
-----
export.json        Full nested export: each call with its narrative, units, persons and vehicles.
csv/<table>.csv    One flat CSV per table (UTF-8, header row). Join on the id columns below.
SCHEMA.sql         The exact database schema the data came from.

Conventions
-----------
* All timestamps are ISO-8601 in UTC with a trailing Z (e.g. 2026-11-24T15:04:05Z).
  Convert to America/New_York for display.
* calls.id is the internal key; calls.call_number is the human call number.
  call_narrative.call_id, call_units.call_id, call_persons.call_id and call_vehicles.call_id reference calls.id.
* call_units holds per-unit timestamps (dispatched/enroute/onscene/transport/cleared) for each call.
* call_persons / call_vehicles rows with deleted=1 were removed by a user but are retained for the record.
* call_narrative is append-only; entry_type SYSTEM rows are automatic chronology, NOTE rows were typed by users.
* audit_log is a hash chain (each hash = sha256 of the previous hash and the row). Verify with
  `python manage.py verify-audit` against the original database.
"""


def send_alert(req, call_id):
    """Manually (re)send an Active911 alert for a call, e.g. for a second alarm or updated information."""
    require(req, "alert.send")
    alerter = req.app.alerter
    if not alerter.enabled:
        raise ApiError(400, "Active911 alerts are not configured ([active911] in config.ini).")
    disc = one_of(s(req.body, "discipline", 10, upper=True) or "", alerter.disciplines(), "discipline")
    note = s(req.body, "note", 300)
    with req.app.db.tx() as c:
        call = get_call(c, req, call_id)
        alerter.queue(c, call_id, disc, "MANUAL", req.user, note=note)
        add_narrative(c, req, call_id, f"Active911 alert requested ({disc})" + (f": {note}" if note else ""), "SYSTEM")
        touch_call(c, call_id)
        req.app.db.audit(c, req.user, req.ip, "ALERT_REQUEST", "call", call_id,
                         {"discipline": disc, "call_number": call["call_number"]})
    alerter.poke()
    return {"ok": True}


def alerts_status(req):
    require(req, "view")
    return req.app.alerter.public_status(detail=True)


def archive_status(req):
    require(req, "export")
    st = dict(req.app.archiver.status)
    c = req.app.db.conn()
    st["calls_total"] = c.execute("SELECT count(*) FROM calls WHERE agency_id=?", (req.app.agency_id,)).fetchone()[0]
    st["calls_not_yet_archived"] = c.execute(
        "SELECT count(*) FROM calls k LEFT JOIN archive_state a ON a.call_id=k.id"
        " WHERE a.call_id IS NULL OR a.version<>k.version").fetchone()[0]
    return st


def archive_run(req):
    require(req, "export")
    if not req.app.archiver.dir:
        raise ApiError(400, "No archive folder is configured ([archive] dir in config.ini).")
    req.app.archiver.poke()
    return {"ok": True}


def export_daily_pdf(req):
    require(req, "export")
    from .archive import daily_pdf, daily_rows
    day = req.query.get("date") or datetime.now().strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ApiError(400, "Date must be YYYY-MM-DD.")
    try:
        rows = daily_rows(req.app.db.conn(), req.app.agency_id, day)
    except ValueError:
        raise ApiError(400, "Invalid date.")
    data = daily_pdf(req.app, day, rows)
    with req.app.db.tx() as c:
        req.app.db.audit(c, req.user, req.ip, "EXPORT_DAILY_PDF", "export", day, {"calls": len(rows)})
    return RawResponse(data, "application/pdf", f"daily-call-log-{day}.pdf")


def export_package(req):
    require(req, "export")
    date_from = parse_iso(req.query["from"]) if req.query.get("from") else None
    date_to = parse_iso(req.query["to"]) if req.query.get("to") else None
    data = make_export_zip(req.app, date_from, date_to)
    with req.app.db.tx() as c:
        req.app.db.audit(c, req.user, req.ip, "EXPORT", "export", None, {"from": date_from, "to": date_to,
                                                                          "bytes": len(data)})
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return RawResponse(data, "application/zip", f"cad-export-{stamp}.zip")


def make_export_zip(app, date_from=None, date_to=None):
    from .db import SCHEMA
    tables = build_export(app, date_from, date_to)
    by_call = {}
    for t in ("call_narrative", "call_units", "call_persons", "call_vehicles"):
        for r in tables[t]:
            by_call.setdefault(r["call_id"], {}).setdefault(t, []).append(r)
    nested = []
    for call in tables["calls"]:
        rel = by_call.get(call["id"], {})
        nested.append(call | {
            "narrative": rel.get("call_narrative", []),
            "units": rel.get("call_units", []),
            "persons": rel.get("call_persons", []),
            "vehicles": rel.get("call_vehicles", []),
        })
    doc = {
        "export_meta": {
            "generated_at": now_iso(), "system": "interim-cad", "system_version": __version__,
            "schema_version": SCHEMA_VERSION, "agency": app.config.get("agency", "name"),
            "ori": app.config.get("agency", "ori"), "range": {"from": date_from, "to": date_to},
            "record_counts": {t: len(v) for t, v in tables.items()},
        },
        "calls": nested,
        "units": tables["units"], "unit_status_log": tables["unit_status_log"],
        "call_types": tables["call_types"], "dispositions": tables["dispositions"], "users": tables["users"],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", EXPORT_README)
        z.writestr("SCHEMA.sql", SCHEMA)
        z.writestr("export.json", json.dumps(doc, indent=1, default=str))
        for t, rows in tables.items():
            out = io.StringIO()
            cols = list(rows[0].keys()) if rows else (
                EXPORT_TABLES[t][0] or [r[1] for r in app.db.conn().execute(f"PRAGMA table_info({t})")])
            w = csv.writer(out)
            w.writerow(cols)
            for r in rows:
                w.writerow(["" if r[k] is None else r[k] for k in cols])
            z.writestr(f"csv/{t}.csv", out.getvalue())
    return buf.getvalue()


class RawResponse:
    def __init__(self, data, content_type, filename=None):
        self.data = data
        self.content_type = content_type
        self.filename = filename


# ---------------------------------------------------------------------------
# Routing table: (method, pattern, handler, auth_mode)
# auth_mode: "public" | "limited" (allowed before password change/MFA setup) | "full"
# ---------------------------------------------------------------------------
def _r(method, pattern, fn, mode="full"):
    return method, re.compile("^" + pattern + "$"), fn, mode


ROUTES = [
    _r("POST", r"/api/login", login, "public"),
    _r("POST", r"/api/logout", logout, "limited"),
    _r("GET", r"/api/me", me, "limited"),
    _r("POST", r"/api/me/password", change_password, "limited"),
    _r("POST", r"/api/me/mfa/begin", mfa_begin, "limited"),
    _r("POST", r"/api/me/mfa/confirm", mfa_confirm, "limited"),
    _r("GET", r"/api/reference", reference),
    _r("GET", r"/api/board", board),
    _r("GET", r"/api/calls/search", search_calls),
    _r("POST", r"/api/calls", create_call),
    _r("GET", r"/api/calls/(\d+)", call_detail),
    _r("PUT", r"/api/calls/(\d+)", update_call),
    _r("POST", r"/api/calls/(\d+)/narrative", add_call_note),
    _r("POST", r"/api/calls/(\d+)/case-number", assign_case_number),
    _r("POST", r"/api/calls/(\d+)/close", close_call),
    _r("POST", r"/api/calls/(\d+)/reopen", reopen_call),
    _r("POST", r"/api/calls/(\d+)/alert", send_alert),
    _r("GET", r"/api/alerts/status", alerts_status),
    _r("POST", r"/api/calls/(\d+)/persons", lambda req, i: add_entity(req, i, "persons")),
    _r("POST", r"/api/calls/(\d+)/vehicles", lambda req, i: add_entity(req, i, "vehicles")),
    _r("PUT", r"/api/persons/(\d+)", lambda req, i: update_entity(req, i, "persons")),
    _r("DELETE", r"/api/persons/(\d+)", lambda req, i: update_entity(req, i, "persons")),
    _r("PUT", r"/api/vehicles/(\d+)", lambda req, i: update_entity(req, i, "vehicles")),
    _r("DELETE", r"/api/vehicles/(\d+)", lambda req, i: update_entity(req, i, "vehicles")),
    _r("PUT", r"/api/call-units/(\d+)", update_call_unit_times),
    _r("POST", r"/api/units/(\d+)/status", set_unit_status),
    _r("POST", r"/api/units/(\d+)/signon", unit_signon),
    _r("POST", r"/api/units/(\d+)/signoff", unit_signoff),
    _r("GET", r"/api/units/(\d+)/history", unit_history),
    _r("GET", r"/api/admin/users", admin_users),
    _r("POST", r"/api/admin/users", admin_create_user),
    _r("PUT", r"/api/admin/users/(\d+)", admin_update_user),
    _r("GET", r"/api/admin/units", admin_units),
    _r("POST", r"/api/admin/units", admin_create_unit),
    _r("PUT", r"/api/admin/units/(\d+)", admin_update_unit),
    _r("GET", r"/api/admin/call-types", lambda req: admin_ref_list(req, "call_types")),
    _r("POST", r"/api/admin/call-types", lambda req: admin_ref_save(req, "call_types")),
    _r("GET", r"/api/admin/dispositions", lambda req: admin_ref_list(req, "dispositions")),
    _r("POST", r"/api/admin/dispositions", lambda req: admin_ref_save(req, "dispositions")),
    _r("GET", r"/api/audit", audit_list),
    _r("GET", r"/api/export", export_package),
    _r("GET", r"/api/export/daily-pdf", export_daily_pdf),
    _r("GET", r"/api/archive/status", archive_status),
    _r("POST", r"/api/archive/run", archive_run),
]
