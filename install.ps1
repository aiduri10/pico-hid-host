# pico-hid installer for Windows
# Usage: Right-click → "Run with PowerShell"
#        or: Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#            then: .\install.ps1
#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$InstallDir = "$env:LOCALAPPDATA\pico-hid"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path

function Log  { param($m) Write-Host "[pico-hid] $m" -ForegroundColor Green  }
function Warn { param($m) Write-Host "[pico-hid] $m" -ForegroundColor Yellow }
function Die  { param($m) Write-Host "[pico-hid] ERROR: $m" -ForegroundColor Red; exit 1 }

# ── 1. find Python 3.10+ ──────────────────────────────────────────────────────
$python = $null
foreach ($cmd in @("python", "python3", "py -3")) {
    try {
        $ok = & cmd /c "$cmd -c `"import sys; print(sys.version_info >= (3,10))`" 2>nul"
        if ($ok -eq "True") { $python = $cmd; break }
    } catch {}
}

if (-not $python) {
    Warn "Python 3.10+ not found — installing via winget..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        # refresh PATH in this session
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH","User")
        $python = "python"
    } else {
        Die "winget not available.`nPlease install Python 3.10+ from https://python.org`nthen re-run this script."
    }
}

Log "Python: $(& cmd /c "$python --version 2>&1")"

# ── 2. install source files ───────────────────────────────────────────────────
Log "Installing to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item "$ScriptDir\core.py", "$ScriptDir\windows.py" -Destination $InstallDir -Force

# ── 3. virtual environment + dependencies ────────────────────────────────────
if (-not (Test-Path "$InstallDir\venv")) {
    Log "Creating virtual environment..."
    & cmd /c "$python -m venv `"$InstallDir\venv`""
}

Log "Installing dependencies (bleak, cryptography)..."
& "$InstallDir\venv\Scripts\pip.exe" install -q --upgrade pip
& "$InstallDir\venv\Scripts\pip.exe" install -q bleak cryptography

# ── 4. create launcher (pico-hid.bat) ────────────────────────────────────────
$bat = "$InstallDir\pico-hid.bat"
@"
@echo off
"$InstallDir\venv\Scripts\python.exe" "$InstallDir\windows.py" %*
"@ | Set-Content $bat -Encoding ASCII
Log "Launcher created: $bat"

# ── 5. add InstallDir to user PATH ───────────────────────────────────────────
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if (-not $userPath) { $userPath = "" }
if ($userPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable("PATH", "$InstallDir;$userPath", "User")
    Warn "PATH updated — open a new terminal for it to take effect."
} else {
    Log "PATH already contains $InstallDir"
}

Write-Host ""
Log "All done!  Open a new terminal and run:  pico-hid"
