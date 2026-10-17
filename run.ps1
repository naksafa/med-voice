# MedVoice launcher (Windows PowerShell)
#   .\run.ps1            -> install (first time) and start on http://localhost:8000
#   .\run.ps1 -Voice     -> also install local Whisper speech-to-text
param([switch]$Voice, [int]$Port = 8000)

$backend = Join-Path $PSScriptRoot "backend"
$py = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment..."
    python -m venv (Join-Path $backend ".venv")
    & $py -m pip install --upgrade pip -q
    & $py -m pip install -r (Join-Path $backend "requirements.txt")
}
if ($Voice) { & $py -m pip install -r (Join-Path $backend "requirements-voice.txt") }

Write-Host "MedVoice running on http://localhost:$Port  (Ctrl+C to stop)"
Push-Location $backend
try { & $py -m uvicorn app.main:app --port $Port } finally { Pop-Location }
