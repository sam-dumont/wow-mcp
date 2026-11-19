# Installs wow-stats for the current Windows user: a scheduled task that runs
# at logon and every 5 minutes, without a console window, and Polestar
# Recorder in the AddOns folder (wow_stats.py --install-recorder). No admin
# needed.
# Run it from the folder it came in:
#   powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
# Safe to rerun (after updating wow_stats.py, for instance).
# $env:WOW_STATS_QUIET = '1' (the one-line installers) prints only the
# closing checklist (wow_stats.py --preflight): what works, and the one fix
# for each thing that doesn't.
$ErrorActionPreference = 'Stop'
$Quiet = $env:WOW_STATS_QUIET -eq '1'
function Say($text) { if (-not $Quiet) { Write-Host $text } }

$TaskName = 'wow-stats'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dest = Join-Path $env:LOCALAPPDATA 'wow-stats'
$Script = Join-Path $Dest 'wow_stats.py'
$ConfDir = Join-Path $env:APPDATA 'wow-stats'
$Conf = Join-Path $ConfDir 'config.toml'

# Python 3.11+: one that uv manages, installed for this user when there is
# none, else the py launcher's. The task names that python.exe for good: a
# Python of uv's own (under %APPDATA%\uv) doesn't vanish when the person
# uninstalls or upgrades their own, and --system keeps a virtual environment
# (the window's folder may hold one) out of it.
$python = $null
$uv = Get-Command uv -ErrorAction SilentlyContinue
$uvPath = if ($uv) { $uv.Source } else { Join-Path $env:USERPROFILE '.local\bin\uv.exe' }
if (Test-Path $uvPath) {
    $find = @('python', 'find', '--system', '--python-preference', 'only-managed', '>=3.11')
    # Windows PowerShell 5.1 turns a native command's stderr into errors: don't stop on them here.
    $ErrorActionPreference = 'Continue'
    $python = & $uvPath @find 2>$null
    if (-not $python) {
        Write-Host 'Installing Python 3.12 for wow-stats (uv)...'
        & $uvPath python install 3.12 2>$null | Out-Null
        if ($LASTEXITCODE) { throw "uv could not install Python (code $LASTEXITCODE)" }
        $python = & $uvPath @find 2>$null
    }
    $ErrorActionPreference = 'Stop'
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $python = & py -3 -c "import sys; print(sys.executable if sys.version_info >= (3, 11) else '')"
}
if (-not $python) {
    throw 'needs uv (https://docs.astral.sh/uv/) or Python 3.11+ (python.org, with the py launcher)'
}
$python = "$python".Trim()
# pythonw.exe runs without a console window flashing up every 5 minutes.
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }

# A copy, so moving or updating the download doesn't break the task.
New-Item -ItemType Directory -Force -Path $Dest, $ConfDir | Out-Null
Copy-Item (Join-Path $Here 'wow_stats.py') $Script -Force
# The GPL's text (the one-line installer carries it): the program's license, next to it.
if (Test-Path (Join-Path $Here 'LICENSE')) { Copy-Item (Join-Path $Here 'LICENSE') (Join-Path $Dest 'LICENSE') -Force }
Say "script: $Script (runs with: $pythonw)"

if (-not (Test-Path $Conf)) {
    Copy-Item (Join-Path $Here 'config.example.toml') $Conf
    Say "config: $Conf created from the example; fill in [ship] to send events"
}

# The folder comes back through a pipe: UTF-8 both ways, or an accented
# folder name (C:\Users\Zoe with its diaeresis) is garbled and looks missing.
$env:PYTHONIOENCODING = 'utf-8'
$enc = [Console]::OutputEncoding
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$wow = "$(& $python $Script --print-wow-dir | Select-Object -Last 1)".Trim()
try { [Console]::OutputEncoding = $enc } catch { }
if (-not (Test-Path (Join-Path $wow 'WTF'))) {
    Write-Warning "no WTF folder in ${wow}: set wow_dir in ${Conf}, then run this again"
}

# Polestar Recorder (not on CurseForge): the pinned release, checked. A
# failed download doesn't stop the install; it says why on stderr.
$ErrorActionPreference = 'Continue'
if ($Quiet) { & $python $Script --install-recorder 2>$null | Out-Null } else { & $python $Script --install-recorder }
$ErrorActionPreference = 'Stop'

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$Script`"" -WorkingDirectory $Dest
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$every5 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atLogon, $every5) `
    -Settings $settings -Principal $principal -Force `
    -Description 'wow-stats: archives WoW Forever SavedVariables and ships the stats' | Out-Null
Start-ScheduledTask -TaskName $TaskName
Say "task: '$TaskName' registered and started; log in $(Join-Path $env:LOCALAPPDATA 'wow-stats\wow-stats.log')"
# The task's own definition, next to the script once it is registered: the
# shipper's auto-update compares it with its server's and runs this installer
# again when it changed.
if ((Resolve-Path $Here).Path -ne (Resolve-Path $Dest).Path) {
    Copy-Item (Join-Path $Here 'install-windows.ps1') (Join-Path $Dest 'install-windows.ps1') -Force
}

# The checklist: exits non-zero only when the install can't work as it is.
Say ''
$ErrorActionPreference = 'Continue'
& $python $Script --preflight
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($code) { exit $code }
