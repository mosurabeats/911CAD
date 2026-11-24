"""Call archive: writes every call to a folder (e.g. a mapped network drive such as I:\\CAD\\Calls).

For each call it writes, and rewrites whenever the call changes:
  <dir>/<YYYY>/<YYYY-MM>/<call_number>.txt    human-readable call record (print-ready)
  <dir>/<YYYY>/<YYYY-MM>/<call_number>.json   complete machine-readable record
and a daily call log:
  <dir>/Daily/<YYYY-MM-DD>.csv                one row per call received that day

The live database never lives on the network drive (SQLite over a file share risks corruption).
The archive is a copy, kept current by a background sweep. If the drive is unreachable,
dispatch keeps working and the sweep catches up automatically when the drive is back.
"""
import csv
import io
import json
import logging
import os
import threading
import time
from datetime import datetime

from .pdf import text_pdf
from .util import now_iso, rows_to_list, row_to_dict

log = logging.getLogger("cad.archive")

DAILY_COLS = ["call_number", "case_number", "status", "received_local", "call_type", "priority", "source",
              "location_address", "location_city", "location_common", "caller_name", "caller_phone",
              "units", "first_dispatched_local", "first_onscene_local", "closed_local", "disposition"]


def local(iso, fmt="%Y-%m-%d %H:%M:%S"):
    if not iso:
        return ""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime(fmt)


def safe_name(s):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in s)


