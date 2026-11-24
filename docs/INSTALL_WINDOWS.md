# Secure installation: server and dispatcher computers (Windows)

**The key idea:** nothing gets installed on the dispatchers' computers. The CAD runs on **one** locked-down server. Dispatchers, supervisors and officers use it through a web browser over an encrypted connection. Their computers hold no CJI database, and there's no software to install, patch or lose on each PC.

```
 Dispatch PC 1 ─┐
 Dispatch PC 2 ─┼── HTTPS (TLS 1.2+) inside the PD network ──►  CAD server (locked room)
 Desk PCs      ─┤                                               ├─ database (BitLocker-encrypted disk)
 MDTs ── agency VPN ─┘                                          ├─ hourly backups
                                                                └─► I:\ archive + audit mirror (PD-only shares)
```

Do this together with whoever handles the town or PD IT. Allow about 2–3 hours.

---

## Part A: the server (one machine)

### A1. Choose and prepare the machine
- [ ] A PC or VM in a **locked, access-controlled room** (dispatch center or IT closet). It must not be a dispatcher's own workstation.
- [ ] Windows 10/11 Pro or Windows Server, fully patched, with Defender (or the town's antivirus) on.
- [ ] **BitLocker turned on** for the system drive. If your CSO requires FIPS mode, also enable the "System cryptography: Use FIPS compliant algorithms" policy.
- [ ] A **static IP**, plus a DNS name if IT can add one (for example `cad.mpd.local`).
- [ ] On a UPS.
- [ ] Remove or disable anything not needed: no web browsing or email on this machine, and no remote-access tools other than what IT already uses under CJIS rules.
- [ ] Only named IT and admin staff with fingerprint background checks have Windows admin rights on it.

### A2. Install Python
Download Python 3.12 or newer from **python.org** (the official site only). Run the installer and choose **Customize installation**, then check **"Install Python for all users"** and **"Add Python to environment variables"**.

### A3. TLS certificate (required: the CAD will not start without one)
Best option: **ask IT to issue a certificate from the town's internal certificate authority** for the server's name (for example `cad.mpd.local`). Ask for it as PEM files: certificate `tls.crt` and private key `tls.key`. Domain-joined PCs then trust it automatically, with no browser warnings.

If there's no internal CA, make a self-signed certificate on any machine with OpenSSL (Git for Windows includes it). Change the name and IP to match your server:
```
openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes -keyout tls.key -out tls.crt ^
  -subj "/CN=cad.mpd.local" -addext "subjectAltName=DNS:cad.mpd.local,IP:10.20.30.5"
```
Then deploy `tls.crt` (**not** the key) to every client PC's *Trusted Root Certification Authorities* store, using Group Policy or the `certlm.msc` console. Keep `tls.key` secret: it goes only on the server.

### A4. Run the installer
Copy the InterimCAD folder to the server. Open **PowerShell as Administrator** in that folder and run:
```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install-server.ps1 -AllowedSubnets 10.20.30.0/24,10.20.31.0/24,172.16.50.0/24
```
Replace the subnets with **your dispatch, PD desk and MDT-VPN address ranges** (IT will know them). The script:
- installs to `C:\InterimCAD` and locks the folder so ordinary Windows users on the server can't read the database;
- writes `config.ini` with your allowed networks (the CAD refuses connections from anywhere else);
- adds a firewall rule opening port 8443 **only** to those subnets;
- registers a startup task that runs the CAD under the low-privilege NETWORK SERVICE account and restarts it if it stops.

Then:
1. Copy `tls.crt` and `tls.key` into `C:\InterimCAD\data\`.
2. Edit `C:\InterimCAD\config.ini`: agency name, ORI, and optionally `[archive] dir` and `[audit] mirror_dir` (use `\\server\share` paths, not drive letters, and grant the **server's computer account**, `DOMAIN\SERVERNAME$`, write access to those shares).
3. Set call numbering to continue after Valcour's last number: `py manage.py set-next-number call NNNN`
4. Create your admin account: `py manage.py create-admin yourname`
5. Start it: `Start-ScheduledTask -TaskName InterimCAD`, then check `C:\InterimCAD\data\logs\server.log`.

### A5. Copies off the server
- [ ] `[audit] mirror_dir` points to a share that **only IT security / the TAC** can modify. That way not even the CAD server's admins can quietly alter the audit trail.
- [ ] `[archive] dir` points to a **PD-only** share. Not a town-wide drive.
- [ ] Daily: copy `C:\InterimCAD\data\backups` to an encrypted, agency-controlled location.

---

## Part B: dispatcher and desk computers

**Nothing to install.** Each PC needs only the steps below. Most can be pushed by IT with Group Policy.

- [ ] **Trust the certificate.** Only needed if it's self-signed: see A3.
- [ ] **Shortcut.** Create a desktop shortcut that opens the CAD in its own window, without browser tabs:
  `"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app=https://cad.mpd.local:8443`
  Name it **CAD**. Users must never be told to "click through" a certificate warning. If a warning appears, stop and call IT: it could mean someone is intercepting the connection.
- [ ] **Individual Windows logins** for each dispatcher. No shared "dispatch" Windows account.
- [ ] **Screen lock after 30 minutes or less of inactivity** (CJIS session lock), set by Group Policy. The CAD also signs users out after 30 idle minutes.
- [ ] **Don't let the browser save CAD passwords.** Edge policy `PasswordManagerEnabled = Disabled`, or in Chrome `PasswordManagerEnabled = false`.
- [ ] BitLocker on laptops and any PC that leaves the building.
- [ ] Windows patched, and antivirus on.
- [ ] USB storage restricted per agency policy (exports and printouts are CJI).
- [ ] Printers used for call printouts are in PD-controlled areas.

## Part C: MDTs and laptops in cars
- [ ] Reach the CAD **only through the existing agency VPN**. It must already meet CJIS requirements, including advanced authentication. Include the VPN's address pool in `allowed_networks`.
- [ ] **Never** open port 8443 on the town's internet firewall or router.
- [ ] Use the same shortcut, certificate trust and BitLocker as Part B.

## Part D: users
1. Admin creates each account (Admin tab, then Users) with a one-time temporary password, handed over in person.
2. At first sign-in each user sets their own password, then enrolls an **authenticator app** on their phone: Microsoft Authenticator, Google Authenticator, or a hardware TOTP token for anyone without a phone.
3. Five-minute briefing: the login banner, sign out at end of shift, never share accounts, report any "failed sign-in attempts" notice they didn't cause, and never click through a certificate warning.

## Part E: go-live verification (do every item, and keep this list with your records)
- [ ] From a dispatch PC: the padlock shows with **no warning**.
- [ ] From a PC **outside** the allowed subnets (for example the town hall Wi-Fi): the CAD **cannot be reached**.
- [ ] Typing `http://` instead of `https://` does not work.
- [ ] A new user is forced to change their password **and** enroll MFA before seeing any data.
- [ ] 5 wrong passwords lock the account. The Audit tab shows the LOGIN_FAIL entries and the chain is marked verified.
- [ ] Leaving the CAD idle for 30 minutes signs the user out.
- [ ] A dispatcher account can't open Admin, Audit or Export.
- [ ] The I: drive archive and audit-mirror folders fill up, and a non-PD town user **cannot** open them.
- [ ] **Restore test:** copy a backup onto a spare PC and start the CAD from it.
- [ ] Your TAC has reviewed docs/SECURITY_CJIS.md, and the CSO has been notified or has approved.

## Updating later
Stop the task (`Stop-ScheduledTask InterimCAD`) and run `py manage.py backup`. Copy the new `cad\` and `static\` folders over the old ones, then start the task again. Your `config.ini` and `data\` stay as they are.
