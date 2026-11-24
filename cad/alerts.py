"""Active911 alerting: e-mails dispatch information to an agency's Active911 alert address.

Active911 receives alerts by e-mail at a unique per-agency address (shown on the Agency tab in
Active911, e.g. 1234-abc1234@alert.active911.com) and parses each dispatch center's format.
Messages here use Active911's documented "Cadpage" NAME: VALUE format:

    CALL: Structure Fire
    PLACE: Riverside Hardware
    ADDR: 12 Mill Rd
    CITY: Demo Town
    ID: 26MPD4521
    PRI: P1
    DATE: 11/24/2026
    TIME: 14:32:07
    UNIT: E1, L1
    INFO: X-ST: Main St; Smoke showing from rear

Tell Active911 support that this CAD sends the Cadpage format (they do not auto-detect it).

Alerts are queued in the `alerts` table inside the dispatch transaction and sent by a background
thread, so a slow or unreachable mail server never delays dispatching. Failed sends are retried
with backoff; every outcome is written to the call's narrative and the audit log.
"""
import logging
import re
import smtplib
import ssl
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from types import SimpleNamespace

from .util import now_iso

log = logging.getLogger("cad.alerts")

DISCIPLINES = ("POLICE", "FIRE", "EMS")
MAX_ATTEMPTS = 5
AUTO_DELAY_SECONDS = 5  # gather units dispatched together into one alert
_LABELS = re.compile(r"\b(CALL|PLACE|ADDR|CITY|ID|PRI|DATE|TIME|MAP|UNIT|INFO)\s*:", re.I)


def _iso_in(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0) \
        .isoformat().replace("+00:00", "Z")


def _clean(v, limit=400):
    """One line, no Cadpage labels inside values (so free text can't confuse Active911's parser)."""
    v = " ".join(str(v or "").split())
    v = _LABELS.sub(lambda m: m.group(1).upper() + " -", v)
    return v[:limit]


