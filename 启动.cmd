@echo off
schtasks /Run /TN "FeibiFeibaPetSwitch"
if errorlevel 1 pause
