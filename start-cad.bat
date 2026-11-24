@echo off
REM Starts the Interim CAD and opens it in your browser. Keep this window open while using the CAD.
cd /d "%~dp0"
if not exist config.ini (
  echo First run: starting guided setup...
  py -3 manage.py setup || python manage.py setup
)
py -3 manage.py serve --open
if errorlevel 1 python manage.py serve --open
pause
