"""HTTP(S) server: sessions, CSRF, security headers, static files, routing."""
import json
import logging
import mimetypes
import os
import ssl
import sys
import threading
from datetime import datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import api, auth, seed
from .archive import Archiver
from .config import Config
from .db import Database
from .util import now_iso, row_to_dict

log = logging.getLogger("cad")
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
COOKIE = "cad_session"
MAX_BODY = 1_000_000

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self';"
                               " frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


class App:
    def __init__(self, config):
        self.config = config
        api.validate_number_formats(config)
        self.db = Database(config.path_of("storage", "db_path"))
        self.db.init_schema()
        self.agency_id = self._ensure_agency()
        self.tls = bool(config.get("server", "tls_cert"))
        self.archiver = Archiver(self, config.path_of("archive", "dir"), config.getint("archive", "interval_seconds"))

    def _ensure_agency(self):
        c = self.db.conn()
        r = c.execute("SELECT id FROM agencies ORDER BY id LIMIT 1").fetchone()
        if r:
            c.execute("UPDATE agencies SET name=?, ori=? WHERE id=?",
                      (self.config.get("agency", "name"), self.config.get("agency", "ori"), r[0]))
            return r[0]
        with self.db.tx() as c:
            aid = c.execute("INSERT INTO agencies(ori, name, created_at) VALUES (?,?,?)",
                            (self.config.get("agency", "ori"), self.config.get("agency", "name"), now_iso())).lastrowid
            c.executemany("INSERT INTO call_types(agency_id, code, description, default_priority) VALUES (?,?,?,?)",
                          [(aid, *t) for t in seed.CALL_TYPES])
            c.executemany("INSERT INTO dispositions(agency_id, code, description) VALUES (?,?,?)",
                          [(aid, *d) for d in seed.DISPOSITIONS])
            self.db.audit(c, None, None, "SYSTEM_INIT", "agency", aid)
        return aid

    def session_user(self, token, background):
        """Validate a session token; returns (session, user) or (None, None)."""
        if not token:
            return None, None
        c = self.db.conn()
        th = auth.token_hash(token)
        row = c.execute("SELECT * FROM sessions WHERE token_hash=?", (th,)).fetchone()
        if not row:
            return None, None
        sess = row_to_dict(row)
        now = datetime.now(timezone.utc)
        idle = timedelta(minutes=self.config.getint("security", "session_idle_minutes"))
        maxage = timedelta(hours=self.config.getint("security", "session_max_hours"))
        last = datetime.fromisoformat(sess["last_seen"].replace("Z", "+00:00"))
        created = datetime.fromisoformat(sess["created_at"].replace("Z", "+00:00"))
        user = c.execute("SELECT * FROM users WHERE id=?", (sess["user_id"],)).fetchone()
        if now - last > idle or now - created > maxage or not user or not user["active"]:
            with self.db.tx() as wc:
                wc.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
                self.db.audit(wc, row_to_dict(user) if user else None, sess["ip"], "SESSION_EXPIRED", "user",
                              sess["user_id"])
            return None, None
        if not background:
            # Plain UPDATE without bumping the board sequence.
            with self.db.write_lock:
                c.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now_iso(), th))
        return sess, row_to_dict(user)


class Req:
    def __init__(self, app, method, path, query, body, ip, user_agent):
        self.app, self.method, self.path, self.query, self.body = app, method, path, query, body
        self.ip, self.user_agent = ip, user_agent
        self.user = None
        self.session = None
        self.set_cookie = None
        self.clear_cookie = False


