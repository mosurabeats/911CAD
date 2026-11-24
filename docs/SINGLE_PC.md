# Set up on one computer only

In this mode the CAD runs on your computer and **only your computer can open it**. Other PCs on the network can't reach it. Nothing needs to be installed on a server, and no security certificate is needed, because the traffic never leaves the machine.

**Use a department-issued, password-protected computer, not a personal one.** The CAD will hold criminal justice information. The computer should have disk encryption on (BitLocker on Windows, FileVault on a Mac) and lock its screen after 30 minutes or less.

## 1. Install Python (one time, about 3 minutes)
- **Windows:** go to python.org, choose Downloads, and run the installer. **Check "Add python.exe to PATH"**, then click Install Now.
- **Mac:** go to python.org, choose Downloads, and run the macOS installer.

## 2. Get the CAD files
On GitHub, open the repository, switch to the branch `claude/vibrant-pasteur-3qg06w`, and choose **Code**, then **Download ZIP**. Unzip it somewhere permanent, for example `C:\InterimCAD` or `Documents/InterimCAD`.

## 3. Start it (it sets itself up the first time)
- **Windows:** double-click `start-cad.bat`.
- **Mac:** double-click `start-cad.command`. If macOS blocks it, right-click the file, choose **Open**, then **Open** again.

The first time, it asks a few questions:
1. Agency name (press Enter to accept "Montpelier Police Department").
2. The letters in your call numbers (press Enter for `MPD`, which gives numbers like `26MPD0001`).
3. A folder to keep a copy of every call in (for example `I:\CAD\Calls`), or press Enter to skip.
4. Your username, full name and a password (12+ characters, not something like "Police2026!").
5. **The next call number.** Enter the number after the last call number Valcour issued this year, so numbers don't clash when the data goes back. Press Enter to start at 0001.

Your browser then opens the CAD. Sign in, then set up two-factor with an authenticator app on your phone (Microsoft Authenticator, Google Authenticator, etc.).

## 4. Every day after that
Double-click the same start file. **Keep the black window open** while you use the CAD; closing it stops the CAD. The CAD itself is at **http://localhost:8080** if you need to reopen the browser tab.

## Where your data is
| What | Where |
|---|---|
| Live database | `data/cad.sqlite3` in the CAD folder |
| Automatic hourly backups (last 96 kept) | `data/backups/` |
| Copy of every call, if you set a folder | the folder you chose, e.g. `I:\CAD\Calls` |

Copy `data/backups` somewhere safe and encrypted regularly, such as the I: drive or an encrypted USB drive. If this computer fails, those backups are your records.

## Moving to a shared setup later
If other dispatchers need to use it too, nothing is lost. Follow [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) on whichever computer will host it, and copy your `data` folder and `config.ini` over first.
