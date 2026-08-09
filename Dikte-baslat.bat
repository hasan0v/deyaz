@echo off
setlocal

rem Hazir Dikte.exe varsa onu, yoxdursa Python versiyasini basladir.
if exist "%~dp0dist\Dikte.exe" (
    start "" "%~dp0dist\Dikte.exe"
) else (
    start "" /b pythonw.exe "%~dp0dikte_windows.py"
)
