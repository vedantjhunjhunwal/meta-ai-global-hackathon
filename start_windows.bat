@echo off
REM Start Stetho on this computer and open it in the browser.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py scripts\run_desktop.py) else (python scripts\run_desktop.py)
pause
