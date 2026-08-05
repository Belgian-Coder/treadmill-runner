@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File ""%~dp0Install-TreadmillRunner.ps1""'"
if errorlevel 1 (
  echo Installation was cancelled or could not be started.
  exit /b 1
)
