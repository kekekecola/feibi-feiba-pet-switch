@echo off
cd /d "%~dp0"
py -3 manager.py settings
if errorlevel 1 pause
