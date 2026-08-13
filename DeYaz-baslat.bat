@echo off
setlocal

rem Hazir DeYaz.exe varsa onu, yoxdursa Python versiyasini basladir.
if exist "%~dp0dist\DeYaz.exe" (
    start "" "%~dp0dist\DeYaz.exe"
) else (
    start "" /b pythonw.exe "%~dp0deyaz_app.py"
)
