@echo off
setlocal
py -3 "%~dp0setup_opencode_jev.py" %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo Setup failed. See the message above and README.txt.
pause
exit /b %RESULT%
