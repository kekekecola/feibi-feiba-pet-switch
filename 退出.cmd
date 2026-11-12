@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0app\bridge_control.py" quit
if errorlevel 1 pause
