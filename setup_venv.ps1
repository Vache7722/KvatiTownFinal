# Create the project virtual environment with Python 3.12 (required).
# Do NOT use plain "python -m venv .venv" — that may pick Python 3.13 on this machine.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Test-Path .venv) {
    Remove-Item -Recurse -Force .venv
}

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt

Write-Host ""
Write-Host "Done. Activate with:  .venv\Scripts\activate"
Write-Host "Then run:           python launch.py --sim --task passing"
