@echo off
cd /d "C:\Users\Gstar\OneDrive\Documents\BotGSTAR"
set LOGFILE=%TEMP%\BotGSTAR_startup.log

:: Sémantique "click = restart" : au lancement on tue toute autre instance
:: (ancien watchdog par titre, ancien bot par cmdline) puis on prend la main.
:: La fenêtre courante n'a pas encore le titre cible → elle ne se tuera pas.
tasklist /FI "WINDOWTITLE eq BotGSTAR - Pipeline COURS" 2>nul | find /I "cmd.exe" >nul
if not errorlevel 1 (
    taskkill /F /FI "WINDOWTITLE eq BotGSTAR - Pipeline COURS" >nul 2>&1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'bot\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

:: À partir d'ici, on est l'unique watchdog.
title BotGSTAR - Pipeline COURS
echo ============================================
echo  BotGSTAR - Watchdog
echo  Log fichier : %LOGFILE%
echo  (logs live ci-dessous ET dans le fichier)
echo ============================================
echo.

:loop
echo [%date% %time%] Demarrage du bot...
echo [%date% %time%] Demarrage du bot... >> "%LOGFILE%"
:: Tee-Object : stdout+stderr diffusés SIMULTANÉMENT dans la console
:: et appendés au fichier log. `python -u` = unbuffered pour live streaming.
powershell -NoProfile -Command "python -u bot.py 2>&1 | Tee-Object -FilePath '%LOGFILE%' -Append"
echo.
echo [%date% %time%] Bot arrete (code: %ERRORLEVEL%). Redemarrage dans 10s...
echo [%date% %time%] Bot arrete (code: %ERRORLEVEL%). Redemarrage dans 10s... >> "%LOGFILE%"
timeout /t 10 /nobreak
goto loop
