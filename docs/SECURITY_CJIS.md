# CJIS security: what the software does, what the agency must do, and known limits

**Software alone can't be "CJIS compliant."** Compliance with the FBI CJIS Security Policy is assessed for the whole environment: facility, network, devices, people, policies and the application. The state CJIS Systems Officer (CSO) at the Department of Public Safety, and audits, decide whether an arrangement complies.

This document is a working checklist for your TAC and CSO. Before go-live, verify each item against the **current** CJIS Security Policy version. It is not legal or compliance advice.

Legend: **App** = built into the software · **Agency** = your agency or IT must do it · **Limit** = known limitation and its mitigation

## Identification and authentication
| Requirement | Who | How |
|---|---|---|
| Unique individual accounts | App | Every action is tied to a named user. Shared accounts are prohibited by policy (Agency). |
| Multi-factor authentication | App | TOTP authenticator (RFC 6238), **on by default** (`require_mfa = true`). Enrollment is forced before any data is shown, and code replay is rejected. The server logs a warning if MFA is ever turned off. |
| Password strength | App | Minimum length (configurable, default 12). Complexity is required unless it's a 20+ character passphrase. The username can't be used. Common and predictable passwords are rejected (season or year, "Police", the town name, "Password1!"...). Set the minimum length your CSO specifies. |
| Password storage | App | PBKDF2-SHA256, 600,000 iterations, random salt. Passwords are never stored or logged in plain text. |
| Temporary passwords | App | Must be changed at first sign-in. Resetting a password ends that user's sessions. |
| Lockout | App | 5 failures gives a 15-minute lockout (configurable). Logged. |
| Inactive accounts | App | Disabled automatically after 90 days unused (configurable). Logged. |
| Account management | Agency | Disable accounts the same day someone leaves. Review the user list monthly. |

## Access control
| Requirement | Who | How |
|---|---|---|
| Least privilege | App | 5 roles, enforced on the server for every request |
| Denied access logged | App | Every refused action is written to the audit log as ACCESS_DENIED |
| System use notification | App | A login banner, and a previous-logon notice (last sign-in time and IP, plus failed attempts since then) after sign-in. Have the agency review the banner wording. |
| Session lock / timeout | App + Agency | The CAD ends a session after 30 minutes idle (the auto-refreshing board doesn't count as activity), with a 12-hour maximum. **Agency:** Windows screen lock of 30 minutes or less on every device, by Group Policy. |
| Network access restriction | App + Agency | `allowed_networks` rejects any other source address, and the installer's firewall rule restricts by subnet. **Agency:** never expose it to the internet; remote access only over the CJIS-compliant VPN. |

## Audit and accountability
| Requirement | Who | How |
|---|---|---|
| Events logged | App | Sign-in success and failure, sign-out, session expiry, auto-disable, access denied, call views, searches (with criteria), every create, change and close, unit status, record removals, user administration, counter changes, exports |
| Content | App | Time (UTC), user, source IP, action, object, details (old and new values for changes) |
| Integrity | App | SHA-256 hash chain, with database triggers blocking edits and deletes. Use **verify-audit** or the Audit tab. |
| Off-system copy | App + Agency | `[audit] mirror_dir` continuously appends every audit entry to another machine. **Agency:** make that share writable only by the CAD server and readable by IT security / TAC, not by CAD admins. |
| Review | Agency | A supervisor or TAC reviews the Audit tab weekly (LOGIN_FAIL, ACCESS_DENIED, EXPORT, CALL_REOPEN, USER_*). |
| Retention | Agency | Keep audit data at least 1 year (CJIS minimum), longer if your retention schedule requires. The database never deletes audit entries. |

## Data protection
| Requirement | Who | How |
|---|---|---|
| Encryption in transit | App | HTTPS is **required**: the server won't start without a certificate. TLS 1.2+ only, HSTS, and Secure / HttpOnly / SameSite=Strict cookies. |
| FIPS 140-validated crypto in transit | **Limit** | Python's TLS uses its bundled OpenSSL, which typically isn't a FIPS-validated module. **Mitigations; ask your CSO which applies:** (a) CJIS requires encryption when CJI leaves a physically secure location. Dispatch PCs and the server inside the PD's secured facility and network may fall within one physically secure location, and MDT traffic already rides the agency's FIPS-validated VPN. (b) If the CSO requires FIPS at this layer, put IIS on the server with Windows FIPS mode as the TLS front end (in `config.ini`: `host = 127.0.0.1`, `trust_proxy = true`, `allow_insecure_http = true`. Plain HTTP is then confined to the server's own loopback interface). |
| Encryption at rest | Agency | BitLocker (FIPS-validated) on the server, backup media, and any share holding the archive or exports. The application doesn't add its own encryption layer. |
| Record integrity | App | Narratives are append-only. Calls can't be deleted. Person and vehicle removals are soft deletes and kept. |
| Web application protections | App | CSRF tokens plus an Origin check, strict Content-Security-Policy, no third-party code or CDNs, parameterized SQL, all output escaped, request size limits, connection timeouts |
| Exports and printouts | Agency | Store export ZIPs only on encrypted, agency-controlled media, and shred printouts per policy. Every export is logged. |
| Archive folder (I: drive) | Agency | A PD-only share on an encrypted file server inside the CJIS boundary |
| NCIC/VCIC responses | Agency | Don't paste query responses into narratives unless the CSO approves storing them here. |

## Physical, personnel and system
| Requirement | Who |
|---|---|
| Server in a physically secure, access-controlled location | Agency |
| Fingerprint background checks and security awareness training for everyone with access, **including IT staff who administer the server** | Agency |
| OS patching, antivirus, no unnecessary software on the server | Agency |
| Incident response: if a compromise is suspected, isolate the server, preserve `data\`, and notify the CSO | Agency |

## Known limitations (be upfront with your CSO)
1. **New, small codebase.** It has not had an independent security assessment or penetration test. Recommend one if it is used beyond the outage, and certainly before any multi-agency use.
2. **Python's built-in web server** is simpler than an enterprise web server. It is mitigated by restriction to allowed networks only, a firewall, TLS, connection timeouts and request size limits. For larger deployments, front it with IIS or another hardened reverse proxy.
3. **Server administrators** can access the database file directly. This is mitigated by the hash chain plus the off-system audit mirror, and by restricting admin rights to background-checked IT staff.
4. **Concurrent sessions** per user aren't limited.
5. **No vendor support contract or CJIS Security Addendum.** Whoever maintains it for the agency becomes the "vendor" for CJIS purposes and must meet the personnel requirements if they can access CJI.
