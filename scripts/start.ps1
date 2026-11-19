$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { py -3.12 -m venv .venv }
& '.\.venv\Scripts\python.exe' -m pip install -r backend/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости Python.' }
Push-Location frontend
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости frontend.' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать frontend.' }
} finally { Pop-Location }
Push-Location backend
try {
    & '..\.venv\Scripts\python.exe' -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось применить миграции.' }
    & '..\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
} finally { Pop-Location }
