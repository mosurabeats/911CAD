"""Fire/EMS dispatch and Active911 alerting, tested end-to-end against a real (local) SMTP server."""
import os
import re
import socketserver
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_api import ADMIN_PW, Client  # noqa: E402

from cad import auth  # noqa: E402
from cad.alerts import _clean  # noqa: E402
from cad.config import Config  # noqa: E402
from cad.server import App, make_server  # noqa: E402
from cad.util import now_iso  # noqa: E402


class FakeSMTP(socketserver.ThreadingTCPServer):
    """Minimal SMTP server: records each message; can be told to refuse mail."""
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.messages = []
        self.fail = False
        super().__init__(("127.0.0.1", 0), SMTPHandler)


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        w = self.wfile.write
        w(b"220 fake ESMTP\r\n")
        data, buf = False, []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if data:
                if line in (b".\r\n", b".\n"):
                    data = False
                    self.server.messages.append(b"".join(buf).decode("utf-8", "replace"))
                    buf = []
                    w(b"250 OK queued\r\n")
                else:
                    buf.append(line[1:] if line.startswith(b"..") else line)
                continue
            cmd = line.strip().upper()
            if cmd.startswith(b"EHLO"):
                w(b"250-fake\r\n250 8BITMIME\r\n")
            elif cmd.startswith(b"MAIL"):
                w(b"451 Try again later\r\n" if self.server.fail else b"250 OK\r\n")
            elif cmd.startswith((b"HELO", b"RCPT", b"RSET", b"NOOP")):
                w(b"250 OK\r\n")
            elif cmd == b"DATA":
                data = True
                w(b"354 End data with <CR><LF>.<CR><LF>\r\n")
            elif cmd == b"QUIT":
                w(b"221 Bye\r\n")
                return
            else:
                w(b"502 Not implemented\r\n")


class FireEmsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.smtp = FakeSMTP()
        threading.Thread(target=cls.smtp.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Config(None, {
            "server": {"host": "127.0.0.1", "port": "0"},
            "storage": {"db_path": os.path.join(cls.tmp.name, "f.sqlite3"), "backup_dir": ""},
            "archive": {"dir": os.path.join(cls.tmp.name, "Calls")},
            "security": {"require_mfa": "false"},
            "agency": {"name": "Test FD"},
            "active911": {"enabled": "true", "fire_address": "1234-fire@alert.active911.com",
                          "ems_address": "1234-ems@alert.active911.com", "smtp_host": "127.0.0.1",
                          "smtp_port": str(cls.smtp.server_address[1]), "smtp_security": "none",
                          "from_address": "cad@example.org", "default_city": "Testville"},
        })
        cls.app, cls.httpd = make_server(cfg)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        now = now_iso()
        with cls.app.db.tx() as c:
            c.execute("INSERT INTO users(agency_id, username, full_name, role, pw_hash, must_change_pw, created_at,"
                      " updated_at) VALUES (?,?,?,?,?,0,?,?)",
                      (cls.app.agency_id, "admin", "Admin", "admin", auth.hash_password(ADMIN_PW), now, now))
        cls.a = Client(cls.base)
        cls.a.login("admin", ADMIN_PW)
        for code, typ in (("E1", "ENGINE"), ("L1", "LADDER"), ("A1", "AMBULANCE_ALS"), ("P1", "PATROL")):
            cls.a.call("POST", "/api/admin/units", {"unit_code": code, "unit_type": typ})
        cls.units = {u["unit_code"]: u for u in cls.a.call("GET", "/api/admin/units")["units"]}

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.smtp.shutdown()
        cls.smtp.server_close()
        cls.tmp.cleanup()

    def send_due(self):
        with self.app.db.tx() as c:
            c.execute("UPDATE alerts SET next_try_at='2000-01-01T00:00:00Z' WHERE status='PENDING'")
        return self.app.alerter.process_once()

    def status(self, code, status, call_id=None):
        self.a.call("POST", f"/api/units/{self.units[code]['id']}/status", {"status": status, "call_id": call_id})

    def test_units_get_discipline_from_type(self):
        self.assertEqual(self.units["E1"]["discipline"], "FIRE")
        self.assertEqual(self.units["A1"]["discipline"], "EMS")
        self.assertEqual(self.units["P1"]["discipline"], "POLICE")

    def test_fire_call_pages_active911_in_cadpage_format(self):
        before = len(self.smtp.messages)
        r = self.a.call("POST", "/api/calls", {
            "call_type": "STRUCT", "location_address": "12 Mill Rd", "location_common": "Riverside Hardware",
            "location_cross": "Main St", "caller_name": "Pat Caller", "caller_phone": "555-0100",
            "narrative": "Smoke showing from the rear. CALL: not a label",
            "assign_units": [self.units["E1"]["id"], self.units["L1"]["id"], self.units["P1"]["id"]]})
        d = self.a.call("GET", f"/api/calls/{r['id']}")
        self.assertEqual(d["call"]["discipline"], "FIRE")
        self.assertEqual(d["call"]["priority"], 1)
        self.assertEqual(len(d["alerts"]), 1)  # one FIRE alert for all fire units dispatched together
        self.assertEqual(self.send_due(), 1)
        msg = self.smtp.messages[before]
        self.assertIn("To: 1234-fire@alert.active911.com", msg)
        for line in ("CALL: Structure Fire", "PLACE: Riverside Hardware", "ADDR: 12 Mill Rd", "CITY: Testville",
                     f"ID: {r['call_number']}", "PRI: P1", "UNIT: E1, L1"):
            self.assertIn(line, msg)
        self.assertRegex(msg, r"INFO: X-ST Main St; Smoke showing from the rear\. CALL - not a label")
        self.assertNotIn("=\n", msg.replace("\r\n", "\n"))  # no quoted-printable soft breaks
        self.assertNotIn("Pat Caller", msg)  # caller details are not paged unless include_caller = true
        self.assertNotIn("P1,", msg)
        narr = " | ".join(n["text"] for n in self.a.call("GET", f"/api/calls/{r['id']}")["narrative"])
        self.assertIn("Active911 alert sent (FIRE): E1, L1", narr)
        # Dispatching more fire units later does not re-page automatically...
        self.status("E1", "AVAILABLE")
        self.status("E1", "DISPATCHED", r["id"])
        self.assertEqual(self.send_due(), 0)
        # ...but a dispatcher can page again (second alarm) by hand.
        self.a.call("POST", f"/api/calls/{r['id']}/alert", {"discipline": "FIRE", "note": "Second alarm"})
        self.assertEqual(self.send_due(), 1)
        self.assertIn("INFO: Second alarm; X-ST Main St", self.smtp.messages[-1])
        self.a.call("POST", f"/api/calls/{r['id']}/alert", {"discipline": "POLICE"}, expect=400)
        self.a.call("POST", f"/api/calls/{r['id']}/close", {"disposition": "EXTINGUISHED"})

    def test_ems_call_patient_fields_and_hospital_time(self):
        r = self.a.call("POST", "/api/calls", {"call_type": "CARDIAC", "location_address": "5 Elm St",
                                               "patient_count": 1, "chief_complaint": "65 y/o male, chest pain",
                                               "assign_units": [self.units["A1"]["id"]]})
        self.assertEqual(self.send_due(), 1)
        msg = self.smtp.messages[-1]
        self.assertIn("To: 1234-ems@alert.active911.com", msg)
        self.assertIn("UNIT: A1", msg)
        self.assertIn("1 PT; CC 65 y/o male, chest pain", msg)
        for st in ("ENROUTE", "ONSCENE", "TRANSPORT", "AT_HOSPITAL"):
            self.status("A1", st)
        self.a.call("PUT", f"/api/calls/{r['id']}", {"destination": "Central Vermont Medical Center"})
        d = self.a.call("GET", f"/api/calls/{r['id']}")
        self.assertEqual(d["call"]["discipline"], "EMS")
        self.assertTrue(d["units"][0]["at_hospital_at"])
        self.assertIn("A1 at hospital", " | ".join(n["text"] for n in d["narrative"]))
        self.status("A1", "IN_QUARTERS")
        self.assertTrue(self.a.call("GET", f"/api/calls/{r['id']}")["units"][0]["cleared_at"])
        board = self.a.call("GET", "/api/board")
        self.assertEqual([c for c in board["calls"] if c["id"] == r["id"]][0]["discipline"], "EMS")
        self.assertTrue(self.a.call("GET", "/api/calls/search?discipline=EMS")["results"])
        self.a.call("POST", f"/api/calls/{r['id']}/close", {"disposition": "TRANSPORT"})
        # The archived record shows discipline, patient info and the hospital time
        self.app.archiver.sweep()
        txt = [os.path.join(dp, f) for dp, _, fs in os.walk(self.app.archiver.dir) for f in fs
               if f == d["call"]["call_number"] + ".txt"][0]
        body = open(txt, encoding="utf-8").read()
        self.assertIn("Discipline: EMS", body)
        self.assertIn("Complaint : 65 y/o male, chest pain", body)
        self.assertIn("Destination: Central Vermont Medical Center", body)
        self.assertIn("At hosp", body)

    def test_police_calls_are_not_paged(self):
        r = self.a.call("POST", "/api/calls", {"call_type": "SUSP", "assign_units": [self.units["P1"]["id"]]})
        self.assertEqual(self.a.call("GET", f"/api/calls/{r['id']}")["alerts"], [])
        self.status("P1", "AVAILABLE")

    def test_mail_server_down_retries_then_delivers(self):
        r = self.a.call("POST", "/api/calls", {"call_type": "CO", "location_address": "9 Pine Ln",
                                               "assign_units": [self.units["L1"]["id"]]})
        self.smtp.fail = True
        try:
            self.send_due()
        finally:
            self.smtp.fail = False
        al = self.a.call("GET", f"/api/calls/{r['id']}")["alerts"][0]
        self.assertEqual((al["status"], al["attempts"]), ("PENDING", 1))
        self.assertIn("451", al["last_error"])
        self.assertIn("retrying", " | ".join(n["text"] for n in self.a.call("GET", f"/api/calls/{r['id']}")["narrative"]))
        self.send_due()
        al = self.a.call("GET", f"/api/calls/{r['id']}")["alerts"][0]
        self.assertEqual(al["status"], "SENT")
        st = self.a.call("GET", "/api/alerts/status")
        self.assertTrue(st["enabled"])
        self.assertEqual(st["pending"], 0)
        self.status("L1", "AVAILABLE")

    def test_officer_cannot_send_alerts(self):
        self.a.call("POST", "/api/admin/users", {"username": "ff1", "full_name": "FF One", "role": "officer",
                                                 "password": "Temp-Password-99"})
        c = Client(self.base)
        c.login("ff1", "Temp-Password-99")
        c.call("POST", "/api/me/password", {"current_password": "Temp-Password-99", "new_password": "New-Password-4567"})
        r = self.a.call("POST", "/api/calls", {"call_type": "GAS"})
        c.call("POST", f"/api/calls/{r['id']}/alert", {"discipline": "FIRE"}, expect=403)

    def test_test_alert_message(self):
        body = self.app.alerter.send_test("FIRE")
        self.assertIn("TEST ALERT - DISREGARD", body)
        self.assertIn("TEST ALERT - DISREGARD", self.smtp.messages[-1])

    def test_clean_strips_labels_and_newlines(self):
        self.assertEqual(_clean("line one\nADDR: fake\tUNIT:x"), "line one ADDR - fake UNIT -x")


class UpgradeTest(unittest.TestCase):
    def test_existing_database_is_upgraded(self):
        """A database created before Fire/EMS support gains the new columns and reference data."""
        from cad import db as dbmod
        old_schema = re.sub(r"\n\s+(discipline|patient_count|chief_complaint|destination|at_hospital_at)\b[^\n]*", "",
                            dbmod.SCHEMA)
        old_schema = re.sub(r",(\s*\n\s*\))", r"\1", old_schema)
        old_schema = re.sub(r"CREATE TABLE IF NOT EXISTS alerts.*?;\s*CREATE INDEX[^;]*;\s*CREATE INDEX[^;]*;", "",
                            old_schema, flags=re.S)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.sqlite3")
            con = sqlite3.connect(path)
            con.executescript(old_schema)
            self.assertNotIn("discipline", {r[1] for r in con.execute("PRAGMA table_info(calls)")})
            con.execute("INSERT INTO meta(key, value) VALUES ('seq','0')")
            con.execute("INSERT INTO agencies(id, name, created_at) VALUES (1, 'Old PD', '2026-11-01T00:00:00Z')")
            con.execute("INSERT INTO call_types(agency_id, code, description, default_priority) VALUES (1,'MEDICAL','Med',1)")
            con.commit()
            con.close()
            app = App(Config(None, {"storage": {"db_path": path}}))
            c = app.db.conn()
            self.assertIn("discipline", {r[1] for r in c.execute("PRAGMA table_info(calls)")})
            self.assertIn("at_hospital_at", {r[1] for r in c.execute("PRAGMA table_info(call_units)")})
            self.assertEqual(c.execute("SELECT discipline FROM call_types WHERE code='MEDICAL'").fetchone()[0], "EMS")
            self.assertEqual(c.execute("SELECT discipline FROM call_types WHERE code='STRUCT'").fetchone()[0], "FIRE")
            self.assertTrue(c.execute("SELECT 1 FROM dispositions WHERE code='EXTINGUISHED'").fetchone())
            app.db.close_thread_conn()


if __name__ == "__main__":
    unittest.main()
