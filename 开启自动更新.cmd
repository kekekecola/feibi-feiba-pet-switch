@echo off
cd /d "%~dp0"
py -3 install.py update --enable-auto-updates
pause
