@echo off
rem WYSIWYG Grilling — cmd.exe entrypoint; defers to the PowerShell wrapper.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0canvas.ps1" %*
exit /b %ERRORLEVEL%