def atomic_write(path, data):
    """Write via a temp file then rename, so a reader never sees a half-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    if isinstance(data, bytes):
        with open(tmp, "wb") as f:
            f.write(data)
    else:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(data)
    os.replace(tmp, path)


def load_call(c, call_id):
    call = row_to_dict(c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone())
    rel = {
        "narrative": rows_to_list(c.execute("SELECT * FROM call_narrative WHERE call_id=? ORDER BY at, id", (call_id,))),
        "units": rows_to_list(c.execute("SELECT * FROM call_units WHERE call_id=? ORDER BY dispatched_at, id", (call_id,))),
        "persons": rows_to_list(c.execute("SELECT * FROM call_persons WHERE call_id=? ORDER BY id", (call_id,))),
        "vehicles": rows_to_list(c.execute("SELECT * FROM call_vehicles WHERE call_id=? ORDER BY id", (call_id,))),
    }
    return call | rel


def render_text(rec, agency):
    L = []
    w = L.append
    w(f"{agency} - CALL FOR SERVICE RECORD")
    w("=" * 72)
    w(f"Call number : {rec['call_number']}")
    w(f"Case number : {rec['case_number'] or '-'}")
    w(f"Status      : {rec['status']}" + (f"   Disposition: {rec['disposition']}" if rec["disposition"] else ""))
    w(f"Call type   : {rec['call_type']}   Priority: P{rec['priority']}   Source: {rec['source']}")
    w(f"Received    : {local(rec['received_at'])}")
    if rec["closed_at"]:
        w(f"Closed      : {local(rec['closed_at'])}")
    w("")
    w("LOCATION")
    w(f"  Address   : {rec['location_address'] or ''}{', ' + rec['location_city'] if rec['location_city'] else ''}")
    for k, lbl in (("location_cross", "Cross st"), ("location_common", "Place"), ("location_notes", "Notes")):
        if rec[k]:
            w(f"  {lbl:<9} : {rec[k]}")
    w("")
    w("CALLER")
    w(f"  Name      : {rec['caller_name'] or ''}")
    w(f"  Phone     : {rec['caller_phone'] or ''}")
    w(f"  Address   : {rec['caller_address'] or ''}")
    if rec["summary"]:
        w("")
        w("SUMMARY")
        w("  " + rec["summary"].replace("\n", "\n  "))
    w("")
    w("UNITS        Dispatched  En route    On scene    Transport   Cleared     Officer")
    for u in rec["units"]:
        t = [local(u[k], "%H:%M:%S") or "--" for k in ("dispatched_at", "enroute_at", "onscene_at", "transport_at",
                                                        "cleared_at")]
        w(f"  {u['unit_code']:<10} " + "  ".join(f"{x:<10}" for x in t) + f"  {u['officer_name'] or ''}")
    if not rec["units"]:
        w("  (none)")
    persons = [p for p in rec["persons"] if not p["deleted"]]
    if persons:
        w("")
        w("PERSONS")
        for p in persons:
            name = ", ".join(x for x in (p["last_name"], p["first_name"]) if x)
            extra = "; ".join(x for x in (f"DOB {p['dob']}" if p["dob"] else "", p["sex"] or "", p["race"] or "",
                                          " ".join(x for x in (p["address"], p["city"], p["state"]) if x),
                                          p["phone"] or "",
                                          f"OLN {p['dl_number']} {p['dl_state'] or ''}" if p["dl_number"] else "") if x)
            w(f"  [{p['role']}] {name}  {extra}")
            if p["notes"]:
                w(f"      {p['notes']}")
    vehicles = [v for v in rec["vehicles"] if not v["deleted"]]
    if vehicles:
        w("")
        w("VEHICLES")
        for v in vehicles:
            desc = " ".join(x for x in (v["year"], v["color"], v["make"], v["model"], v["style"]) if x)
            w(f"  [{v['role']}] {v['plate'] or 'no plate'} {v['plate_state'] or ''}  {desc}"
              + (f"  VIN {v['vin']}" if v["vin"] else ""))
            if v["notes"]:
                w(f"      {v['notes']}")
    w("")
    w("NARRATIVE / CHRONOLOGY")
    for n in rec["narrative"]:
        who = n["username"] or "system"
        txt = n["text"].replace("\n", "\n" + " " * 34)
        w(f"  {local(n['at'])}  {who:<12}{'*' if n['entry_type'] == 'SYSTEM' else ' '} {txt}")
    w("")
    w("(* = automatic system entry)")
    w(f"Archived {local(now_iso())} - version {rec['version']}. Source of record: Interim CAD database.")
    return "\r\n".join(L) + "\r\n"


def daily_rows(c, agency_id, day):
    """Calls received on a local calendar day (YYYY-MM-DD), with unit summary columns."""
    from .util import parse_iso
    start = datetime.strptime(day, "%Y-%m-%d").astimezone()
    end = start.replace(hour=23, minute=59, second=59)
    return c.execute(
        """SELECT k.*,
             (SELECT group_concat(DISTINCT unit_code) FROM call_units cu WHERE cu.call_id=k.id) AS units,
             (SELECT min(dispatched_at) FROM call_units cu WHERE cu.call_id=k.id) AS first_disp,
             (SELECT min(onscene_at) FROM call_units cu WHERE cu.call_id=k.id) AS first_ons
           FROM calls k WHERE k.agency_id=? AND k.received_at BETWEEN ? AND ? ORDER BY k.received_at""",
        (agency_id, parse_iso(start.isoformat()), parse_iso(end.isoformat()))).fetchall()


def daily_pdf(app, day, rows):
    """One-page-per-~40-calls daily call log, landscape."""
    agency = app.config.get("agency", "name")
    date_label = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %d, %Y")
    head = f"{'CALL #':<12} {'RECEIVED':<8} {'TYPE':<9} {'P':<1} {'LOCATION':<34} {'UNITS':<12} {'ON SCENE':<8} {'CLOSED':<8} {'DISP / STATUS':<11} {'CASE #':<12}"
    lines = [f"{len(rows)} call(s) received.  Times are local.", "", head, "-" * len(head)]
    for k in rows:
        loc = ", ".join(x for x in (k["location_address"] or k["location_common"], k["location_city"]) if x)
        lines.append(
            f"{k['call_number']:<12} {local(k['received_at'], '%H:%M:%S'):<8} {k['call_type'][:9]:<9} {k['priority']:<1} "
            f"{loc[:34]:<34} {(k['units'] or '-')[:12]:<12} {local(k['first_ons'], '%H:%M:%S') or '-':<8} "
            f"{local(k['closed_at'], '%H:%M:%S') or '-':<8} {(k['disposition'] or k['status'])[:11]:<11} {k['case_number'] or '':<12}")
    if not rows:
        lines.append("(no calls)")
    by_type = {}
    for k in rows:
        by_type[k["call_type"]] = by_type.get(k["call_type"], 0) + 1
    if by_type:
        lines += ["", "CALLS BY TYPE: " + "   ".join(f"{t} {n}" for t, n in sorted(by_type.items(), key=lambda x: -x[1]))]
    lines += ["", f"Generated {local(now_iso())} from the Interim CAD database (source of record)."]
    return text_pdf(lines, f"{agency} - Daily Call Log - {date_label}", landscape=True)


class Archiver:
    def __init__(self, app, directory, interval_seconds=30):
        self.app = app
        self.dir = directory
        self.interval = interval_seconds
        self.pdf = app.config.getbool("archive", "pdf")
        self.status = {"enabled": bool(directory), "dir": directory, "last_ok": None, "last_error": None,
                       "pending": None, "written_total": 0}
        self._wake = threading.Event()

    def start(self):
        if not self.dir:
            return
        threading.Thread(target=self._loop, daemon=True, name="archive").start()

    def poke(self):
        self._wake.set()

    def _loop(self):
        while True:
            try:
                self.sweep()
            except Exception as e:  # noqa: BLE001 - drive offline etc.; keep dispatch running
                self.status["last_error"] = f"{now_iso()} {type(e).__name__}: {e}"
                log.warning("Archive sweep failed (will retry): %s", e)
            finally:
                self.app.db.close_thread_conn()
            self._wake.wait(self.interval)
            self._wake.clear()

    def sweep(self, limit=500):
        """Write every call whose version differs from what was last archived. Returns count written."""
        db = self.app.db
        c = db.conn()
        if not os.path.isdir(self.dir):
            # If the drive/share itself (e.g. I:\ or \\server\share) is disconnected, do not create
            # a local folder in its place - report it and retry on the next sweep.
            drive = os.path.splitdrive(os.path.abspath(self.dir))[0]
            if drive and not os.path.exists(drive + os.sep):
                raise OSError(f"Archive drive not reachable: {drive}")
            os.makedirs(self.dir, exist_ok=True)
        rows = c.execute(
            "SELECT k.id, k.version FROM calls k LEFT JOIN archive_state a ON a.call_id = k.id"
            " WHERE a.call_id IS NULL OR a.version <> k.version ORDER BY k.id LIMIT ?", (limit,)).fetchall()
        self.status["pending"] = len(rows)
        days = set()
        agency = self.app.config.get("agency", "name")
        written = 0
        for r in rows:
            rec = load_call(c, r["id"])
            month = local(rec["received_at"], "%Y-%m")
            base = os.path.join(self.dir, month[:4], month, safe_name(rec["call_number"]))
            text = render_text(rec, agency)
            atomic_write(base + ".txt", text)
            if self.pdf:
                atomic_write(base + ".pdf", text_pdf(text.split("\r\n"), f"{agency} - Call {rec['call_number']}"))
            atomic_write(base + ".json", json.dumps(rec, indent=1, default=str))
            days.add(local(rec["received_at"], "%Y-%m-%d"))
            with db.write_lock:  # not via tx(): archive bookkeeping must not trigger board refreshes
                c.execute("INSERT INTO archive_state(call_id, version, archived_at) VALUES (?,?,?)"
                          " ON CONFLICT(call_id) DO UPDATE SET version=excluded.version, archived_at=excluded.archived_at",
                          (r["id"], rec["version"], now_iso()))
            written += 1
        for day in sorted(days):
            self.write_daily(c, day)
        self.status.update(last_ok=now_iso(), last_error=None, pending=0)
        self.status["written_total"] += written
        return written

    def write_daily(self, c, day):
        rows = daily_rows(c, self.app.agency_id, day)
        out = io.StringIO()
        wr = csv.writer(out)
        wr.writerow(DAILY_COLS)
        for k in rows:
            wr.writerow([k["call_number"], k["case_number"] or "", k["status"], local(k["received_at"]), k["call_type"],
                         k["priority"], k["source"], k["location_address"] or "", k["location_city"] or "",
                         k["location_common"] or "", k["caller_name"] or "", k["caller_phone"] or "", k["units"] or "",
                         local(k["first_disp"]), local(k["first_ons"]), local(k["closed_at"]), k["disposition"] or ""])
        atomic_write(os.path.join(self.dir, "Daily", f"{day}.csv"), out.getvalue())
        if self.pdf:
            atomic_write(os.path.join(self.dir, "Daily", f"{day}.pdf"), daily_pdf(self.app, day, rows))


class AuditMirror:
    """Appends every audit entry to <mirror_dir>/audit-YYYY-MM-DD.jsonl on another machine or share.

    The database already hash-chains the audit log, but someone with full control of the server could
    rebuild the chain. A continuously written second copy elsewhere (readable by the TAC/IT, not by
    CAD users or the server's admins) makes that detectable.
    """

    def __init__(self, app, directory, interval_seconds=15):
        self.app = app
        self.dir = directory
        self.interval = interval_seconds
        self.last_error = None

    def start(self):
        if self.dir:
            threading.Thread(target=self._loop, daemon=True, name="audit-mirror").start()

    def _loop(self):
        while True:
            try:
                self.sync()
                self.last_error = None
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{now_iso()} {e}"
                log.warning("Audit mirror failed (will retry): %s", e)
            finally:
                self.app.db.close_thread_conn()
            time.sleep(self.interval)

    def sync(self):
        db = self.app.db
        c = db.conn()
        os.makedirs(self.dir, exist_ok=True)
        row = c.execute("SELECT value FROM meta WHERE key='audit_mirrored_id'").fetchone()
        last = int(row[0]) if row else 0
        rows = c.execute("SELECT * FROM audit_log WHERE id > ? ORDER BY id LIMIT 5000", (last,)).fetchall()
        if not rows:
            return 0
        by_day = {}
        for r in rows:
            by_day.setdefault(r["at"][:10], []).append(json.dumps(row_to_dict(r), separators=(",", ":")))
        for day, lines in by_day.items():
            with open(os.path.join(self.dir, f"audit-{day}.jsonl"), "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
                f.flush()
                os.fsync(f.fileno())
        with db.write_lock:
            c.execute("INSERT INTO meta(key, value) VALUES ('audit_mirrored_id', ?)"
                      " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(rows[-1]["id"]),))
        return len(rows)
