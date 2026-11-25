@echo off
REM تشغيل المشروع كامل: الموبايل الأول وبعده السيميوليشن
cd /d "%~dp0"
start "Phone App" python phone.py
timeout /t 2 /nobreak >nul
python sim.py
