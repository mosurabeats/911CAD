# Getting the data back into Valcour (or a replacement)

The interim CAD is designed so **nothing is lost**. When the permanent system is available, an export gives the vendor or state everything they need to load the outage-period records.

## Producing the export

- In the app: **Export** tab, optionally choose a date range, then **Download export**. This needs the supervisor, records or admin role.
- On the server: `python manage.py export --from 2026-11-01 --to 2027-01-31 -o mpd-outage-export.zip`

Each export is itself recorded in the audit log.

## What's in the ZIP

| File | Contents |
|---|---|
| `export.json` | Nested: each call with its `narrative[]`, `units[]`, `persons[]` and `vehicles[]`, plus units, unit status log, call types, dispositions and users (no password data). Has `export_meta` with record counts, agency, ORI and range. |
| `csv/calls.csv` | One row per call |
| `csv/call_narrative.csv` | Chronological entries (`entry_type` is NOTE for typed entries, SYSTEM for automatic ones) |
| `csv/call_units.csv` | Per-unit dispatched, en route, on scene, transport and cleared times for each call |
| `csv/call_persons.csv`, `csv/call_vehicles.csv` | Involved parties and vehicles (`deleted=1` means the user removed it; kept for the record) |
| `csv/unit_status_log.csv` | Every unit status change |
| `csv/units.csv`, `csv/call_types.csv`, `csv/dispositions.csv`, `csv/users.csv` | Reference data |
| `csv/audit_log.csv` | Full audit trail |
| `SCHEMA.sql` | Exact table definitions |
| `README.txt` | Field conventions for the importing vendor |

**Conventions:** all timestamps are ISO-8601 UTC (for example `2026-11-24T15:04:05Z`). Join child tables on `call_id` to `calls.id`. `call_number` and `case_number` are the human-facing identifiers.

## Recommended migration process

1. **Freeze**: pick a cutover time. Close or clear all open calls in the interim CAD, or note which ones carry over.
2. **Export** everything (no date range) and make a final `manage.py backup`.
3. **Map codes**: give the vendor `call_types.csv` and `dispositions.csv` so they can map your interim codes to the permanent system's codes. Using the same codes as Valcour in the Admin tab from day one makes this trivial; ask a supervisor who knows the Valcour code table to set them.
4. **Number collisions**: call numbers default to the department's format, `26MPD0001`. Valcour has already issued `26MPD` numbers this year, so **before the first interim call**, find the last call number Valcour issued and continue after it:
   `python manage.py set-next-number call 4512` (the next call will be `26MPD4512`).
   Leave a gap, for example jump to the next round thousand, if you're unsure of the exact last number. Alternatively, ask the Valcour administrator or vendor to reserve a block. If a number is ever already in use, the system skips it automatically, so duplicates can't happen.
5. **Validate**: compare `export_meta.record_counts` with what was imported, and spot-check a sample of calls end to end.
6. **Retain** the final database file and export (encrypted) according to your retention schedule. They are the source record for the outage period.

## Tip: set this up on day one

- Confirm `call_number_format` / `case_number_format` in `config.ini`, then run `set-next-number` to continue from Valcour's last number, before the first call.
- Make the call-type and disposition codes match Valcour's codes as closely as possible.
- Ask the vendor or state early which import format they accept. If they need a specific layout (NIEM, a vendor CSV template), a converter from `export.json` is a small script.
