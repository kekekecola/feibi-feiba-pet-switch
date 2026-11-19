@echo off
cd /d "%~dp0"
py -3 manager.py stop
if errorlevel 1 pause