class Alerter:
    def __init__(self, app):
        cfg = app.config
        self.app = app
        g = lambda k: cfg.get("active911", k).strip()  # noqa: E731
        self.addresses = {d: [a.strip() for a in g(f"{d.lower()}_address").split(",") if a.strip()]
                          for d in DISCIPLINES}
        self.smtp_host = g("smtp_host")
        self.smtp_port = int(g("smtp_port") or 587)
        self.security = (g("smtp_security") or "starttls").lower()
        self.username = g("smtp_username")
        self.password = g("smtp_password")
        pw_file = cfg.path_of("active911", "smtp_password_file")
        if pw_file and not self.password:
            try:
                with open(pw_file, encoding="utf-8") as f:
                    self.password = f.read().strip()
            except OSError as e:
                log.error("Cannot read smtp_password_file: %s", e)
        self.from_address = g("from_address")
        self.include_caller = cfg.getbool("active911", "include_caller")
        self.default_city = g("default_city")
        self.config_problem = None
        self.enabled = cfg.getbool("active911", "enabled")
        if self.enabled:
            if not self.smtp_host or not self.from_address:
                self.config_problem = "smtp_host and from_address are required"
            elif not any(self.addresses.values()):
                self.config_problem = "no alert address set (fire_address / ems_address / police_address)"
            elif self.security not in ("starttls", "ssl", "none"):
                self.config_problem = "smtp_security must be starttls, ssl or none"
            if self.config_problem:
                log.error("Active911 alerts disabled: %s", self.config_problem)
                self.enabled = False
        self.send_func = self.smtp_send  # replaceable in tests
        self.last_sent = None
        self.last_error = None
        self._wake = threading.Event()

    # ---- configuration / status ------------------------------------------------
    def disciplines(self):
        return [d for d in DISCIPLINES if self.addresses.get(d)]

    def public_status(self, detail=False):
        st = {"enabled": self.enabled, "disciplines": self.disciplines() if self.enabled else []}
        if detail:
            c = self.app.db.conn()
            counts = dict(c.execute("SELECT status, count(*) FROM alerts GROUP BY status").fetchall())
            st.update(config_problem=self.config_problem, last_sent=self.last_sent, last_error=self.last_error,
                      pending=counts.get("PENDING", 0), failed=counts.get("FAILED", 0), sent=counts.get("SENT", 0))
        return st

    # ---- queueing (called inside the dispatch transaction) -------------------------
    def queue(self, c, call_id, discipline, kind, user, note=None):
        if not self.enabled or not self.addresses.get(discipline):
            return False
        if kind == "AUTO" and c.execute("SELECT 1 FROM alerts WHERE call_id=? AND discipline=? AND kind='AUTO'",
                                        (call_id, discipline)).fetchone():
            return False
        when = _iso_in(AUTO_DELAY_SECONDS) if kind == "AUTO" else now_iso()
        for addr in self.addresses[discipline]:
            c.execute("INSERT INTO alerts(call_id, discipline, kind, to_address, status, created_at, created_by,"
                      " next_try_at, note) VALUES (?,?,?,?, 'PENDING', ?,?,?,?)",
                      (call_id, discipline, kind, addr, now_iso(), user["id"] if user else None, when, note))
        self.poke()
        return True

    # ---- message -----------------------------------------------------------------
    def build_message(self, c, call_id, discipline, note=None, test=False):
        if test:
            now = datetime.now()
            body = "\n".join([
                "CALL: TEST ALERT - DISREGARD", "ADDR: TEST", f"CITY: {self.default_city or 'TEST'}",
                "ID: TEST", "PRI: P5", f"DATE: {now:%m/%d/%Y}", f"TIME: {now:%H:%M:%S}", "UNIT: TEST",
                "INFO: Test message from the Interim CAD. No response needed."])
            return "CAD TEST ALERT - DISREGARD", body
        k = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
        ct = c.execute("SELECT description FROM call_types WHERE agency_id=? AND code=?",
                       (k["agency_id"], k["call_type"])).fetchone()
        nature = ct[0] if ct else k["call_type"]
        units = [r[0] for r in c.execute(
            "SELECT cu.unit_code FROM call_units cu JOIN units u ON u.id = cu.unit_id"
            " WHERE cu.call_id=? AND cu.cleared_at IS NULL AND u.discipline=? ORDER BY cu.dispatched_at, cu.id",
            (call_id, discipline))]
        if not units:
            units = [r[0] for r in c.execute("SELECT unit_code FROM call_units WHERE call_id=? AND cleared_at IS NULL"
                                             " ORDER BY dispatched_at, id", (call_id,))]
        addr = k["location_address"] or (f"{k['location_lat']},{k['location_lon']}"
                                         if k["location_lat"] is not None and k["location_lon"] is not None else "")
        info = []
        if note:
            info.append(note)
        if k["location_cross"]:
            info.append(f"X-ST {k['location_cross']}")
        if k["location_notes"]:
            info.append(k["location_notes"])
        if discipline == "EMS" or k["discipline"] == "EMS":
            if k["patient_count"]:
                info.append(f"{k['patient_count']} PT")
            if k["chief_complaint"]:
                info.append(f"CC {k['chief_complaint']}")
        if self.include_caller and (k["caller_name"] or k["caller_phone"]):
            info.append("CALLER " + " ".join(x for x in (k["caller_name"], k["caller_phone"]) if x))
        first_note = c.execute("SELECT text FROM call_narrative WHERE call_id=? AND entry_type='NOTE' ORDER BY at, id"
                               " LIMIT 1", (call_id,)).fetchone()
        if first_note:
            info.append(first_note[0])
        now = datetime.now()
        lines = [f"CALL: {_clean(nature, 80)}"]
        if k["location_common"]:
            lines.append(f"PLACE: {_clean(k['location_common'], 120)}")
        lines += [
            f"ADDR: {_clean(addr, 160) or 'UNKNOWN'}",
            f"CITY: {_clean(k['location_city'] or self.default_city, 80)}",
            f"ID: {k['call_number']}",
            f"PRI: P{k['priority']}",
            f"DATE: {now:%m/%d/%Y}",
            f"TIME: {now:%H:%M:%S}",
            f"UNIT: {_clean(', '.join(units), 120)}",
        ]
        if info:
            lines.append(f"INFO: {_clean('; '.join(info), 600)}")
        subject = _clean(f"{k['call_type']} - {addr or 'UNKNOWN'}", 120)
        return subject, "\n".join(lines)

    # ---- delivery ------------------------------------------------------------------
    def smtp_send(self, to_address, subject, body):
        msg = EmailMessage()
        msg["From"] = self.from_address
        msg["To"] = to_address
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.from_address.split("@")[-1] if "@" in self.from_address else None)
        # Plain, unwrapped text (no quoted-printable soft line breaks) so Active911's parser sees whole lines.
        msg.set_content(body, cte="7bit" if body.isascii() else "8bit")
        ctx = ssl.create_default_context()
        if self.security == "ssl":
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=20, context=ctx)
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20)
        try:
            server.ehlo()
            if self.security == "starttls":
                server.starttls(context=ctx)
                server.ehlo()
            if self.username:
                server.login(self.username, self.password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass
            except OSError:
                pass

    def send_test(self, discipline):
        subject, body = self.build_message(None, None, discipline, test=True)
        for addr in self.addresses.get(discipline, []):
            self.send_func(addr, subject, body)
        return body

    def process_once(self):
        """Send every due alert. Returns the number of alerts attempted."""
        db = self.app.db
        c = db.conn()
        due = c.execute("SELECT * FROM alerts WHERE status='PENDING' AND next_try_at <= ? ORDER BY id",
                        (now_iso(),)).fetchall()
        sysreq = SimpleNamespace(user=None)
        from .api import add_narrative, touch_call  # local import: api imports nothing from here
        for a in due:
            subject, body = self.build_message(c, a["call_id"], a["discipline"], a["note"])
            try:
                self.send_func(a["to_address"], subject, body)
            except Exception as e:  # noqa: BLE001 - any mail failure is retried, never crashes the worker
                err = f"{type(e).__name__}: {e}"[:300]
                attempts = a["attempts"] + 1
                final = attempts >= MAX_ATTEMPTS
                self.last_error = f"{now_iso()} {err}"
                log.warning("Active911 alert %s for call %s failed (attempt %s): %s", a["id"], a["call_id"], attempts, err)
                with db.tx() as wc:
                    wc.execute("UPDATE alerts SET attempts=?, last_error=?, status=?, next_try_at=? WHERE id=?",
                               (attempts, err, "FAILED" if final else "PENDING", _iso_in(30 * 2 ** (attempts - 1)),
                                a["id"]))
                    if attempts == 1 or final:
                        add_narrative(wc, sysreq, a["call_id"],
                                      f"Active911 alert ({a['discipline']}) "
                                      + ("FAILED after retries - page crews another way" if final
                                         else "not delivered yet - retrying") + f": {err}", "SYSTEM")
                        touch_call(wc, a["call_id"])
                    db.audit(wc, None, "alerter", "ALERT_FAILED", "call", a["call_id"],
                             {"alert_id": a["id"], "discipline": a["discipline"], "attempt": attempts, "error": err})
                continue
            self.last_sent = now_iso()
            units = re.search(r"^UNIT: (.*)$", body, re.M)
            with db.tx() as wc:
                wc.execute("UPDATE alerts SET status='SENT', sent_at=?, attempts=attempts+1, subject=?, body=?,"
                           " last_error=NULL WHERE id=?", (now_iso(), subject, body, a["id"]))
                add_narrative(wc, sysreq, a["call_id"],
                              f"Active911 alert sent ({a['discipline']})" + (f": {units.group(1)}" if units else ""),
                              "SYSTEM")
                touch_call(wc, a["call_id"])
                db.audit(wc, None, "alerter", "ALERT_SENT", "call", a["call_id"],
                         {"alert_id": a["id"], "discipline": a["discipline"], "kind": a["kind"]})
        return len(due)

    # ---- background worker -------------------------------------------------------------
    def poke(self):
        self._wake.set()

    def start(self):
        if not self.enabled:
            return
        threading.Thread(target=self._loop, daemon=True, name="active911").start()

    def _loop(self):
        while True:
            try:
                self.process_once()
            except Exception:  # noqa: BLE001
                log.exception("Active911 worker error")
            finally:
                self.app.db.close_thread_conn()
            self._wake.wait(AUTO_DELAY_SECONDS)
            self._wake.clear()
