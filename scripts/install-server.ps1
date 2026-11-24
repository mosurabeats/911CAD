<#
.SYNOPSIS
  Installs Interim CAD on a Windows server/PC as a locked-down background task.

.DESCRIPTION
  - Copies the application to $InstallDir (default C:\InterimCAD)
  - Locks down folder permissions: only Administrators, SYSTEM and the NETWORK SERVICE account
    (which runs the CAD) can access it; ordinary users on this PC cannot read the database
  - Creates config.ini with your allowed networks
  - Adds a Windows Firewall rule allowing the CAD port ONLY from your PD subnets
  - Registers a startup task that runs the CAD as NETWORK SERVICE (least privilege) and restarts it on failure

  Run from an elevated PowerShell prompt in the extracted InterimCAD folder:
    Set-ExecutionPolicy -Scope Process Bypass
    .\scripts\install-server.ps1 -AllowedSubnets 10.20.30.0/24,10.20.31.0/24

  Test on a non-production PC first.

.PARAMETER AllowedSubnets
  Networks allowed to reach the CAD (dispatch PCs, PD desk PCs, MDT VPN address pool).
#>
#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory = $true)][string[]]$AllowedSubnets,
    [string]$InstallDir = "C:\InterimCAD",
    [int]$Port = 8443,
    [string]$TaskName = "InterimCAD"
)
$ErrorActionPreference = "Stop"
$Source = Split-Path -Parent $PSScriptRoot

Write-Host "== Interim CAD installer ==" -ForegroundColor Cyan

# --- 1. Python (must be installed for ALL users so the service account can run it) --------------
$python = $null
foreach ($cand in @("py", "python")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $exe = if ($cand -eq "py") { & py -3 -c "import sys; print(sys.executable)" } else { & python -c "import sys; print(sys.executable)" }
            if ($exe) { $python = $exe.Trim(); break }
        } catch { }
    }
}
if (-not $python) { throw "Python 3.10+ not found. Install it from python.org with 'Install for all users' and 'Add to PATH'." }
$ver = & $python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]"3.10") { throw "Python $ver found; 3.10 or newer is required." }
if ($python -like "$env:SystemDrive\Users\*") {
    throw "Python is installed per-user ($python). Reinstall Python with 'Install for all users' (Program Files) so the service account can run it."
}
Write-Host "Python $ver : $python"

# --- 2. Copy application files ------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
foreach ($d in @("cad", "static", "docs", "scripts")) {
    robocopy (Join-Path $Source $d) (Join-Path $InstallDir $d) /MIR /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
}
foreach ($f in @("manage.py", "config.example.ini", "README.md")) {
    Copy-Item (Join-Path $Source $f) $InstallDir -Force
}
$data = Join-Path $InstallDir "data"
New-Item -ItemType Directory -Force -Path $data, (Join-Path $data "backups"), (Join-Path $data "logs") | Out-Null

# --- 3. config.ini --------------------------------------------------------------------------------
$cfg = Join-Path $InstallDir "config.ini"
if (-not (Test-Path $cfg)) {
    $text = Get-Content (Join-Path $InstallDir "config.example.ini") -Raw
    $text = $text -replace "(?m)^allowed_networks =.*$", ("allowed_networks = " + ($AllowedSubnets -join ", "))
    $text = $text -replace "(?m)^port = .*$", "port = $Port"
    Set-Content -Path $cfg -Value $text -Encoding UTF8
    Write-Host "Created $cfg (edit agency name / ORI / archive paths as needed)."
} else {
    Write-Host "Keeping existing $cfg"
}

# --- 4. Permissions (SIDs avoid problems on non-English Windows) -----------------------------------
# S-1-5-32-544 Administrators, S-1-5-18 SYSTEM, S-1-5-20 NETWORK SERVICE
& icacls $InstallDir /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-20:(OI)(CI)RX" | Out-Null
& icacls $data /grant:r "*S-1-5-20:(OI)(CI)M" | Out-Null
Write-Host "Permissions set: only Administrators, SYSTEM and NETWORK SERVICE can access $InstallDir."

# --- 5. Firewall: only from PD subnets, only on domain/private networks -----------------------------
Get-NetFirewallRule -DisplayName "Interim CAD (HTTPS)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "Interim CAD (HTTPS)" -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress $AllowedSubnets -Action Allow -Profile Domain, Private | Out-Null
Write-Host "Firewall: TCP $Port allowed only from $($AllowedSubnets -join ', ')."

# --- 6. Startup task running as NETWORK SERVICE ----------------------------------------------------
$action = New-ScheduledTaskAction -Execute $python -Argument "manage.py serve" -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "S-1-5-20" -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Interim CAD server" -Force | Out-Null
Write-Host "Startup task '$TaskName' registered (runs as NETWORK SERVICE, restarts on failure)."

# --- 7. Next steps ----------------------------------------------------------------------------------
$hasCert = (Test-Path (Join-Path $data "tls.crt")) -and (Test-Path (Join-Path $data "tls.key"))
Write-Host ""
Write-Host "== Next steps ==" -ForegroundColor Cyan
if (-not $hasCert) {
    Write-Host " 1. Put the TLS certificate and key (PEM) at $data\tls.crt and $data\tls.key" -ForegroundColor Yellow
    Write-Host "    (from your IT/internal CA - see docs\INSTALL_WINDOWS.md step 3). The CAD will not start without them."
} else { Write-Host " 1. TLS certificate found." }
Write-Host " 2. Edit $cfg  (agency name, ORI, [archive] dir, [audit] mirror_dir)"
Write-Host " 3. Create the first administrator:"
Write-Host "      cd $InstallDir ; & '$python' manage.py create-admin <yourname>"
Write-Host " 4. Start it:   Start-ScheduledTask -TaskName $TaskName"
Write-Host " 5. Check:      Get-Content $data\logs\server.log -Tail 20"
Write-Host " 6. From a dispatch PC browse to  https://<this-server-name>:$Port"