class Handler(BaseHTTPRequestHandler):
    server_version = "InterimCAD"
    sys_version = ""
    app: App = None  # set by make_server

    def log_message(self, fmt, *args):
        log.info("%s %s", self.client_ip(), fmt % args)

    def client_ip(self):
        if self.app and self.app.config.getbool("server", "trust_proxy"):
            fwd = self.headers.get("X-Forwarded-For")
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0]

    # -- responses ----------------------------------------------------------
    def _send(self, status, body, content_type, extra_headers=None):
        self.send_response(status)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        if self.app.tls:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj, extra_headers=None):
        self._send(status, json.dumps(obj, default=str).encode("utf-8"), "application/json; charset=utf-8",
                   extra_headers)

    def _cookie_header(self, value, clear=False):
        parts = [f"{COOKIE}={'' if clear else value}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if self.app.tls:
            parts.append("Secure")
        if clear:
            parts.append("Max-Age=0")
        return ("Set-Cookie", "; ".join(parts))

    # -- dispatch -----------------------------------------------------------
    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method):
        try:
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                self._api(method, url)
            elif method == "GET":
                self._static(url.path)
            else:
                self._json(405, {"error": "Method not allowed"})
        except Exception:  # noqa: BLE001 - last-resort guard; never leak details to client
            log.exception("Unhandled error")
            try:
                self._json(500, {"error": "Internal server error. The event was logged."})
            except Exception:  # noqa: BLE001
                pass
        finally:
            self.app.db.close_thread_conn()

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        rel = os.path.normpath(path.lstrip("/"))
        full = os.path.join(STATIC_DIR, rel)
        if rel.startswith("..") or not os.path.abspath(full).startswith(STATIC_DIR + os.sep) or not os.path.isfile(full):
            self._send(404, b"Not found", "text/plain")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def _api(self, method, url):
        app = self.app
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        body = {}
        if method in ("POST", "PUT", "DELETE"):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json(413, {"error": "Request too large"})
            raw = self.rfile.read(length) if length else b""
            if raw:
                if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                    return self._json(415, {"error": "JSON required"})
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return self._json(400, {"error": "Invalid JSON"})
                if not isinstance(body, dict):
                    return self._json(400, {"error": "JSON object required"})
            # Reject cross-site requests (defense in depth alongside SameSite cookies + CSRF token).
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                return self._json(403, {"error": "Cross-origin request rejected"})

        for r_method, pattern, fn, mode in api.ROUTES:
            if r_method != method:
                continue
            m = pattern.match(url.path)
            if not m:
                continue
            req = Req(app, method, url.path, query, body, self.client_ip(), self.headers.get("User-Agent"))
            if mode != "public":
                jar = cookies.SimpleCookie(self.headers.get("Cookie") or "")
                token = jar[COOKIE].value if COOKIE in jar else None
                sess, user = app.session_user(token, background=self.headers.get("X-Background") == "1")
                if not user:
                    return self._json(401, {"error": "Not signed in", "code": "AUTH"})
                if method != "GET" and self.headers.get("X-CSRF-Token") != sess["csrf"]:
                    return self._json(403, {"error": "Security token mismatch. Reload the page."})
                restricted = user["must_change_pw"] or (
                    app.config.getbool("security", "require_mfa") and not user["totp_enabled"])
                if mode == "full" and restricted:
                    return self._json(403, {"error": "Complete password change / MFA setup first.",
                                            "code": "SETUP"})
                req.session, req.user = sess, user
            try:
                args = [int(g) for g in m.groups()]
                result = fn(req, *args)
            except api.ApiError as e:
                return self._json(e.status, {"error": e.message, **e.extra})
            headers = []
            if req.set_cookie:
                headers.append(self._cookie_header(req.set_cookie))
            if req.clear_cookie:
                headers.append(self._cookie_header("", clear=True))
            if isinstance(result, api.RawResponse):
                if result.filename:
                    headers.append(("Content-Disposition", f'attachment; filename="{result.filename}"'))
                return self._send(200, result.data, result.content_type, headers)
            return self._json(200, result, headers)
        return self._json(404, {"error": "Not found"})


def make_server(config):
    app = App(config)
    handler = type("BoundHandler", (Handler,), {"app": app})
    host = config.get("server", "host")
    port = config.getint("server", "port")
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    if config.get("server", "tls_cert"):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(config.path_of("server", "tls_cert"), config.path_of("server", "tls_key") or None)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return app, httpd


def start_backup_thread(app, interval_minutes=60, keep=96):
    """Hourly online backups of the database into backup_dir, keeping the most recent `keep`."""
    backup_dir = app.config.path_of("storage", "backup_dir")
    if not backup_dir:
        return

    def loop():
        import time
        while True:
            time.sleep(interval_minutes * 60)
            try:
                os.makedirs(backup_dir, exist_ok=True)
                name = datetime.now().strftime("cad-%Y%m%d-%H%M%S.sqlite3")
                app.db.backup(os.path.join(backup_dir, name))
                app.db.close_thread_conn()
                files = sorted(f for f in os.listdir(backup_dir) if f.startswith("cad-") and f.endswith(".sqlite3"))
                for old in files[:-keep]:
                    os.remove(os.path.join(backup_dir, old))
                log.info("Backup written: %s", name)
            except Exception:  # noqa: BLE001
                log.exception("Backup failed")

    threading.Thread(target=loop, daemon=True, name="backup").start()


def serve(config_path):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    config = Config(config_path)
    app, httpd = make_server(config)
    start_backup_thread(app)
    app.archiver.start()
    if app.archiver.dir:
        log.info("Archiving every call to %s (every %ss)", app.archiver.dir, app.archiver.interval)
    scheme = "https" if app.tls else "http"
    if not app.tls:
        log.warning("TLS is NOT configured. Use only on a trusted, isolated network, and configure tls_cert/tls_key.")
    log.info("Interim CAD listening on %s://%s:%s", scheme, config.get("server", "host"), config.get("server", "port"))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
