@echo off
setlocal
cd /d "%~dp0"
rem start.ps1 baut den Server, erstellt fehlende Container und wartet auf die API.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
