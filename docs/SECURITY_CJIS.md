# Security and CJIS considerations

This system will store criminal justice information (CJI): names, DOBs, addresses, OLNs, plates, narratives. An outage doesn't suspend the agency's obligations under the **FBI CJIS Security Policy** or state rules. The software provides the controls below. Your agency supplies the rest: the environment, policies and people.

**Before go-live, brief your agency TAC (Terminal Agency Coordinator) and the state CJIS Systems Officer (CSO) at the Department of Public Safety.** Get their sign-off on the interim arrangement. Verify the specific requirements against the current CJIS Security Policy version: the table below is a working checklist, not legal or compliance advice.

## Controls the software provides

| Control | Implementation |
|---|---|
| Unique user IDs, no shared accounts | Individual accounts; usernames are unique |
| Multi-factor authentication | TOTP (RFC 6238) per user. `require_mfa = true` enforces enrollment before any access. Replay of a used code is rejected. |
| Password policy | Minimum length (default 12), complexity unless the password is a long passphrase, can't contain the username. Temporary passwords must be changed at first sign-in. Passwords are hashed with PBKDF2-SHA256, 600,000 iterations. |
| Account lockout | 5 failed attempts gives a 15-minute lockout (configurable). Admins can unlock. |
| Session lock | Sessions end after 30 minutes without user activity. The auto-refreshing board does not count as activity. There is also a 12-hour absolute limit. |
| System use notification | A login banner states the system is restricted and monitored. Have your agency review the wording in `static/app.js` (`renderLogin`). |
| Least privilege | Five roles (admin, supervisor, dispatcher, officer, records), enforced on the server for every request |
| Audit logging | Successful and failed sign-ins, sign-outs, session expiry, call views, searches (with the criteria), every create, update or close, unit status changes, exports, user administration. Each entry records the time, user and source IP. |
| Audit integrity | Each audit row is SHA-256 hash-chained to the previous one. Database triggers block UPDATE and DELETE on the audit log. Run `manage.py verify-audit`, or see the Audit tab, to check the chain. |
| Record integrity | Narratives are append-only. Calls can't be deleted. Person and vehicle removals are soft deletes. |
| Encryption in transit | TLS 1.2 minimum when `tls_cert` and `tls_key` are set. HSTS. Cookies are `Secure`, `HttpOnly`, `SameSite=Strict`. |
| Web protections | CSRF tokens plus an Origin check, a strict Content-Security-Policy (no inline script, no external resources), `X-Frame-Options: DENY`, `no-store` caching, parameterized SQL, and all output escaped (no `innerHTML`) |
| No external dependencies | Python standard library only, with no CDN or telemetry. Nothing leaves your network. |

## What the agency must provide

1. **Physically secure location** for the server: a locked room with restricted access.
2. **Encryption at rest**: BitLocker or LUKS on the server, and on any drive holding backups or exports. CJIS expects FIPS 140-validated encryption. Use Windows BitLocker in FIPS mode or a validated module. The application's TLS uses the operating system's or Python's OpenSSL; ask IT whether that build is FIPS-validated, or put a validated TLS reverse proxy in front.
3. **Network segmentation**: reachable only from PD workstations and MDTs (over the existing CJIS-compliant VPN). Never expose it to the internet.
4. **Personnel**: everyone with access, including IT staff who administer the server, must meet the CJIS personnel-security requirements (fingerprint-based background check, security awareness training).
5. **Patching and antivirus** on the server OS.
6. **Audit review**: a supervisor reviews the Audit tab weekly, especially LOGIN_FAIL, EXPORT, CALL_REOPEN and USER_* events. The CJIS policy requires audit records to be kept for at least one year. Keep the database, or exports of it, for at least that long, or as long as your retention schedule requires if that is longer.
7. **Account management**: disable accounts the same day someone leaves. Review the user list monthly.
8. **Archive folder (I: drive)**: if `[archive] dir` is set, every call is written as plain files on that share. Restrict the share's permissions to PD personnel only, make sure the file server is encrypted and inside the CJIS boundary, and include it in your audit and backup plans.
9. **Export handling**: export ZIPs contain CJI. Store them only on encrypted, agency-controlled media and delete them when no longer needed.
10. **Incident response**: if you suspect a compromise, take the server off the network, preserve the database and `server.log`, and notify your CSO. That is especially relevant given that the statewide system is down because of a breach.

## Things to deliberately not do

- Don't host this on a personal laptop, a home network, or a consumer cloud account.
- Don't paste NCIC or VCIC query responses into narratives unless your CSO approves storing them here. Record that a query was run and its outcome, as your agency's policy dictates.
- Don't share accounts or passwords between shifts.
