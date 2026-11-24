# Deployment guide

The goal is a working dispatch board within an hour, on hardware you already have.

## 1. Pick the server machine

- Any always-on PC or VM **inside the PD's secured network and physically secure area**, such as a dispatch-room PC or a server in the IT closet. 4 GB of RAM and a few GB of disk is plenty.
- Windows 10/11, Windows Server, or Linux.
- Give it a **static IP** or a DNS name, such as `cad.mpd.local`.
- Use full-disk encryption (BitLocker on Windows, LUKS on Linux). The database holds CJI. See SECURITY_CJIS.md.
- Put it on a UPS. If possible, have a second PC ready as a warm spare (see Backups).

## 2. Install

**Windows**
1. Install Python 3.12+ from python.org and check **"Add python.exe to PATH"**.
2. Copy this folder to, for example, `C:\InterimCAD`.
3. `copy config.example.ini config.ini`, then edit `config.ini` in Notepad.
4. Open Command Prompt in the folder and run `py manage.py create-admin <yourname>`.
5. Double-click `start-cad.bat`.
6. Allow inbound TCP 8443 in Windows Defender Firewall, **only from the PD subnet(s)**.

**Linux**
```bash
sudo useradd --system --home /opt/interim-cad cad
sudo cp -r . /opt/interim-cad && sudo chown -R cad: /opt/interim-cad
cd /opt/interim-cad && sudo -u cad cp config.example.ini config.ini   # edit it
sudo -u cad python3 manage.py create-admin yourname
```
A systemd unit file, `/etc/systemd/system/interim-cad.service`:
```ini
[Unit]
Description=Interim CAD
After=network.target
[Service]
User=cad
WorkingDirectory=/opt/interim-cad
ExecStart=/usr/bin/python3 manage.py serve
Restart=always
[Install]
WantedBy=multi-user.target
```
`sudo systemctl enable --now interim-cad`

**Windows as a service (so it survives reboots and logoffs):** use NSSM (`nssm install InterimCAD "C:\Python312\python.exe" "C:\InterimCAD\manage.py serve"`, with the app directory set to `C:\InterimCAD`). Alternatively, use Task Scheduler with "Run whether user is logged on or not" and "At startup".

## 3. TLS (encrypt traffic)

Ask your IT provider for a certificate from the town's internal CA if one exists. That avoids browser warnings. Otherwise, create a self-signed one:

```bash
python manage.py make-cert      # prints the exact openssl command
```

Set `tls_cert` and `tls_key` in `config.ini` and restart. Distribute the certificate to client PCs (Trusted Root store) so browsers trust it.

Without TLS, the server logs a warning on every start. Run it that way only for a short test on an isolated network.

## 4. Configure the agency (in the app)

Sign in as the admin, then go to the **Admin** tab:
- **Units**: your call signs.
- **Users**: one account per person, never shared. Hand out temporary passwords in person.
- **Call types / Dispositions**: adjust the preloaded lists.

Set `require_mfa = true` in `config.ini`. Each user then enrolls an authenticator app (Microsoft Authenticator, Google Authenticator, Authy, or a hardware TOTP token) at first sign-in.

## 5. Clients

Any modern browser (Edge, Chrome, Firefox) on dispatch consoles, desk PCs and MDTs that can reach the server. Nothing needs to be installed on the clients.

MDTs in the field reach the server only through your existing agency VPN. **Do not expose port 8443 to the internet.**

## 5a. Multiple dispatchers

Run **one** server. Every dispatcher, supervisor and officer opens the same address in a browser, and they all see the same live board. Don't run a separate copy on each dispatch PC: each copy would be its own separate database.

Tested with 8 dispatchers creating calls at the same moment: no errors, no duplicate call numbers, nothing lost. A department-sized load (a few consoles plus a dozen MDTs) is well within capacity.

## 5b. Copying every call to the I: drive (network share)

Set this in `config.ini`:
```ini
[archive]
dir = I:\CAD\Calls
```
Restart the server. Every call is then written to:
```
I:\CAD\Calls\2026\2026-11\26-000123.txt    readable call record (prints cleanly)
I:\CAD\Calls\2026\2026-11\26-000123.json   complete record (for import/migration)
I:\CAD\Calls\Daily\2026-11-24.csv           that day's call log (opens in Excel)
```
Each call's files are updated within about 30 seconds of any change, or right away when the call is closed. The **Export** tab shows archive status and has a **Copy now** button.

Important details:
- **Mapped drive letters and services:** if the CAD runs as a Windows service (NSSM or Task Scheduler), it usually **cannot see `I:`**, because drive letters are mapped per logged-in user. Use the UNC path instead, for example `dir = \\townserver\police\CAD\Calls`. Ask IT what `I:` maps to: run `net use` in a Command Prompt. The service account needs write permission on that share.
- **Keep the live database local.** Leave `db_path` on the server's own disk. SQLite databases on network shares can be corrupted. The I: drive copy is an archive, not the live system.
- **Drive outages:** if the share is unreachable, dispatch continues normally. The Export tab shows the problem, and every missed call is copied automatically once the share is back.
- **Access control:** these files contain CJI. The folder must be on an agency-controlled server and restricted to the PD security group. It must not be a town-wide share that other departments can read. See SECURITY_CJIS.md.

## 6. Backups

- The server writes a consistent backup **every hour** to `backup_dir` and keeps the last 96 (4 days).
- **Also copy backups off the machine** every day to an encrypted, agency-controlled location, such as a BitLocker-encrypted USB drive kept in a locked area, or an encrypted agency file share.
- Manual backup at any time: `python manage.py backup`.
- **Restore:** stop the server, replace `data/cad.sqlite3` with a backup file, then start the server. Delete any `cad.sqlite3-wal` and `cad.sqlite3-shm` files left beside the old database first.
- **Warm spare:** keep a second PC with the folder and `config.ini` installed. If the primary fails, copy the latest backup over and start it.
- **Run a restore test in week one.** A backup you haven't restored is not a backup.

## 7. Paper fallback

If the server is down, go back to paper run cards. When it is back, enter the calls using **Back-enter a call from a paper log** in the New Call dialog, and correct unit times with the **times** button. Original times are kept, and every correction is logged.

## 8. Upgrades

Stop the server, back up, replace the `cad/` and `static/` folders, then start the server. Your `config.ini` and `data/` stay as they are.
