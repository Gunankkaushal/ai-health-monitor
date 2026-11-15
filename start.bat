@echo off
REM Windows launcher: double-click or run "start.bat" (extra args are passed through, e.g. start.bat --check)
cd /d "%~dp0"
where py >nul 2>nul && (py -3 start.py %* & goto :eof)
python start.py %*
