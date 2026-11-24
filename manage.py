#!/usr/bin/env python3
"""Interim CAD management commands.

  python manage.py serve                       Run the server
  python manage.py create-admin USERNAME       Create an administrator (prompts for password)
  python manage.py reset-password USERNAME     Set a temporary password (user must change at login)
  python manage.py unlock USERNAME             Clear a lockout
  python manage.py backup [PATH]               Write a consistent copy of the database
  python manage.py export [--from D] [--to D] [-o FILE.zip]   Full data export (JSON + CSV)
  python manage.py set-next-number call|case N [--year YYYY]   Set the next number issued
  python manage.py check                       Verify the whole configuration (run after install)
  python manage.py test-alert fire|ems          Send a TEST page to Active911
  python manage.py verify-audit                Check the audit log hash chain for tampering
  python manage.py make-cert                   Print the command to create a self-signed TLS cert

All commands accept --config PATH (default: config.ini next to this file).
"""
import argparse
import getpass
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cad import auth  # noqa: E402
from cad.api import make_export_zip  # noqa: E402
from cad.config import Config  # noqa: E402
from cad.server import App, serve  # noqa: E402
from cad.util import now_iso, parse_iso  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def prompt_password(cfg, username):
    min_len = cfg.getint("security", "password_min_length")
    while True:
        pw = getpass.getpass(f"Password for {username} (min {min_len} chars): ")
        problems = auth.password_problems(pw, min_len, username)
        if problems:
            print("Password " + "; ".join(problems) + ".")
            continue
        if getpass.getpass("Confirm password: ") != pw:
            print("Passwords do not match.")
            continue
        return pw


def ask(question, default=""):
    ans = input(f"{question}" + (f" [{default}]" if default else "") + ": ").strip()
    return ans or default


def setup_single_pc(config_path):
    """Guided setup: config for this computer only, first administrator, starting call number."""
    print("\n=== Interim CAD setup (this computer only) ===\n")
    if os.path.exists(config_path):
        print(f"A configuration already exists at {config_path}.")
        if ask("Keep it and only add an administrator / call number? (y/n)", "y").lower() != "y":
            print("Setup cancelled. Delete or rename config.ini first if you want to start over.")
            return 1
    else:
        with open(os.path.join(HERE, "config.example.ini"), encoding="utf-8") as f:
            text = f.read()
        agency = ask("Agency name", "Montpelier Police Department")
        prefix = ask("Letters in your call numbers (26MPD0001 -> MPD)", "MPD").upper()
        import re as _re
        subs = {
            r"(?m)^host = .*$": "host = 127.0.0.1",
            r"(?m)^port = .*$": "port = 8080",
            r"(?m)^tls_cert = .*$": "tls_cert =",
            r"(?m)^tls_key = .*$": "tls_key =",
            r"(?m)^name = .*$": f"name = {agency}",
            r"(?m)^call_number_format = .*$": "call_number_format = {yy}" + prefix + "{seq:04d}",
            r"(?m)^case_prefix = .*$": f"case_prefix = {prefix}",
        }
        for pat, val in subs.items():
            text = _re.sub(pat, lambda _m, v=val: v, text)
        archive = ask("Folder to keep a copy of every call in (e.g. I:\\CAD\\Calls), or leave blank", "")
        if archive:
            text = _re.sub(r"(?m)^dir =.*$", lambda _m: f"dir = {archive}", text, count=1)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\nSaved settings to {config_path}")

    cfg = Config(config_path)
    app = App(cfg)
    db = app.db
    if not db.conn().execute("SELECT 1 FROM users WHERE role='admin' AND active=1").fetchone():
        print("\nCreate your administrator account (you will use this to sign in).")
        username = ask("Username", getpass.getuser().lower())
        full = ask("Your full name", username)
        pw = prompt_password(cfg, username)
        now = now_iso()
        with db.tx() as c:
            uid = c.execute("INSERT INTO users(agency_id, username, full_name, role, pw_hash, must_change_pw,"
                            " created_at, updated_at) VALUES (?,?,?,?,?,0,?,?)",
                            (app.agency_id, username, full, "admin", auth.hash_password(pw), now, now)).lastrowid
            db.audit(c, None, "cli", "USER_CREATE", "user", uid, {"username": username, "role": "admin"})
        print(f"Administrator '{username}' created.")

    year = datetime.now().year
    issued = db.conn().execute("SELECT value FROM counters WHERE agency_id=? AND year=? AND kind='call'",
                               (app.agency_id, year)).fetchone()
    if not issued:
        from cad.api import format_number
        print(f"\nValcour has already used call numbers this year. To avoid duplicates when the data goes back,")
        print("continue after Valcour's last number. Leave blank to start at 1.")
        nxt = ask("Next call number to use (just the number, e.g. 4512)", "")
        if nxt.isdigit() and int(nxt) > 1:
            with db.tx() as c:
                c.execute("INSERT INTO counters(agency_id, year, kind, value) VALUES (?,?,?,?)",
                          (app.agency_id, year, "call", int(nxt) - 1))
                db.audit(c, None, "cli", "COUNTER_SET", "counter", f"call-{year}", {"next": int(nxt)})
        print("First call number will be " + format_number(cfg, "call", year, int(nxt) if nxt.isdigit() else 1))

    print("\nSetup complete. Start the CAD by double-clicking the start file")
    print("(Windows: start-cad.bat   Mac: start-cad.command), or run: python manage.py serve --open")
    print("At first sign-in you'll set up two-factor with an authenticator app on your phone.\n")
    return 0


