import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.cookiejar import CookieJar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cad import auth  # noqa: E402
from cad.config import Config  # noqa: E402
from cad.server import make_server  # noqa: E402
from cad.util import now_iso  # noqa: E402

ADMIN_PW = "Admin-Password-123"


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = None

    def call(self, method, path, body=None, expect=200, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.csrf:
            req.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(req) as r:
                status, payload = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, payload = e.code, e.read()
        if status != expect:
            raise AssertionError(f"{method} {path}: expected {expect}, got {status}: {payload[:300]!r}")
        return payload if raw else json.loads(payload)

    def login(self, username, password, totp=None):
        body = {"username": username, "password": password}
        if totp:
            body["totp"] = totp
        r = self.call("POST", "/api/login", body)
        if r.get("ok"):
            self.csrf = self.call("GET", "/api/me")["csrf"]
        return r


class CadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Config(None, {
            "server": {"host": "127.0.0.1", "port": "0"},
            "storage": {"db_path": os.path.join(cls.tmp.name, "t.sqlite3"), "backup_dir": ""},
            "security": {"require_mfa": "false", "lockout_attempts": "3"},
            "agency": {"name": "Test PD", "case_prefix": "TPD"},
        })
        cls.app, cls.httpd = make_server(cfg)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        now = now_iso()
        with cls.app.db.tx() as c:
            c.execute("INSERT INTO users(agency_id, username, full_name, role, pw_hash, must_change_pw, created_at,"
                      " updated_at) VALUES (?,?,?,?,?,0,?,?)",
                      (cls.app.agency_id, "admin", "Admin User", "admin", auth.hash_password(ADMIN_PW), now, now))
        cls.admin = Client(cls.base)
        cls.admin.login("admin", ADMIN_PW)
        for code in ("P1", "P2", "P3"):
            cls.admin.call("POST", "/api/admin/units", {"unit_code": code, "unit_type": "PATROL"})

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def units(self):
        return {u["unit_code"]: u for u in self.admin.call("GET", "/api/board")["units"]}

    def make_user(self, username, role):
        self.admin.call("POST", "/api/admin/users", {"username": username, "full_name": username.title(),
                                                     "role": role, "password": "Temp-Password-99"})
        cl = Client(self.base)
        cl.login(username, "Temp-Password-99")
        # Restricted until password changed
        cl.call("GET", "/api/board", expect=403)
        cl.call("POST", "/api/me/password", {"current_password": "Temp-Password-99",
                                             "new_password": "New-Password-4567"})
        return cl

    # ------------------------------------------------------------------
    def test_unauthenticated_rejected(self):
        Client(self.base).call("GET", "/api/board", expect=401)

    def test_csrf_required(self):
        c = Client(self.base)
        c.login("admin", ADMIN_PW)
        c.csrf = None
        c.call("POST", "/api/calls", {"call_type": "ALARM"}, expect=403)

    def test_call_lifecycle(self):
        a = self.admin
        r = a.call("POST", "/api/calls", {"call_type": "domestic", "location_address": "1 Main St",
                                          "caller_name": "Jane Roe", "caller_phone": "802-555-0100",
                                          "narrative": "Caller reports yelling next door"})
        cid = r["id"]
        self.assertRegex(r["call_number"], r"^\d\d-\d{6}$")
        d = a.call("GET", f"/api/calls/{cid}")
        self.assertEqual(d["call"]["priority"], 1)  # default priority from call type
        self.assertEqual(d["call"]["status"], "PENDING")
        u = self.units()
        # Dispatch P1 and P2
        a.call("POST", f"/api/units/{u['P1']['id']}/status", {"status": "DISPATCHED", "call_id": cid})
        a.call("POST", f"/api/units/{u['P2']['id']}/status", {"status": "DISPATCHED", "call_id": cid})
        a.call("POST", f"/api/units/{u['P1']['id']}/status", {"status": "ONSCENE"})
        d = a.call("GET", f"/api/calls/{cid}")
        self.assertEqual(d["call"]["status"], "ACTIVE")
        p1 = [x for x in d["units"] if x["unit_code"] == "P1"][0]
        self.assertTrue(p1["dispatched_at"] and p1["onscene_at"] and p1["is_primary"])
        # Persons & vehicles
        a.call("POST", f"/api/calls/{cid}/persons", {"role": "SUSPECT", "last_name": "Doe", "first_name": "John",
                                                     "dob": "1980-01-02"})
        a.call("POST", f"/api/calls/{cid}/persons", {"role": "VICTIM", "dob": "01/02/1980"}, expect=400)
        a.call("POST", f"/api/calls/{cid}/vehicles", {"role": "SUSPECT", "plate": "abc 123", "plate_state": "vt"})
        a.call("POST", f"/api/calls/{cid}/narrative", {"text": "Units separating parties"})
        cn = a.call("POST", f"/api/calls/{cid}/case-number")["case_number"]
        self.assertRegex(cn, r"^\d\dTPD-\d{5}$")
        a.call("POST", f"/api/calls/{cid}/case-number", expect=409)
        # Edit with optimistic concurrency
        ver = a.call("GET", f"/api/calls/{cid}")["call"]["version"]
        a.call("PUT", f"/api/calls/{cid}", {"priority": 2, "version": ver})
        a.call("PUT", f"/api/calls/{cid}", {"priority": 3, "version": ver}, expect=409)
        # Search
        self.assertTrue(any(x["id"] == cid for x in a.call("GET", "/api/calls/search?name=doe")["results"]))
        self.assertTrue(any(x["id"] == cid for x in a.call("GET", "/api/calls/search?plate=ABC123")["results"]))
        self.assertTrue(any(x["id"] == cid for x in a.call("GET", "/api/calls/search?q=separating")["results"]))
        # Close -> units auto-cleared
        a.call("POST", f"/api/calls/{cid}/close", {"disposition": "ARREST"})
        u = self.units()
        self.assertEqual(u["P1"]["status"], "AVAILABLE")
        self.assertIsNone(u["P1"]["current_call_id"])
        d = a.call("GET", f"/api/calls/{cid}")
        self.assertEqual(d["call"]["status"], "CLOSED")
        self.assertTrue(all(x["cleared_at"] for x in d["units"]))
        texts = " | ".join(n["text"] for n in d["narrative"])
        self.assertIn("P1 on scene", texts)
        self.assertIn("Disposition: ARREST", texts)
        # Cannot dispatch to a closed call; reopen requires reason
        a.call("POST", f"/api/units/{u['P3']['id']}/status", {"status": "DISPATCHED", "call_id": cid}, expect=409)
        a.call("POST", f"/api/calls/{cid}/reopen", {}, expect=400)
        a.call("POST", f"/api/calls/{cid}/reopen", {"reason": "More info"})

    def test_reassign_unit_clears_previous_call(self):
        a = self.admin
        c1 = a.call("POST", "/api/calls", {"call_type": "PARKING"})["id"]
        c2 = a.call("POST", "/api/calls", {"call_type": "MVAINJ"})["id"]
        p3 = self.units()["P3"]["id"]
        a.call("POST", f"/api/units/{p3}/status", {"status": "DISPATCHED", "call_id": c1})
        a.call("POST", f"/api/units/{p3}/status", {"status": "DISPATCHED", "call_id": c2})
        d1 = a.call("GET", f"/api/calls/{c1}")
        self.assertTrue(d1["units"][0]["cleared_at"])
        self.assertEqual(self.units()["P3"]["current_call_id"], c2)
        a.call("POST", f"/api/units/{p3}/status", {"status": "AVAILABLE"})

    def test_back_entry_and_time_correction(self):
        a = self.admin
        r = a.call("POST", "/api/calls", {"call_type": "LARCENY", "received_at": "2026-11-20T14:05:00-05:00"})
        d = a.call("GET", f"/api/calls/{r['id']}")
        self.assertEqual(d["call"]["received_at"], "2026-11-20T19:05:00Z")
        a.call("POST", "/api/calls", {"call_type": "LARCENY", "received_at": "2099-01-01T00:00:00Z"}, expect=400)
        p2 = self.units()["P2"]["id"]
        a.call("POST", f"/api/units/{p2}/status", {"status": "DISPATCHED", "call_id": r["id"]})
        a.call("POST", f"/api/units/{p2}/status", {"status": "AVAILABLE"})
        cu = a.call("GET", f"/api/calls/{r['id']}")["units"][0]
        a.call("PUT", f"/api/call-units/{cu['id']}", {"dispatched_at": "2026-11-20T14:07:00-05:00",
                                                       "cleared_at": "2026-11-20T14:40:00-05:00"})
        cu = a.call("GET", f"/api/calls/{r['id']}")["units"][0]
        self.assertEqual(cu["dispatched_at"], "2026-11-20T19:07:00Z")

    def test_roles(self):
        officer = self.make_user("officer1", "officer")
        records = self.make_user("records1", "records")
        disp = self.make_user("disp1", "dispatcher")
        u = self.units()
        cid = disp.call("POST", "/api/calls", {"call_type": "SUSP"})["id"]
        # Officer cannot change a unit they are not signed on to
        officer.call("POST", f"/api/units/{u['P3']['id']}/status", {"status": "ONSCENE", "call_id": cid}, expect=403)
        officer.call("POST", f"/api/units/{u['P3']['id']}/signon", {})
        officer.call("POST", f"/api/units/{u['P3']['id']}/status", {"status": "ENROUTE", "call_id": cid})
        officer.call("POST", f"/api/calls/{cid}/narrative", {"text": "Officer note"})
        officer.call("POST", f"/api/calls/{cid}/close", {"disposition": "GOA"}, expect=403)
        officer.call("GET", "/api/admin/users", expect=403)
        officer.call("GET", "/api/export", expect=403, raw=True)
        records.call("POST", "/api/calls", {"call_type": "SUSP"}, expect=403)
        records.call("GET", f"/api/calls/{cid}")
        disp.call("GET", "/api/audit", expect=403)
        disp.call("POST", f"/api/calls/{cid}/close", {"disposition": "GOA"})
        officer.call("POST", f"/api/units/{u['P3']['id']}/signoff", {})

    def test_lockout_and_audit_chain(self):
        self.admin.call("POST", "/api/admin/users", {"username": "lockme", "full_name": "Lock Me",
                                                     "role": "dispatcher", "password": "Temp-Password-99"})
        c = Client(self.base)
        for _ in range(3):
            c.call("POST", "/api/login", {"username": "lockme", "password": "wrong"}, expect=401)
        c.call("POST", "/api/login", {"username": "lockme", "password": "Temp-Password-99"}, expect=423)
        audit = self.admin.call("GET", "/api/audit?action=LOGIN_FAIL")
        self.assertGreaterEqual(len(audit["entries"]), 3)
        self.assertTrue(audit["chain_ok"])
        # Tamper detection: bypass the triggers by recomputing nothing; just verify triggers block edits.
        import sqlite3
        with self.assertRaises(sqlite3.DatabaseError):
            self.app.db.conn().execute("UPDATE audit_log SET action='X' WHERE id=1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.app.db.conn().execute("DELETE FROM call_narrative")

    def test_mfa(self):
        cl = self.make_user("mfauser", "dispatcher")
        setup = cl.call("POST", "/api/me/mfa/begin")
        cl.call("POST", "/api/me/mfa/confirm", {"code": "000000"}, expect=400)
        cl.call("POST", "/api/me/mfa/confirm", {"code": auth.totp_now(setup["secret"])})
        c2 = Client(self.base)
        self.assertTrue(c2.login("mfauser", "New-Password-4567")["mfa_required"])
        c2.call("POST", "/api/login", {"username": "mfauser", "password": "New-Password-4567", "totp": "123456"},
                expect=401)
        # Same code as enrollment step is rejected as replay; the next step's code would work.
        import time
        future = auth.totp_now(setup["secret"], time.time() + 30)
        self.assertTrue(c2.login("mfauser", "New-Password-4567", totp=future)["ok"])

    def test_export_package(self):
        a = self.admin
        a.call("POST", "/api/calls", {"call_type": "ANIMAL", "location_address": "99 State St"})
        data = a.call("GET", "/api/export", raw=True)
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
        for n in ("export.json", "README.txt", "SCHEMA.sql", "csv/calls.csv", "csv/call_narrative.csv",
                  "csv/call_units.csv", "csv/call_persons.csv", "csv/audit_log.csv"):
            self.assertIn(n, names)
        doc = json.loads(z.read("export.json"))
        self.assertTrue(any(c["location_address"] == "99 State St" for c in doc["calls"]))
        self.assertIn("narrative", doc["calls"][0])
        self.assertNotIn("pw_hash", doc["users"][0])
        self.assertNotIn("pw_hash", z.read("csv/users.csv").decode())
        for n in names:
            if n.startswith("csv/"):
                self.assertTrue(z.read(n).decode().split("\n")[0].startswith("id"), n)

    def test_static_and_traversal(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn("Content-Security-Policy", r.headers)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/../cad/db.py")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/%2e%2e/config.example.ini")


if __name__ == "__main__":
    unittest.main()
