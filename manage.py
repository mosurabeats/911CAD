#!/usr/bin/env python3
"""Interim CAD management commands.

  python manage.py serve                       Run the server
  python manage.py create-admin USERNAME       Create an administrator (prompts for password)
  python manage.py reset-password USERNAME     Set a temporary password (user must change at login)
  python manage.py unlock USERNAME             Clear a lockout
  python manage.py backup [PATH]               Write a consistent copy of the database
  python manage.py export [--from D] [--to D] [-o FILE.zip]   Full data export (JSON + CSV)
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


def main():
    ap = argparse.ArgumentParser(description="Interim CAD management")
    ap.add_argument("--config", default=os.path.join(HERE, "config.ini"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
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
    sub.add_parser("verify-audit")
    sub.add_parser("make-cert")
    args = ap.parse_args()

    if not os.path.exists(args.config) and args.cmd != "make-cert":
        print(f"Config file not found: {args.config}\nCopy config.example.ini to config.ini and edit it first.")
        return 2
    cfg = Config(args.config)

    if args.cmd == "serve":
        serve(args.config)
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
