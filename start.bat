@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
  call "venv\Scripts\activate.bat"
) else (
  echo Keine virtuelle Umgebung gefunden (.venv oder venv).
  exit /b 1
)

python main.py
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