def run_checks(cfg, app):
    """Post-install verification for IT. Prints PASS / WARN / FAIL per item; exit code 1 if any FAIL."""
    import ssl
    import tempfile
    from cad.server import is_local_only
    results = []

    def res(level, item, detail=""):
        results.append(level)
        print(f"  [{level}] {item}" + (f" - {detail}" if detail else ""))

    print("\nInterim CAD configuration check\n")
    local = is_local_only(cfg)
    res("PASS", "Mode", "single computer (127.0.0.1 only)" if local else f"network, listening on {cfg.get('server', 'host')}:{cfg.get('server', 'port')}")
    cert, key = cfg.path_of("server", "tls_cert"), cfg.path_of("server", "tls_key")
    if cert:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key or None)
            res("PASS", "TLS certificate and key load", cert)
        except (OSError, ssl.SSLError) as e:
            res("FAIL", "TLS certificate", f"{cert}: {e}")
    elif local:
        res("PASS", "TLS", "not needed in single-computer mode")
    elif cfg.getbool("server", "allow_insecure_http"):
        res("FAIL", "TLS", "allow_insecure_http = true - not acceptable for production")
    else:
        res("FAIL", "TLS", "tls_cert / tls_key not set - the server will not start")
    if not local:
        if app.allowed_networks:
            res("PASS", "Allowed networks", ", ".join(str(n) for n in app.allowed_networks))
        else:
            res("WARN", "Allowed networks", "empty - any address that reaches the port can try to sign in")
    res("PASS" if cfg.getbool("security", "require_mfa") else "FAIL", "Two-factor sign-in required",
        "yes" if cfg.getbool("security", "require_mfa") else "require_mfa = false")
    for section, key_, label in (("archive", "dir", "Call archive folder (I: drive copy)"),
                                 ("audit", "mirror_dir", "Audit mirror folder"),
                                 ("storage", "backup_dir", "Backup folder")):
        d = cfg.path_of(section, key_)
        if not d:
            res("WARN", label, "not configured")
            continue
        if not local and len(d) > 1 and d[1] == ":" and d[0].upper() not in "C":
            res("WARN", label, f"{d} uses a drive letter; a background service usually cannot see mapped drives - use \\\\server\\share")
        try:
            os.makedirs(d, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=d, prefix=".cad-write-test-", delete=True):
                pass
            res("PASS", label, f"{d} is writable (by the account running this check)")
        except OSError as e:
            res("FAIL", label, f"{d}: {e}")
    db_path = cfg.path_of("storage", "db_path")
    if db_path.startswith("\\\\") or (len(db_path) > 1 and db_path[1] == ":" and db_path[0].upper() != "C"):
        res("WARN", "Database location", f"{db_path} looks like a network drive - keep the live database on a local disk")
    else:
        res("PASS", "Database location", db_path)
    c = app.db.conn()
    admins = c.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
    res("PASS" if admins else "FAIL", "Administrator account", f"{admins} active" if admins else "none - run: manage.py create-admin NAME")
    units = c.execute("SELECT count(*) FROM units WHERE active=1").fetchone()[0]
    res("PASS" if units else "WARN", "Units configured", str(units) if units else "none yet - add them in Admin > Units")
    from cad.api import format_number
    year = datetime.now().year
    nxt = (c.execute("SELECT value FROM counters WHERE agency_id=? AND year=? AND kind='call'",
                     (app.agency_id, year)).fetchone() or [0])[0] + 1
    res("PASS", "Next call number", format_number(cfg, "call", year, nxt)
        + ("" if nxt > 1 else "  (starts at 1 - use set-next-number to continue after Valcour's last number)"))
    al = app.alerter
    if cfg.getbool("active911", "enabled"):
        if al.enabled:
            res("PASS", "Active911 alerts", "on for " + ", ".join(al.disciplines())
                + f" via {al.smtp_host}:{al.smtp_port} ({al.security}) - send one with: manage.py test-alert fire")
        else:
            res("FAIL", "Active911 alerts", al.config_problem or "misconfigured")
    else:
        res("WARN", "Active911 alerts", "off ([active911] enabled = false)")
    ok, n, bad = app.db.verify_audit()
    res("PASS" if ok else "FAIL", "Audit log integrity", f"{n} entries verified" if ok else f"chain broken at entry {bad}")
    fails, warns = results.count("FAIL"), results.count("WARN")
    print(f"\n{fails} problem(s), {warns} warning(s).\n")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description="Interim CAD management")
    ap.add_argument("--config", default=os.path.join(HERE, "config.ini"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--open", action="store_true", help="open the CAD in the browser once it starts")
    sub.add_parser("setup", help="guided first-time setup for use on this one computer")
    p = sub.add_parser("create-admin")
    p.add_argument("username")
    p.add_argument("--full-name", default=None)
    p = sub.add_parser("reset-password")
    p.add_argument("username")
    p = sub.add_parser("unlock")
    p.add_argument("username")
    p = sub.add_parser("backup")
    p.add_argument("path", nargs="?")
    p = sub.add_parser("export")
    p.add_argument("--from", dest="date_from")
    p.add_argument("--to", dest="date_to")
    p.add_argument("-o", "--output")
    p = sub.add_parser("set-next-number")
    p.add_argument("kind", choices=["call", "case"])
    p.add_argument("next", type=int)
    p.add_argument("--year", type=int, default=datetime.now().year)
    sub.add_parser("verify-audit")
    p = sub.add_parser("test-alert", help="send a TEST alert to the Active911 address for a discipline")
    p.add_argument("discipline", choices=["fire", "ems", "police"])
    sub.add_parser("check", help="verify configuration, certificate, folders, accounts and audit log")
    sub.add_parser("make-cert")
    args = ap.parse_args()

    if args.cmd == "setup":
        return setup_single_pc(args.config)
    if not os.path.exists(args.config) and args.cmd != "make-cert":
        print(f"Config file not found: {args.config}\nCopy config.example.ini to config.ini and edit it first.")
        return 2
    cfg = Config(args.config)

    if args.cmd == "serve":
        serve(args.config, open_browser=args.open)
        return 0
    if args.cmd == "make-cert":
        print("Create a self-signed certificate (replace cad.local / IP with your server's name and address):\n")
        print('  openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes -keyout data/tls.key -out data/tls.crt '
              '-subj "/CN=cad.local" -addext "subjectAltName=DNS:cad.local,IP:192.168.1.10"\n')
        print("Then set tls_cert = data/tls.crt and tls_key = data/tls.key in config.ini.")
        print("Better: have your IT provider issue a certificate from your internal CA.")
        return 0

    app = App(cfg)
    db = app.db

    if args.cmd == "create-admin":
        if db.conn().execute("SELECT 1 FROM users WHERE username=?", (args.username,)).fetchone():
            print("User already exists.")
            return 1
        full = args.full_name or input("Full name: ").strip() or args.username
        pw = prompt_password(cfg, args.username)
        now = now_iso()
        with db.tx() as c:
            uid = c.execute("INSERT INTO users(agency_id, username, full_name, role, pw_hash, must_change_pw,"
                            " created_at, updated_at) VALUES (?,?,?,?,?,0,?,?)",
                            (app.agency_id, args.username, full, "admin", auth.hash_password(pw), now, now)).lastrowid
            db.audit(c, None, "cli", "USER_CREATE", "user", uid, {"username": args.username, "role": "admin"})
        print(f"Administrator '{args.username}' created.")
    elif args.cmd in ("reset-password", "unlock"):
        u = db.conn().execute("SELECT * FROM users WHERE username=?", (args.username,)).fetchone()
        if not u:
            print("No such user.")
            return 1
        with db.tx() as c:
            if args.cmd == "reset-password":
                pw = prompt_password(cfg, args.username)
                c.execute("UPDATE users SET pw_hash=?, must_change_pw=1, failed_attempts=0, locked_until=NULL"
                          " WHERE id=?", (auth.hash_password(pw), u["id"]))
                c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
            else:
                c.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (u["id"],))
            db.audit(c, None, "cli", "USER_" + args.cmd.upper().replace("-", "_"), "user", u["id"])
        print("Done.")
    elif args.cmd == "backup":
        path = args.path or os.path.join(cfg.path_of("storage", "backup_dir"),
                                         datetime.now().strftime("cad-%Y%m%d-%H%M%S.sqlite3"))
        db.backup(path)
        print(f"Backup written to {path}")
    elif args.cmd == "export":
        df = parse_iso(args.date_from) if args.date_from else None
        dt = parse_iso(args.date_to) if args.date_to else None
        out = args.output or datetime.now().strftime("cad-export-%Y%m%d-%H%M%S.zip")
        data = make_export_zip(app, df, dt)
        with open(out, "wb") as f:
            f.write(data)
        with db.tx() as c:
            db.audit(c, None, "cli", "EXPORT", "export", None, {"from": df, "to": dt, "file": out})
        print(f"Export written to {out} ({len(data):,} bytes)")
    elif args.cmd == "set-next-number":
        from cad.api import format_number
        if args.next < 1:
            print("Number must be 1 or more.")
            return 1
        cur = db.conn().execute("SELECT value FROM counters WHERE agency_id=? AND year=? AND kind=?",
                                (app.agency_id, args.year, args.kind)).fetchone()
        if cur and cur[0] >= args.next:
            print(f"Warning: {cur[0]} {args.kind} number(s) already issued for {args.year}. Numbers already in use "
                  "will be skipped automatically, never duplicated.")
        with db.tx() as c:
            c.execute("INSERT INTO counters(agency_id, year, kind, value) VALUES (?,?,?,?)"
                      " ON CONFLICT(agency_id, year, kind) DO UPDATE SET value=excluded.value",
                      (app.agency_id, args.year, args.kind, args.next - 1))
            db.audit(c, None, "cli", "COUNTER_SET", "counter", f"{args.kind}-{args.year}",
                     {"next": args.next, "previous_value": cur[0] if cur else 0})
        print(f"Next {args.kind} number for {args.year}: {format_number(cfg, args.kind, args.year, args.next)}")
    elif args.cmd == "test-alert":
        al = app.alerter
        disc = args.discipline.upper()
        if not al.enabled:
            print("Active911 alerts are not enabled or not configured: " + (al.config_problem or "enabled = false"))
            return 1
        if disc not in al.disciplines():
            print(f"No {args.discipline}_address is set in [active911].")
            return 1
        try:
            body = al.send_test(disc)
        except Exception as e:  # noqa: BLE001
            print(f"FAILED to send: {type(e).__name__}: {e}")
            print("Check smtp_host/port/security/username/password and that this server may reach the mail server.")
            return 1
        print(f"Test alert sent to {', '.join(al.addresses[disc])}:\n\n{body}\n")
        print("It should appear in the Active911 app within a minute.")
    elif args.cmd == "check":
        return run_checks(cfg, app)
    elif args.cmd == "verify-audit":
        ok, n, bad = db.verify_audit()
        if ok:
            print(f"Audit chain OK ({n} entries).")
        else:
            print(f"AUDIT CHAIN BROKEN at entry id {bad} (checked {n}). The log may have been altered.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
