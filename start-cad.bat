@echo off
REM Starts the Interim CAD server on Windows. Keep this window open.
cd /d "%~dp0"
py -3 manage.py serve
if errorlevel 1 python manage.py serve
pause
