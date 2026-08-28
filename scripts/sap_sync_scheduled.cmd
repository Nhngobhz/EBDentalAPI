@echo off
REM ---------------------------------------------------------------------------
REM Unattended SAP -> Postgres materials sync, for Windows Task Scheduler.
REM
REM Run BY the scheduler on QPLUS365SERVER, where store-api and SQL Server sit on
REM the same machine - hence --transport local, which is sqlcmd against localhost
REM with Windows authentication. There is no SSH and no SQL login in this path;
REM the task's own account is the credential, so it must be one SQL Server knows
REM (Administrator does).
REM
REM Install it with scripts/install_sap_sync_task.ps1, which is where the schedule
REM and the account live. This file is only "what one run does".
REM ---------------------------------------------------------------------------

setlocal

set "APP_DIR=E:\Website\store-api"
set "LOG_DIR=E:\Website\logs"
set "PYTHON=%APP_DIR%\venv\Scripts\python.exe"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM One log per day, appended to. Keeping runs in the file means a morning
REM question ("did last night work?") is answered by one file rather than by
REM re-running the sync to find out.
for /f "tokens=2 delims==" %%d in ('wmic os get LocalDateTime /value') do set "LDT=%%d"
set "STAMP=%LDT:~0,4%-%LDT:~4,2%-%LDT:~6,2%"
set "LOG=%LOG_DIR%\sap_sync_%STAMP%.log"

echo. >> "%LOG%"
echo ===== %DATE% %TIME% - sap_sync starting ===== >> "%LOG%"

cd /d "%APP_DIR%" || (echo Cannot cd to %APP_DIR% >> "%LOG%" & exit /b 1)

REM --apply, because a scheduled dry run would write a report nobody reads and
REM change nothing. The safety rail inside sap_sync (--max-delist-ratio) is what
REM keeps an unattended --apply from emptying the storefront on a partial read.
"%PYTHON%" -m scripts.sap_sync --transport local --apply >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

echo ===== %DATE% %TIME% - sap_sync finished, exit %RC% ===== >> "%LOG%"

REM Propagated so Task Scheduler's "Last Run Result" is the truth. Without this
REM the task reports success every night no matter what the sync did, which is
REM the failure mode that lets a broken sync go unnoticed for a month.
exit /b %RC%
