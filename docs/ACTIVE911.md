# Active911 paging

When Fire or EMS units are dispatched, the CAD pages those crews through Active911 automatically. Dispatchers can also send a page by hand from any open call (for example "Second alarm" or updated information).

## How it works

- Active911 accepts dispatch alerts **by email**. Each Active911 agency has its own alert address, shown in Active911 under **Agency, Alert Email**. It looks like `1234-abc1234@alert.active911.com`.
- The CAD sends each alert through your **town or agency email server**, in Active911's documented **"Cadpage"** format:

```
CALL: Structure Fire
PLACE: Riverside Hardware
ADDR: 12 Barre St
CITY: Montpelier
ID: 26MPD4521
PRI: P1
DATE: 11/24/2026
TIME: 14:32:07
UNIT: E1, L1
INFO: X-ST Main St; Caller reports smoke from second floor windows, occupants out.
```

- **One automatic page per department per call.** It goes out about 5 seconds after the first Fire (or EMS) unit is dispatched, so units dispatched together appear in one page. Dispatching more units later does not page again automatically. Use the **Page Fire / Page EMS** button on the call for that.
- Pages are sent in the background. **A slow or down email server never delays dispatching.** Failed pages retry automatically: 5 attempts over about 8 minutes. Every page, retry and failure is written to the call's narrative and the audit log, and shows in the call's "Active911 alerts" section. If a page finally fails, the narrative says **"FAILED after retries - page crews another way"**.
- **Police calls are not paged** unless you set `police_address`. Police call details are CJI, so ask your CSO first.
- **Caller name and phone are never sent** unless `include_caller = true`.
- EMS pages include the patient count and chief complaint (for example "1 PT; CC 68 y/o male, chest pain"). No patient names are sent.

## Setup (IT, about 30 minutes)

1. **Get the alert addresses.** An Active911 administrator for the fire department and for the ambulance service copies each agency's alert email address.
2. **Get mail-server details from IT:** host, port, security type, and an account allowed to send. Office 365 example: `smtp.office365.com`, port `587`, `starttls`, plus a licensed mailbox or SMTP relay. Allow outbound traffic from the CAD server to that mail server only.
3. **Edit `config.ini`:**

```ini
[active911]
enabled = true
fire_address = 1234-abc1234@alert.active911.com
ems_address = 5678-def5678@alert.active911.com
police_address =
smtp_host = smtp.office365.com
smtp_port = 587
smtp_security = starttls
smtp_username = cad-alerts@yourtown.gov
smtp_password_file = C:\InterimCAD\data\smtp-password.txt
from_address = cad-alerts@yourtown.gov
include_caller = false
default_city = Montpelier
```

   Put the password in its own file (`smtp_password_file`). Make it readable only by Administrators and the CAD service account; the installer's folder permissions already restrict `C:\InterimCAD\data`.
4. **Tell Active911 support** (support@active911.com) that your CAD sends the **"Cadpage" format** to your alert address. Active911 does not detect the format automatically, and they set up parsing per dispatch center. Their documentation says parsing is refined over roughly the first dozen calls, and until then alerts may show as a general alert.
5. **Restart the CAD,** then send a test page:

```
py manage.py test-alert fire
py manage.py test-alert ems
```

   Each sends "TEST ALERT - DISREGARD" and should appear on the crews' phones within a minute. `py manage.py check` also reports whether paging is configured correctly.

## Daily checks
- The **Export tab** shows paging status: on or off, sent, waiting and failed counts, and the last problem if there was one.
- If phones stop receiving pages, check that tab first, then `data\logs\server.log`.

Sources for the email format: Active911's "Setting up Messaging Formats" and "Connecting ActiveAlert to Your Dispatch Center" documentation.
