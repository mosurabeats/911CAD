# Interim CAD

A self-hosted computer-aided dispatch and call-logging system that keeps a police department running while its state or vendor CAD/RMS is offline. It also preserves every record so the data can be imported into the restored system or a replacement.

It was built to get an agency through an outage of several weeks (for example, the Valcour outage). The data model already carries an agency ID on every record, so the same codebase can grow into a regional or statewide system. See [docs/ROADMAP.md](docs/ROADMAP.md).

> **Read [docs/SECURITY_CJIS.md](docs/SECURITY_CJIS.md) before going live.** This system will hold criminal justice information. Your agency's CJIS obligations still apply during an outage. Coordinate with your agency's TAC and the state CJIS Systems Officer.

## What it does

| Area | Features |
|---|---|
| **Calls for service** | Calls numbered in the department format (`26MPD0001`, configurable). Call type and priority, source (911, phone, walk-in, officer-initiated, etc.), location with cross street and common place, caller info, summary. Case numbers on demand (`26MPD-00042`). Dispositions. Close and reopen (reopen needs a reason). |
| **Chronology** | Append-only narrative per call. Every unit status change, assignment, priority or type change, and close is time-stamped automatically. User notes can't be edited or deleted: corrections go in as new entries. |
| **Units** | Live status board: Available, Dispatched, En route, On scene, Transport, Busy, Out of service, Off duty. Per-call unit times (dispatched, en route, on scene, transport, cleared). Moving a unit to a new call clears it from the old one. Crew and officer sign-on. Full status history. |
| **Persons and vehicles** | Attach complainants, victims, suspects, witnesses, drivers and others (name, DOB, address, OLN, descriptors), plus vehicles (plate, state, year, make, model, color, VIN). Removed records are kept, flagged as deleted. |
| **Back-entry** | Enter calls from paper logs with their original received time, and correct unit times. Every correction is recorded. |
| **Search** | By call or case number, address, person name, plate, unit, call type, status, date range, or any narrative text. Prior calls at the same address show on each call. |
| **Multiple dispatchers** | Any number of dispatch consoles, desk PCs and MDTs work at once against one server, and every screen updates within about 3 seconds. If two people edit the same call's details at the same moment, the second person is told to reload instead of silently overwriting the first person's changes. Narrative entries and unit status changes never conflict. |
| **Network-drive archive** | Optionally keeps a copy of every call in a folder such as `I:\CAD\Calls`: a readable `.txt` call record and a complete `.json` file per call, plus a `Daily\YYYY-MM-DD.csv` call log. Files are rewritten within about 30 seconds whenever a call changes. If the drive drops, dispatch keeps working and the copy catches up when the drive is back. |
| **Export** | One-click ZIP with every record as nested JSON plus one CSV per table, the schema, and a README for the vendor doing the import. See [docs/DATA_MIGRATION.md](docs/DATA_MIGRATION.md). |
| **Security** | Role-based access: admin, supervisor, dispatcher, officer, records. Optional or enforced TOTP two-factor (any authenticator app). Account lockout. 30-minute inactivity timeout. Forced change of temporary passwords. CSRF protection, strict CSP and security headers, TLS. |
| **Audit** | Every sign-in (including failures), call view, search, change and export is logged. Each audit entry is hash-chained to the previous one, so tampering is detectable (`manage.py verify-audit`). Database triggers block edits and deletes of the audit log, narratives and calls. |
| **Resilience** | A single SQLite file in WAL mode. Automatic hourly backups (the last 96 are kept). Runs on Windows or Linux on any office PC. |

There are **no third-party dependencies**: Python 3.10+ standard library only, and no internet or CDN access is needed at runtime. This keeps installation fast, works on an isolated network, and leaves no outside supply chain to vet.

## Quick start

```bash
# 1. Install Python 3.10+ (Windows: python.org installer, check "Add to PATH")
# 2. Get this folder onto the server PC, then:
cp config.example.ini config.ini          # Windows: copy config.example.ini config.ini
#    edit config.ini: agency name, ORI, case prefix, TLS cert paths
python manage.py create-admin yourname    # prompts for a password
python manage.py serve                    # or double-click start-cad.bat on Windows
```

Then go to `https://<server-address>:8443` from any dispatch console, desk PC or MDT on the department network.

**First-time setup in the app (Admin tab):**
1. **Units**: add your unit or call-sign codes.
2. **Users**: create an account for each dispatcher, officer and supervisor with a temporary password. Users must change it, and enroll two-factor if `require_mfa = true`, at first sign-in.
3. **Call types / Dispositions**: a standard set is preloaded. Edit it to match your agency.

Full installation, TLS, backup and Windows-service instructions are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Daily use

- **F2** opens a new call. **Esc** closes the open call or dialog. **Ctrl+Enter** saves a narrative entry.
- Open a call, then use a unit's **DISP / ENR / ONS / TRN / AVL** buttons on the board. Status changes record their times on the call automatically.
- Officers sign on to their unit from the bar at the top of the screen. They can update their own status, add narrative, add persons and vehicles, create officer-initiated calls, and request case numbers.
- The board refreshes automatically about every 3 seconds on every connected screen.
- **Print** on a call gives a clean printed call record.

## Roles

| Role | Can do |
|---|---|
| `dispatcher` | Create, edit and close calls; dispatch and change any unit; assign crews; correct times |
| `officer` | View; create calls; narrative; persons and vehicles; own-unit status; case numbers |
| `supervisor` | Everything a dispatcher can, plus reopen calls, manage units, view the audit log, export |
| `records` | Read-only view, search, export |
| `admin` | Everything, plus users, call types and dispositions |

## Commands

```
python manage.py serve                  run the server
python manage.py create-admin NAME      create an administrator
python manage.py reset-password NAME    set a temporary password
python manage.py unlock NAME            clear a lockout
python manage.py backup [PATH]          consistent database copy
python manage.py export [--from 2026-11-01] [--to 2026-12-31] [-o out.zip]
python manage.py set-next-number call N  continue numbering from Valcour's last number
python manage.py verify-audit           detect audit-log tampering
python manage.py make-cert              show how to create a TLS certificate
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## Project layout

```
cad/          server (Python standard library only)
  api.py      business logic and endpoints
  db.py       schema, transactions, hash-chained audit log
  auth.py     PBKDF2 passwords, TOTP, sessions
  server.py   HTTP(S) server, sessions, CSRF, headers, backups
static/       browser client (vanilla JS, no external resources)
docs/         deployment, security/CJIS, migration, roadmap
tests/        end-to-end API tests
```
