# Retro - one-command Windows installer.
#   powershell -ExecutionPolicy Bypass -File install.ps1
# Creates an isolated .venv, installs the tested dependency set, detects an
# NVIDIA GPU for fast Whisper, and puts a launcher icon on your desktop.
# Safe to re-run: it updates the install in place.
param([switch]$NoShortcut)
$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

function Find-Python {
    foreach ($cand in @(@("py", "-3.12"), @("py", "-3.11"), @("py", "-3.10"), @("python"))) {
        try {
            $v = & $cand[0] $cand[1] --version 2>$null
            if ($v -match "^Python 3\.(1[0-2])\.") { return $cand }
        } catch {}
    }
    return $null
}

$py = Find-Python
if ($null -eq $py) {
    Write-Host "Python 3.10-3.12 not found." -ForegroundColor Yellow
    Write-Host "Install it with:  winget install Python.Python.3.12"
    Write-Host "Then run this script again."
    exit 1
}
Write-Host "Using $((& $py[0] $py[1] --version) 2>&1)"

$venv = Join-Path $repo ".venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment..."
    & $py[0] $py[1] -m venv $venv
}
$vpy = "$venv\Scripts\python.exe"

Write-Host "Installing dependencies (first run downloads ~300 MB)..."
& $vpy -m pip install --upgrade pip --quiet
$lock = Join-Path $repo "requirements.lock"
if (Test-Path $lock) { & $vpy -m pip install -r $lock --quiet }
Push-Location $repo
try {
    & $vpy -m pip install . --quiet
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        Write-Host "NVIDIA GPU detected - installing CUDA acceleration (faster + more accurate)..."
        & $vpy -m pip install ".[gpu]" --quiet
    } else {
        Write-Host "No NVIDIA GPU - using CPU speech recognition (works fine)."
    }
} finally { Pop-Location }

if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut("$desktop\Retro.lnk")
    $s.TargetPath = "$venv\Scripts\pythonw.exe"
    $s.Arguments = "-m retro"
    $s.WorkingDirectory = $repo
    $s.IconLocation = Join-Path $repo "retro\assets\icon.ico"
    $s.Description = "Retro - voice remote for Spotify"
    $s.Save()
    Write-Host "Desktop shortcut created." -ForegroundColor Green
}

Write-Host ""
Write-Host "Installed. Two steps left (5 minutes, one time):" -ForegroundColor Green
Write-Host "  1. Create a free app at https://developer.spotify.com/dashboard"
Write-Host "     - Redirect URI (exactly): http://127.0.0.1:8888/callback"
Write-Host "     - API: Web API"
Write-Host "  2. Double-click 'Retro' on your desktop and paste the app's Client ID."
Write-Host ""
Write-Host "Then say: hey retro, play bohemian rhapsody"
