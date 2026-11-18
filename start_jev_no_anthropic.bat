@echo off
setlocal
py -3 "%~dp0jev_router.py" --disable haiku,sonnet,opus %*
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" exit /b 0
echo.
echo Jev could not start. See the message above and README.txt.
pause
exit /b %RESULT%
