# Roadmap: from interim tool to a regional or statewide system

Phase 0 (this release) is a single-agency, single-server tool for the outage. The decisions below were made so it can grow without a rewrite.

## Already in place for scale

- `agency_id` on every record (users, units, calls, call types, dispositions, number counters).
- Separate numbering per agency and per year.
- Role-based permissions enforced on the server.
- A tamper-evident audit trail.
- Export in plain JSON and CSV with no vendor lock-in.

## Phase 1: hardening for an extended outage (weeks)

- [ ] Per-call printable run card and shift (daily) activity report
- [ ] Simple statistics: calls by type, hour and unit, plus response times (dispatch to on-scene)
- [ ] BOLO / premise-hazard notes by address (officer safety flags shown on matching calls)
- [ ] Unit timers (alert when a unit has been on scene past N minutes without a status check)
- [ ] Converter from `export.json` to whatever import format the state or vendor specifies
- [ ] Scheduled off-machine backup copy

## Phase 2: multi-agency and regional dispatch

- Agency selection at sign-in, with agency-scoped data access (the schema already supports this; add `agency_id` to sessions and to user-to-agency membership).
- Shared regional board, so a dispatch center can serve several towns while each agency still sees only its own records.
- Mutual-aid view: see neighboring agencies' available units.
- Move from SQLite to **PostgreSQL**, with replication and a hot standby.
- Run behind a FIPS-validated TLS reverse proxy. Integrate with directory sign-in (SAML / OIDC through the state identity provider) with MFA.
- Push updates (WebSockets / SSE) instead of polling, for hundreds of consoles.

## Phase 3: statewide

Each item needs state approval, vendor or partner agreements, and CJIS audits:
- **Interfaces**: NCIC / VCIC queries through the state message switch, E911 ANI/ALI and caller location from the PSAP equipment, GIS address verification and mapping (E911 address points), AVL for unit locations.
- **RMS integration**: incident reports, arrests and NIBRS reporting.
- Formal security program: independent penetration test, CJIS audit, change management, a 24/7 support model, disaster recovery in a second state data center.
- Governance: a state agency or consortium owns the system, with a published data-sharing MOU among participating agencies.

## Realistic note

A statewide CAD/RMS is a multi-year, multi-million-dollar program with procurement, legal and CJIS-audit requirements. A small open codebase like this one can serve as a strong **contingency and continuity system**: a vendor-independent fallback that any agency in the state can stand up in an hour the next time a central system goes down. That role alone has real value, and it is a practical way to start the conversation with DPS.
