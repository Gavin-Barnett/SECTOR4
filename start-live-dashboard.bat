@echo off
setlocal

cd /d "%~dp0"

set WEB_PORT=5180
set LIVE_BACKFILL_DAYS=7
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="WEB_PORT" set WEB_PORT=%%B
  )
)

where docker >nul 2>nul
if errorlevel 1 goto :missing_docker

where make >nul 2>nul
if errorlevel 1 goto :missing_make

echo Starting database...
docker compose -f infrastructure/docker-compose.yml up -d db
if errorlevel 1 goto :error

echo Building API and web images...
docker compose -f infrastructure/docker-compose.yml build api web
if errorlevel 1 goto :error

echo Applying migrations...
call make migrate
if errorlevel 1 goto :error

echo Refreshing recent live SEC filings and recomputing signals...
docker compose -f infrastructure/docker-compose.yml run --rm api python -m app.cli.main ingest-backfill --days %LIVE_BACKFILL_DAYS% --recompute
if errorlevel 1 goto :error

echo Starting API and dashboard in a new window...
start "SECTOR4 Live Daily" cmd /k "cd /d ""%~dp0"" && docker compose -f infrastructure/docker-compose.yml up api web"

echo Opening dashboard and API docs...
start "" "http://localhost:%WEB_PORT%"
start "" "http://localhost:8000/docs"

echo.
echo Dashboard: http://localhost:%WEB_PORT%
echo API docs:  http://localhost:8000/docs
echo.
echo If you still have sample data in your database, run start-live-only.bat once first.
exit /b 0

:missing_docker
echo Docker is not available on PATH.
pause
exit /b 1

:missing_make
echo make is not available on PATH.
echo Open PowerShell in this repo and run:
echo $env:PATH += ";$HOME\scoop\shims"
pause
exit /b 1

:error
echo.
echo Live refresh failed. Check the output above for details.
pause
exit /b 1
