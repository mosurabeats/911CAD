/* Browser-only stand-in for the Interim CAD server, used by the public demo page.
   Fictional agency and data only. Everything lives in memory and resets on reload. */
"use strict";
(function () {
  const PASSWORD = "demo";
  window.CAD_DEMO = {
    loginHint: [
      "Demo accounts (password for all: demo):",
      "dispatch · officer · sergeant · records · admin",
    ],
  };

  const iso = (d = new Date()) => new Date(Math.floor(d.getTime() / 1000) * 1000).toISOString().replace(".000Z", "Z");
  const ago = (min) => iso(new Date(Date.now() - min * 60000));
  const yy = String(new Date().getFullYear()).slice(2);

  const BASE = ["view", "call.create", "call.edit", "call.close", "narrative", "unit.status.any", "unit.crew", "entity.edit", "time.edit", "alert.send"];
  const PERMS = {
    admin: [...BASE, "call.reopen", "unit.manage", "export", "audit", "admin"],
    supervisor: [...BASE, "call.reopen", "unit.manage", "export", "audit"],
    dispatcher: BASE,
    officer: ["view", "call.create", "narrative", "unit.status.own", "entity.edit"],
    records: ["view", "export"],
  };
  const REF = {
    call_types: [
      ["ALARM", "Alarm - Burglary/Panic", 2], ["ANIMAL", "Animal Complaint", 4], ["ASSAULT", "Assault", 1],
      ["ASSIST", "Assist Other Agency", 3], ["BURG", "Burglary", 2], ["CHECK", "Building/Area Check", 5],
      ["DISTURB", "Disturbance / Noise", 3], ["DOMESTIC", "Domestic Disturbance", 1], ["DUI", "DUI / Impaired Driver", 2],
      ["FRAUD", "Fraud / Scam", 4], ["LARCENY", "Larceny / Theft", 3], ["MENTAL", "Mental Health Crisis", 1],
      ["MISSING", "Missing Person", 2], ["MVA", "Motor Vehicle Crash", 2], ["MVAINJ", "Motor Vehicle Crash - Injury", 1],
      ["PARKING", "Parking Complaint", 5], ["SUSP", "Suspicious Person/Vehicle", 3], ["TRAFFIC", "Traffic Complaint / Hazard", 3],
      ["TS", "Traffic Stop", 3], ["WELFARE", "Welfare Check", 2], ["911HANG", "911 Hang-up / Open Line", 2], ["OTHER", "Other", 4],
      ["STRUCT", "Structure Fire", 1, "FIRE"], ["FIREALARM", "Fire Alarm Activation", 2, "FIRE"], ["CO", "CO Alarm / Detector", 2, "FIRE"],
      ["VEHFIRE", "Vehicle Fire", 2, "FIRE"], ["GAS", "Gas Leak / Odor", 2, "FIRE"], ["EXTRIC", "Extrication / Entrapment", 1, "FIRE"],
      ["CARDIAC", "Cardiac / Chest Pain", 1, "EMS"], ["BREATH", "Breathing Problem", 1, "EMS"], ["FALL", "Fall", 2, "EMS"],
      ["UNCON", "Unconscious / Unresponsive", 1, "EMS"], ["SICK", "Sick Person", 3, "EMS"], ["LIFTASSIST", "Lift Assist", 4, "EMS"],
    ].map(([code, description, default_priority, discipline], i) => ({ id: i + 1, code, description, default_priority, active: 1, discipline: discipline || (code === "MVAINJ" ? "EMS" : "POLICE") })),
    dispositions: [
      ["REPORT", "Report Taken"], ["ARREST", "Arrest"], ["CITATION", "Citation Issued"], ["WARNING", "Warning Issued"],
      ["ADVISED", "Advised / Mediated"], ["ASSISTED", "Assisted"], ["GOA", "Gone on Arrival"], ["UTL", "Unable to Locate"],
      ["UNFOUNDED", "Unfounded"], ["CANCELLED", "Cancelled"], ["TRANSPORT", "Transported"], ["EXTINGUISHED", "Fire Extinguished"],
      ["FALSEALARM", "False Alarm"], ["TREATED", "Treated, Not Transported"], ["REFUSAL", "Patient Refused Care / Transport"],
    ].map(([code, description], i) => ({ id: i + 1, code, description, active: 1 })),
    priorities: [1, 2, 3, 4, 5],
    sources: ["911", "PHONE", "RADIO", "WALK_IN", "OFFICER", "ALARM_CO", "OTHER_AGENCY", "OTHER"],
    unit_statuses: ["AVAILABLE", "IN_QUARTERS", "DISPATCHED", "ENROUTE", "ONSCENE", "TRANSPORT", "AT_HOSPITAL", "BUSY", "OUT_OF_SERVICE", "OFF_DUTY"],
    call_statuses: ["DISPATCHED", "ENROUTE", "ONSCENE", "TRANSPORT", "AT_HOSPITAL"],
    unit_types: ["PATROL", "SUPERVISOR", "DETECTIVE", "K9", "TRAFFIC", "ENGINE", "LADDER", "TANKER", "RESCUE", "FIRE_CHIEF", "AMBULANCE_ALS", "AMBULANCE_BLS", "EMS_SUPERVISOR", "OTHER"],
    unit_type_discipline: { PATROL: "POLICE", SUPERVISOR: "POLICE", DETECTIVE: "POLICE", K9: "POLICE", TRAFFIC: "POLICE", ENGINE: "FIRE", LADDER: "FIRE", TANKER: "FIRE", RESCUE: "FIRE", FIRE_CHIEF: "FIRE", AMBULANCE_ALS: "EMS", AMBULANCE_BLS: "EMS", EMS_SUPERVISOR: "EMS", OTHER: "POLICE" },
    disciplines: ["POLICE", "FIRE", "EMS"],
    active911: { enabled: true, disciplines: ["FIRE", "EMS"] },
    person_roles: ["COMPLAINANT", "CALLER", "VICTIM", "SUSPECT", "WITNESS", "ARRESTEE", "DRIVER", "PASSENGER", "OWNER", "SUBJECT", "MISSING", "PATIENT", "OTHER"],
    vehicle_roles: ["INVOLVED", "SUSPECT", "VICTIM", "TOWED", "STOLEN", "RECOVERED", "ABANDONED", "OTHER"],
    roles: ["admin", "supervisor", "dispatcher", "officer", "records"],
  };

  // ------------------------------------------------------------------ state
  const db = { seq: 1, users: [], units: [], calls: [], narrative: [], callUnits: [], persons: [], vehicles: [], usl: [], audit: [], alerts: [], counters: { call: 0, case: 0 }, ids: {} };
  const nextId = (t) => (db.ids[t] = (db.ids[t] || 0) + 1);
  let session = null;

  const mkUser = (username, full_name, role, badge) => db.users.push({
    id: nextId("users"), username, full_name, role, badge, active: 1, totp_enabled: 1, must_change_pw: 0, locked: false,
    last_login_at: ago(60 * 20), created_at: ago(60 * 24 * 10),
  });
  mkUser("dispatch", "Dana Dispatcher", "dispatcher", "D12");
  mkUser("officer", "Ofc. Pat Rowe", "officer", "214");
  mkUser("sergeant", "Sgt. Lee Marsh", "supervisor", "105");
  mkUser("records", "Robin Records", "records", null);
  mkUser("admin", "Alex Admin", "admin", null);

  const mkUnit = (unit_code, unit_type, officer_name, status, officer_user_id = null) => db.units.push({
    id: nextId("units"), unit_code, description: null, unit_type, status, status_at: ago(40), current_call_id: null,
    officer_user_id, officer_name, active: 1, discipline: REF.unit_type_discipline[unit_type] || "POLICE",
  });
  mkUnit("P1", "PATROL", "Ofc. Kim Hale #211", "AVAILABLE");
  mkUnit("P2", "PATROL", "Ofc. Pat Rowe #214", "AVAILABLE", 2);
  mkUnit("P3", "PATROL", "Ofc. Sam Ortiz #220", "AVAILABLE");
  mkUnit("P4", "PATROL", null, "OFF_DUTY");
  mkUnit("S1", "SUPERVISOR", "Sgt. Lee Marsh #105", "AVAILABLE", 3);
  mkUnit("E1", "ENGINE", "Lt. R. Cole + 2", "IN_QUARTERS");
  mkUnit("L1", "LADDER", "Capt. J. Hart + 1", "IN_QUARTERS");
  mkUnit("A1", "AMBULANCE_ALS", "Medic D. Shaw / EMT T. Ray", "AVAILABLE");
  mkUnit("A2", "AMBULANCE_BLS", "EMT K. Lund / EMT M. Ito", "IN_QUARTERS");

  const user = () => session;
  const sysUser = () => session || db.users[0];
  function audit(action, entity, entity_id, detail) {
    const u = session;
    db.audit.push({ id: db.audit.length + 1, at: iso(), user_id: u ? u.id : null, username: u ? u.username : null, ip: "demo",
      action, entity: entity || null, entity_id: entity_id == null ? null : String(entity_id), detail: detail ? JSON.stringify(detail) : null });
  }
  function narr(call_id, text, entry_type = "NOTE", at = iso(), u = sysUser()) {
    db.narrative.push({ id: nextId("narr"), call_id, at, user_id: u.id, username: u.username, entry_type, text });
  }
  const touch = (c) => { c.updated_at = iso(); c.version += 1; };
  const bump = () => { db.seq += 1; };
  const fmtCall = (n) => `${yy}DPD${String(n).padStart(4, "0")}`;
  const fmtCase = (n) => `${yy}DPD-${String(n).padStart(5, "0")}`;

  class E extends Error { constructor(status, msg) { super(msg); this.status = status; } }
  const can = (p) => session && PERMS[session.role].includes(p);
  const need = (p) => { if (!can(p)) { audit("ACCESS_DENIED", "path", p); throw new E(403, "You do not have permission for this action."); } };
  const getCall = (id) => { const c = db.calls.find((x) => x.id === id); if (!c) throw new E(404, "Call not found."); return c; };
  const getUnit = (id) => { const u = db.units.find((x) => x.id === id); if (!u) throw new E(404, "Unit not found."); return u; };
  const clean = (v, upper) => (v === undefined || v === null || String(v).trim() === "" ? null : (upper ? String(v).trim().toUpperCase() : String(v).trim()));

  // ------------------------------------------------------------------ unit status (mirrors server rules)
  const TIME = { DISPATCHED: "dispatched_at", ENROUTE: "enroute_at", ONSCENE: "onscene_at", TRANSPORT: "transport_at", AT_HOSPITAL: "at_hospital_at" };
  const LABEL = { DISPATCHED: "dispatched", ENROUTE: "en route", ONSCENE: "on scene", TRANSPORT: "transporting", AT_HOSPITAL: "at hospital" };
  function page(callId, discipline, kind, note) {
    if (!REF.active911.disciplines.includes(discipline)) return;
    if (kind === "AUTO" && db.alerts.some((a) => a.call_id === callId && a.discipline === discipline && a.kind === "AUTO")) return;
    const a = { id: nextId("alerts"), call_id: callId, discipline, kind, status: "SENT", created_at: iso(), sent_at: iso(), attempts: 1, note: note || null };
    db.alerts.push(a);
    const units = db.callUnits.filter((x) => x.call_id === callId && !x.cleared_at && (getUnit(x.unit_id).discipline === discipline)).map((x) => x.unit_code);
    narr(callId, `Active911 alert sent (${discipline})${units.length ? ": " + units.join(", ") : ""} [simulated in demo]`, "SYSTEM");
  }
  function clearFromCall(unit, reason, at) {
    if (!unit.current_call_id) return;
    const cu = db.callUnits.find((x) => x.call_id === unit.current_call_id && x.unit_id === unit.id && !x.cleared_at);
    if (cu) cu.cleared_at = at;
    narr(unit.current_call_id, `${unit.unit_code} cleared${reason ? ` (${reason})` : ""}`, "SYSTEM", at);
    touch(getCall(unit.current_call_id));
  }
  function setStatus(unit, status, callId, note, at = iso()) {
    if (!REF.unit_statuses.includes(status)) throw new E(400, "Unknown status.");
    let newCall = null;
    if (TIME[status]) {
      const target = callId || unit.current_call_id;
      if (!target) throw new E(400, `${status} requires a call.`);
      const call = getCall(target);
      if (call.status === "CLOSED") throw new E(409, `Call ${call.call_number} is closed.`);
      if (unit.current_call_id && unit.current_call_id !== target) clearFromCall(unit, `reassigned to ${call.call_number}`, at);
      let cu = db.callUnits.find((x) => x.call_id === target && x.unit_id === unit.id && !x.cleared_at);
      if (!cu) {
        const primary = !db.callUnits.some((x) => x.call_id === target && !x.cleared_at);
        cu = { id: nextId("cu"), call_id: target, unit_id: unit.id, unit_code: unit.unit_code, officer_name: unit.officer_name,
          is_primary: primary ? 1 : 0, dispatched_at: at, enroute_at: null, onscene_at: null, transport_at: null, at_hospital_at: null, cleared_at: null };
        db.callUnits.push(cu);
      }
      if (!cu[TIME[status]]) cu[TIME[status]] = at;
      if (call.status === "PENDING") call.status = "ACTIVE";
      narr(target, `${unit.unit_code} ${LABEL[status]}${note ? ": " + note : ""}`, "SYSTEM", at);
      touch(call);
      newCall = target;
      if (status === "DISPATCHED") pendingPages.push([target, unit.discipline]);
    } else if (unit.current_call_id) {
      clearFromCall(unit, note || status.replace(/_/g, " ").toLowerCase(), at);
    }
    db.usl.push({ id: nextId("usl"), unit_id: unit.id, unit_code: unit.unit_code, call_id: newCall || unit.current_call_id, status, at, note: note || null });
    unit.status = status; unit.status_at = at; unit.current_call_id = newCall;
    audit("UNIT_STATUS", "unit", unit.id, { unit: unit.unit_code, to: status });
  }

  // ------------------------------------------------------------------ calls
  const CALL_FIELDS = ["location_address", "location_city", "location_cross", "location_common", "location_notes",
    "caller_name", "caller_phone", "caller_address", "summary", "case_number", "chief_complaint", "destination"];
  let pendingPages = [];
  const flushPages = () => { const p = pendingPages; pendingPages = []; for (const [cid, d] of p) page(cid, d, "AUTO"); };
  function createCall(b, at = iso()) {
    const type = REF.call_types.find((t) => t.code === clean(b.call_type, true));
    if (!type) throw new E(400, "'call_type' is required.");
    const received = b.received_at || at;
    if (new Date(received) > new Date(Date.now() + 600000)) throw new E(400, "'received_at' cannot be in the future.");
    db.counters.call += 1;
    const c = { id: nextId("calls"), call_number: fmtCall(db.counters.call), case_number: null, status: "PENDING",
      received_at: received, call_type: type.code, priority: Number(b.priority) || type.default_priority, source: b.source || "PHONE",
      location_lat: null, location_lon: null, disposition: null, closed_at: null, closed_by: null,
      created_by: sysUser().id, created_at: at, updated_at: at, version: 1 };
    for (const f of CALL_FIELDS) c[f] = clean(b[f]);
    c.discipline = b.discipline || type.discipline;
    c.patient_count = b.patient_count === "" || b.patient_count == null ? null : Number(b.patient_count);
    db.calls.push(c);
    narr(c.id, `Call created: ${c.call_type} P${c.priority}${received !== at ? ` (back-entered, received ${received})` : ""}`, "SYSTEM", received);
    if (clean(b.narrative)) narr(c.id, clean(b.narrative), "NOTE", received);
    audit("CALL_CREATE", "call", c.id, { call_number: c.call_number });
    for (const uid of b.assign_units || []) setStatus(getUnit(Number(uid)), "DISPATCHED", c.id, null, at);
    flushPages();
    return c;
  }
  function closeCall(c, disp, note, at = iso()) {
    if (!REF.dispositions.some((d) => d.code === disp)) throw new E(400, "'disposition' is required.");
    for (const u of db.units.filter((x) => x.current_call_id === c.id)) setStatus(u, "AVAILABLE", null, "cleared when call closed", at);
    Object.assign(c, { status: "CLOSED", disposition: disp, closed_at: at, closed_by: sysUser().id });
    if (note) narr(c.id, note, "NOTE", at);
    narr(c.id, `Call closed. Disposition: ${disp}`, "SYSTEM", at);
    touch(c);
  }

  // ------------------------------------------------------------------ seed realistic sample activity
  (function seed() {
    const old = (min) => new Date(Date.now() - min * 60000);
    const fake = (min, fn) => fn(iso(old(min)));
    const unit = (code) => db.units.find((u) => u.unit_code === code);
    // closed earlier
    fake(300, (t) => { const c = createCall({ call_type: "PARKING", location_address: "40 Elm St", location_city: "Demo Town", caller_name: "Jordan Pike", caller_phone: "555-0142", narrative: "Vehicle blocking driveway since this morning." }, t);
      setStatus(unit("P3"), "DISPATCHED", c.id, null, t); setStatus(unit("P3"), "ONSCENE", c.id, null, iso(old(288)));
      narr(c.id, "Owner located, vehicle moved.", "NOTE", iso(old(280))); closeCall(c, "WARNING", null, iso(old(279))); });
    fake(190, (t) => { const c = createCall({ call_type: "ALARM", location_address: "12 Mill Rd", location_common: "Riverside Hardware", location_city: "Demo Town", source: "ALARM_CO", narrative: "Rear door zone. Keyholder en route." }, t);
      setStatus(unit("P1"), "DISPATCHED", c.id, null, t); setStatus(unit("P1"), "ONSCENE", c.id, null, iso(old(183)));
      narr(c.id, "Building secure, wind-blown door. Keyholder reset alarm.", "NOTE", iso(old(170))); closeCall(c, "UNFOUNDED", null, iso(old(168))); });
    fake(95, (t) => { const c = createCall({ call_type: "DISTURB", location_address: "40 Elm St", location_city: "Demo Town", caller_name: "Jordan Pike", caller_phone: "555-0142", narrative: "Loud music from upstairs unit." }, t);
      setStatus(unit("P2"), "DISPATCHED", c.id, null, t); setStatus(unit("P2"), "ONSCENE", c.id, null, iso(old(88)));
      closeCall(c, "ADVISED", "Tenant advised, music turned down.", iso(old(80))); });
    // open now
    fake(14, (t) => { const c = createCall({ call_type: "MVA", priority: 2, location_address: "Main St & State St", location_city: "Demo Town", source: "911", caller_name: "Casey Moore", caller_phone: "555-0187", narrative: "Two vehicles, no injuries reported, partially blocking the eastbound lane." }, t);
      setStatus(unit("P1"), "DISPATCHED", c.id, null, t); setStatus(unit("P1"), "ENROUTE", c.id, null, iso(old(13))); setStatus(unit("P1"), "ONSCENE", c.id, null, iso(old(8)));
      narr(c.id, "P1: both drivers out and uninjured. Requesting tow for one vehicle.", "NOTE", iso(old(6)));
      db.vehicles.push({ id: nextId("veh"), call_id: c.id, role: "INVOLVED", plate: "DMO123", plate_state: "VT", year: "2016", make: "Subaru", model: "Outback", style: "SW", color: "Gray", vin: null, notes: "Front-end damage", created_at: t, updated_at: t, deleted: 0 });
      db.persons.push({ id: nextId("per"), call_id: c.id, role: "DRIVER", last_name: "Moore", first_name: "Casey", middle_name: null, suffix: null, dob: "1990-05-02", sex: "F", race: null, address: "8 Pine Ln", city: "Demo Town", state: "VT", zip: null, phone: "555-0187", dl_number: null, dl_state: null, height: null, weight: null, notes: null, created_at: t, updated_at: t, deleted: 0 });
      c.case_number = fmtCase(++db.counters.case); narr(c.id, `Case number assigned: ${c.case_number}`, "SYSTEM", iso(old(5))); });
    fake(6, (t) => { const c = createCall({ call_type: "WELFARE", location_address: "221 Hill St", location_city: "Demo Town", caller_name: "Morgan Price", caller_phone: "555-0110", narrative: "Daughter has not heard from 81-year-old mother since yesterday. Lives alone." }, t);
      setStatus(unit("S1"), "DISPATCHED", c.id, null, iso(old(4))); setStatus(unit("S1"), "ENROUTE", c.id, null, iso(old(3))); });
    fake(9, (t) => { const c = createCall({ call_type: "CARDIAC", location_address: "18 Spring St", location_city: "Demo Town", source: "911", caller_name: "Lee Park", caller_phone: "555-0133", patient_count: 1, chief_complaint: "68 y/o male, chest pain, conscious and breathing", narrative: "Wife reports chest pain for 20 minutes, history of heart problems." }, t);
      setStatus(unit("A1"), "DISPATCHED", c.id, null, t); flushPages(); setStatus(unit("A1"), "ENROUTE", c.id, null, iso(old(8))); setStatus(unit("A1"), "ONSCENE", c.id, null, iso(old(3))); });
    fake(4, (t) => { const c = createCall({ call_type: "FIREALARM", location_address: "100 Main St", location_common: "Demo Town Elementary School", location_city: "Demo Town", source: "ALARM_CO", narrative: "General fire alarm, zone 3 east wing. Alarm company calling keyholder." }, t);
      setStatus(unit("E1"), "DISPATCHED", c.id, null, t); setStatus(unit("L1"), "DISPATCHED", c.id, null, t); flushPages(); setStatus(unit("E1"), "ENROUTE", c.id, null, iso(old(3))); });
    fake(2, (t) => { createCall({ call_type: "SUSP", location_address: "Lot behind 5 Depot Sq", location_city: "Demo Town", caller_name: "Anonymous", narrative: "Person checking car door handles in the lot. Male, dark jacket, backpack." }, t); });
    db.audit.length = 0; // start the demo's audit log clean
    pendingPages = [];
  })();

  // ------------------------------------------------------------------ routing
  const routes = [];
  const R = (m, re, fn, open = false) => routes.push([m, new RegExp("^" + re + "$"), fn, open]);

  R("POST", "/api/login", (b) => {
    const u = db.users.find((x) => x.username.toLowerCase() === String(b.username || "").toLowerCase() && x.active);
    if (!u || b.password !== PASSWORD) { audit("LOGIN_FAIL", "user", u ? u.id : null, { username: b.username }); throw new E(401, "Invalid username or password. (Demo password is: demo)"); }
    if (u.totp_enabled && !b.totp) return { mfa_required: true };
    if (u.totp_enabled && !/^\d{6}$/.test(b.totp)) throw new E(401, "Invalid authentication code. (Demo: any 6 digits work)");
    session = u; audit("LOGIN", "user", u.id); return { ok: true };
  }, true);
  R("POST", "/api/logout", () => { audit("LOGOUT", "user", session.id); session = null; return { ok: true }; });
  R("GET", "/api/me", () => ({
    user: { ...session }, csrf: "demo", permissions: PERMS[session.role], must_change_pw: false, mfa_setup_required: false,
    agency: { name: "Demo Police Department", ori: "" }, idle_minutes: 30, password_min_length: 12, version: "demo",
    previous_logon: { at: session.last_login_at, ip: "10.0.0.21", failed_since: 0 },
  }));
  R("POST", "/api/me/password", () => { throw new E(400, "Password changes are turned off in the demo."); });
  R("POST", "/api/me/mfa/begin", () => ({ secret: "DEMODEMODEMODEMODEMODEMODEMODEMO", uri: "otpauth://totp/demo" }));
  R("POST", "/api/me/mfa/confirm", () => { session.totp_enabled = 1; return { ok: true }; });
  R("GET", "/api/reference", () => REF);
  R("GET", "/api/board", (b, q) => {
    need("view");
    if (q.since && Number(q.since) === db.seq) return { seq: db.seq, changed: false };
    const cutoff = Date.now() - 12 * 3600000;
    const order = { PENDING: 0, ACTIVE: 1, CLOSED: 2 };
    const calls = db.calls.filter((c) => c.status !== "CLOSED" || new Date(c.closed_at) >= cutoff)
      .map((c) => ({ ...c, units: db.callUnits.filter((x) => x.call_id === c.id && !x.cleared_at).map((x) => x.unit_code).join(" ") || null }))
      .sort((a, b2) => order[a.status] - order[b2.status] || (a.status === "CLOSED" ? 0 : a.priority - b2.priority) || (a.received_at < b2.received_at ? 1 : -1));
    const units = db.units.filter((u) => u.active).map((u) => {
      const c = u.current_call_id ? getCall(u.current_call_id) : null;
      return { ...u, current_call_number: c && c.call_number, current_call_type: c && c.call_type, current_call_address: c && c.location_address };
    }).sort((a, b2) => (a.status === "OFF_DUTY") - (b2.status === "OFF_DUTY") || a.unit_code.localeCompare(b2.unit_code));
    return { seq: db.seq, changed: true, calls, units, closed_window_hours: 12 };
  });
  R("GET", "/api/calls/search", (b, q) => {
    need("view");
    const has = (v, s) => v && String(v).toLowerCase().includes(s.toLowerCase());
    let rows = db.calls.filter((c) => {
      if (q.q && !(has(c.call_number, q.q) || has(c.case_number, q.q) || has(c.location_address, q.q) || has(c.caller_name, q.q) ||
        db.narrative.some((n) => n.call_id === c.id && has(n.text, q.q)))) return false;
      if (q.address && !(has(c.location_address, q.address) || has(c.location_common, q.address))) return false;
      if (q.name && !(has(c.caller_name, q.name) || db.persons.some((p) => p.call_id === c.id && !p.deleted && (has(p.last_name, q.name) || has(p.first_name, q.name))))) return false;
      if (q.plate && !db.vehicles.some((v) => v.call_id === c.id && !v.deleted && has((v.plate || "").replace(/[\s-]/g, ""), q.plate.replace(/[\s-]/g, "")))) return false;
      if (q.unit && !db.callUnits.some((x) => x.call_id === c.id && x.unit_code === q.unit.toUpperCase())) return false;
      if (q.call_type && c.call_type !== q.call_type) return false;
      if (q.status && c.status !== q.status) return false;
      if (q.from && c.received_at < new Date(q.from).toISOString()) return false;
      if (q.to && c.received_at > new Date(q.to).toISOString()) return false;
      return true;
    });
    rows = rows.sort((a, b2) => (a.received_at < b2.received_at ? 1 : -1)).map((c) => ({ ...c,
      units: [...new Set(db.callUnits.filter((x) => x.call_id === c.id).map((x) => x.unit_code))].join(",") || null }));
    audit("SEARCH", "call", null, { ...q, results: rows.length });
    return { results: rows, limit: 200 };
  });
  R("POST", "/api/calls", (b) => { need("call.create"); const c = createCall(b); bump(); return { id: c.id, call_number: c.call_number }; });
  R("GET", "/api/calls/(\\d+)", (b, q, id) => {
    need("view");
    const c = getCall(id);
    audit("CALL_VIEW", "call", id);
    return {
      call: { ...c },
      narrative: db.narrative.filter((n) => n.call_id === id).sort((a, b2) => (a.at < b2.at ? -1 : a.at > b2.at ? 1 : a.id - b2.id)),
      units: db.callUnits.filter((x) => x.call_id === id),
      persons: db.persons.filter((p) => p.call_id === id && !p.deleted),
      vehicles: db.vehicles.filter((v) => v.call_id === id && !v.deleted),
      alerts: db.alerts.filter((a) => a.call_id === id),
      prior_at_address: c.location_address ? db.calls.filter((x) => x.id !== id && (x.location_address || "").toLowerCase() === c.location_address.toLowerCase())
        .map(({ id: pid, call_number, received_at, call_type, disposition }) => ({ id: pid, call_number, received_at, call_type, disposition })) : [],
    };
  });
  R("PUT", "/api/calls/(\\d+)", (b, q, id) => {
    need("call.edit");
    const c = getCall(id);
    if (c.status === "CLOSED" && !can("call.reopen")) throw new E(409, "Call is closed. A supervisor must make changes.");
    if (b.version != null && Number(b.version) !== c.version) throw new E(409, "This call was changed by someone else. Reload and try again.");
    const changes = [];
    if ("patient_count" in b) c.patient_count = b.patient_count === "" || b.patient_count == null ? null : Number(b.patient_count);
    if (b.discipline && b.discipline !== c.discipline) { changes.push(`discipline: ${c.discipline} → ${b.discipline}`); c.discipline = b.discipline; }
    for (const f of [...CALL_FIELDS, "call_type", "priority", "source"]) {
      if (!(f in b)) continue;
      const v = f === "priority" ? Number(b[f]) : clean(b[f], f === "call_type");
      if (v !== c[f] && !(v === null && !c[f])) { if (["call_type", "priority", "location_address"].includes(f)) changes.push(`${f.replace("location_", "")}: ${c[f] || "—"} → ${v || "—"}`); c[f] = v; }
    }
    if (b.received_at && b.received_at !== c.received_at) { changes.push(`received_at: ${c.received_at} → ${b.received_at}`); c.received_at = b.received_at; }
    if (changes.length) narr(id, "Changed " + changes.join("; "), "SYSTEM");
    touch(c); bump(); audit("CALL_UPDATE", "call", id);
    return { ok: true, version: c.version };
  });
  R("POST", "/api/calls/(\\d+)/narrative", (b, q, id) => {
    need("narrative"); const c = getCall(id); const t = clean(b.text);
    if (!t) throw new E(400, "'text' is required.");
    narr(id, t); touch(c); bump(); audit("NARRATIVE_ADD", "call", id); return { ok: true };
  });
  R("POST", "/api/calls/(\\d+)/case-number", (b, q, id) => {
    need("narrative"); const c = getCall(id);
    if (c.case_number) throw new E(409, `Call already has case number ${c.case_number}.`);
    c.case_number = fmtCase(++db.counters.case); narr(id, `Case number assigned: ${c.case_number}`, "SYSTEM"); touch(c); bump();
    audit("CASE_NUMBER", "call", id); return { case_number: c.case_number };
  });
  R("POST", "/api/calls/(\\d+)/close", (b, q, id) => {
    need("call.close"); const c = getCall(id);
    if (c.status === "CLOSED") throw new E(409, "Call is already closed.");
    closeCall(c, clean(b.disposition, true), clean(b.note)); bump(); audit("CALL_CLOSE", "call", id); return { ok: true };
  });
  R("POST", "/api/calls/(\\d+)/reopen", (b, q, id) => {
    need("call.reopen"); const c = getCall(id);
    if (!clean(b.reason)) throw new E(400, "'reason' is required.");
    narr(id, `Call reopened (was ${c.disposition}): ${b.reason}`, "SYSTEM");
    Object.assign(c, { status: "ACTIVE", closed_at: null, closed_by: null }); touch(c); bump(); audit("CALL_REOPEN", "call", id); return { ok: true };
  });
  const ENT = { persons: ["persons", "Person"], vehicles: ["vehicles", "Vehicle"] };
  for (const kind of ["persons", "vehicles"]) {
    R("POST", `/api/calls/(\\d+)/${kind}`, (b, q, id) => {
      need("entity.edit"); const c = getCall(id);
      if (!clean(b.role)) throw new E(400, "'role' is required.");
      if (b.dob && !/^\d{4}-\d{2}-\d{2}$/.test(b.dob)) throw new E(400, "Date of birth must be YYYY-MM-DD.");
      const rec = { id: nextId(kind), call_id: id, created_at: iso(), updated_at: iso(), deleted: 0 };
      for (const [k, v] of Object.entries(b)) rec[k] = clean(v, ["plate", "plate_state", "state", "dl_state", "vin", "role"].includes(k));
      db[kind].push(rec);
      const desc = kind === "persons" ? [rec.last_name, rec.first_name].filter(Boolean).join(", ") : [rec.plate || "no plate", rec.plate_state, rec.color, rec.make, rec.model].filter(Boolean).join(" ");
      narr(id, `${ENT[kind][1]} added (${rec.role}): ${desc}`, "SYSTEM"); touch(c); bump(); audit(`${ENT[kind][1].toUpperCase()}_ADD`, kind, rec.id);
      return { id: rec.id };
    });
    const upd = (b, q, id, method) => {
      need("entity.edit");
      const rec = db[kind].find((x) => x.id === id && !x.deleted); if (!rec) throw new E(404, "Not found.");
      if (method === "DELETE") { rec.deleted = 1; narr(rec.call_id, `${ENT[kind][1]} record #${id} removed`, "SYSTEM"); }
      else for (const [k, v] of Object.entries(b)) rec[k] = clean(v, ["plate", "plate_state", "state", "dl_state", "vin", "role"].includes(k));
      touch(getCall(rec.call_id)); bump(); audit(`${ENT[kind][1].toUpperCase()}_${method === "DELETE" ? "DELETE" : "UPDATE"}`, kind, id); return { ok: true };
    };
    R("PUT", `/api/${kind}/(\\d+)`, (b, q, id) => upd(b, q, id, "PUT"));
    R("DELETE", `/api/${kind}/(\\d+)`, (b, q, id) => upd(b, q, id, "DELETE"));
  }
  R("PUT", "/api/call-units/(\\d+)", (b, q, id) => {
    need("time.edit"); const cu = db.callUnits.find((x) => x.id === id); if (!cu) throw new E(404, "Not found.");
    const ch = [];
    for (const f of ["dispatched_at", "enroute_at", "onscene_at", "transport_at", "cleared_at"]) {
      if (!(f in b)) continue;
      if (f === "cleared_at" && !b[f] && cu.cleared_at) throw new E(400, "Cannot un-clear a unit; dispatch it again instead.");
      if ((b[f] || null) !== cu[f]) { ch.push(`${f.replace("_at", "")} ${cu[f] || "—"} → ${b[f] || "—"}`); cu[f] = b[f] || null; }
    }
    if (ch.length) narr(cu.call_id, `${cu.unit_code} times corrected: ${ch.join("; ")}`, "SYSTEM");
    touch(getCall(cu.call_id)); bump(); return { ok: true };
  });
  R("POST", "/api/units/(\\d+)/status", (b, q, id) => {
    const u = getUnit(id);
    if (!can("unit.status.any")) {
      if (!(can("unit.status.own") && u.officer_user_id === session.id)) throw new E(403, "You can only change the status of the unit you are signed on to.");
      if (b.call_id && b.status === "DISPATCHED") throw new E(403, "Officers cannot dispatch; use ENROUTE or ONSCENE to attach to a call.");
    }
    setStatus(u, String(b.status || "").toUpperCase(), b.call_id ? Number(b.call_id) : null, clean(b.note)); flushPages(); bump(); return { ok: true };
  });
  R("POST", "/api/units/(\\d+)/signon", (b, q, id) => {
    const u = getUnit(id);
    if (can("unit.crew") && "officer_name" in b) { u.officer_name = clean(b.officer_name); u.officer_user_id = null; }
    else {
      for (const p of db.units) if (p.officer_user_id === session.id && p.id !== id) { p.officer_user_id = null; p.officer_name = null; }
      u.officer_user_id = session.id; u.officer_name = session.full_name + (session.badge ? ` #${session.badge}` : "");
    }
    if (u.officer_name && u.status === "OFF_DUTY") setStatus(u, "AVAILABLE", null, "on duty");
    bump(); return { ok: true };
  });
  R("POST", "/api/units/(\\d+)/signoff", (b, q, id) => {
    const u = getUnit(id);
    if (!can("unit.crew") && u.officer_user_id !== session.id) throw new E(403, "Not permitted.");
    if (u.status !== "OFF_DUTY") setStatus(u, "OFF_DUTY", null, "off duty");
    u.officer_name = null; u.officer_user_id = null; bump(); return { ok: true };
  });
  R("GET", "/api/units/(\\d+)/history", (b, q, id) => ({
    history: db.usl.filter((x) => x.unit_id === id).slice().reverse().map((x) => ({ ...x, call_number: x.call_id ? getCall(x.call_id).call_number : null })),
  }));
  R("GET", "/api/admin/users", () => { need("admin"); return { users: db.users.map((u) => ({ ...u })) }; });
  R("POST", "/api/admin/users", (b) => {
    need("admin");
    if (!clean(b.username) || !clean(b.full_name)) throw new E(400, "Username and full name are required.");
    if (db.users.some((u) => u.username.toLowerCase() === b.username.toLowerCase())) throw new E(409, "Username already exists.");
    db.users.push({ id: nextId("users"), username: b.username, full_name: b.full_name, role: b.role, badge: clean(b.badge), active: 1,
      totp_enabled: 0, must_change_pw: 1, locked: false, last_login_at: null, created_at: iso() });
    return { ok: true };
  });
  R("PUT", "/api/admin/users/(\\d+)", (b, q, id) => {
    need("admin"); const u = db.users.find((x) => x.id === id);
    if (b.action === "update") {
      if (id === session.id && (b.active === false || (b.role && b.role !== "admin"))) throw new E(400, "You cannot deactivate or demote your own account.");
      if ("role" in b) u.role = b.role; if ("active" in b) u.active = b.active ? 1 : 0;
    } else if (b.action === "reset_mfa") u.totp_enabled = 0;
    else if (b.action === "reset_password") u.must_change_pw = 1;
    else if (b.action === "unlock") u.locked = false;
    audit("USER_" + String(b.action).toUpperCase(), "user", id); return { ok: true };
  });
  R("GET", "/api/admin/units", () => { need("unit.manage"); return { units: db.units.map((u) => ({ ...u })) }; });
  R("POST", "/api/admin/units", (b) => {
    need("unit.manage"); const code = clean(b.unit_code, true);
    if (!code) throw new E(400, "'unit_code' is required.");
    if (db.units.some((u) => u.unit_code === code)) throw new E(409, "Unit code already exists.");
    mkUnit(code, b.unit_type || "PATROL", null, "OFF_DUTY"); db.units[db.units.length - 1].description = clean(b.description); bump(); return { ok: true };
  });
  R("PUT", "/api/admin/units/(\\d+)", (b, q, id) => {
    need("unit.manage"); const u = getUnit(id);
    if (b.active === false && u.current_call_id) throw new E(409, "Clear the unit from its call before deactivating it.");
    if ("active" in b) u.active = b.active ? 1 : 0; bump(); return { ok: true };
  });
  for (const [path, key] of [["call-types", "call_types"], ["dispositions", "dispositions"]]) {
    R("GET", `/api/admin/${path}`, () => { need("admin"); return { items: REF[key] }; });
    R("POST", `/api/admin/${path}`, (b) => {
      need("admin"); const code = clean(b.code, true);
      if (!code || !clean(b.description)) throw new E(400, "Code and description are required.");
      const ex = REF[key].find((x) => x.code === code);
      const rec = { code, description: b.description, active: b.active === false ? 0 : 1, ...(key === "call_types" ? { default_priority: Number(b.default_priority) || 3 } : {}) };
      if (ex) Object.assign(ex, rec); else REF[key].push({ id: REF[key].length + 1, ...rec });
      return { ok: true };
    });
  }
  R("GET", "/api/audit", (b, q) => {
    need("audit");
    let rows = db.audit.slice().reverse();
    for (const k of ["action", "entity", "entity_id"]) if (q[k]) rows = rows.filter((r) => r[k] === q[k]);
    if (q.username) rows = rows.filter((r) => (r.username || "").toLowerCase() === q.username.toLowerCase());
    return { entries: rows.slice(0, Number(q.limit) || 200), chain_ok: true, chain_rows: db.audit.length, chain_first_bad_id: null };
  });
  R("POST", "/api/calls/(\\d+)/alert", (b, q, id) => {
    need("alert.send"); getCall(id);
    if (!REF.active911.disciplines.includes(b.discipline)) throw new E(400, "'discipline' must be one of: FIRE, EMS.");
    page(id, b.discipline, "MANUAL", clean(b.note)); touch(getCall(id)); bump(); return { ok: true };
  });
  R("GET", "/api/alerts/status", () => ({ enabled: true, disciplines: REF.active911.disciplines, sent: db.alerts.length, pending: 0, failed: 0,
    last_sent: db.alerts.length ? db.alerts[db.alerts.length - 1].sent_at : null, last_error: null }));
  R("GET", "/api/archive/status", () => ({ enabled: true, dir: "I:\\CAD\\Calls (simulated in demo)", last_ok: iso(), last_error: null,
    written_total: db.calls.length * 2, calls_total: db.calls.length, calls_not_yet_archived: 0 }));
  R("POST", "/api/archive/run", () => ({ ok: true }));

  // ------------------------------------------------------------------ fetch shim
  const json = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
  const realFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = async function (input, init = {}) {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (!url.pathname.startsWith("/api/")) return realFetch(input, init);
    await new Promise((r) => setTimeout(r, 40)); // feel like a network round trip
    const method = (init.method || "GET").toUpperCase();
    const body = init.body ? JSON.parse(init.body) : {};
    const q = Object.fromEntries(url.searchParams.entries());
    for (const [m, re, fn, open] of routes) {
      if (m !== method) continue;
      const match = url.pathname.match(re);
      if (!match) continue;
      if (!open && !session) return json(401, { error: "Not signed in", code: "AUTH" });
      try {
        return json(200, fn(body, q, ...match.slice(1).map(Number), method) ?? { ok: true });
      } catch (e) {
        if (e instanceof E) return json(e.status, { error: e.message });
        console.error(e);
        return json(500, { error: "Demo error: " + e.message });
      }
    }
    return json(404, { error: "Not found" });
  };
})();
