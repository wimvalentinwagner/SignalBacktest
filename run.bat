@echo off
setlocal enableextensions
chcp 65001 >nul
title SignalBacktest

cd /d "%~dp0"

echo.
echo === SignalBacktest ===
echo.

REM === 1) Python finden ===
echo [1/4] Suche Python ...
set "SYSPY="
where py >nul 2>nul
if %errorlevel% equ 0 set "SYSPY=py -3"
if not defined SYSPY (
    where python >nul 2>nul
    if %errorlevel% equ 0 set "SYSPY=python"
)
if not defined SYSPY (
    echo.
    echo [FEHLER] Python wurde nicht gefunden.
    echo Bitte Python 3.10 oder neuer installieren: https://www.python.org/downloads/
    echo Beim Setup unbedingt "Add Python to PATH" aktivieren.
    goto :end
)
%SYSPY% -V
if errorlevel 1 (
    echo.
    echo [FEHLER] Python wurde gefunden, laesst sich aber nicht ausfuehren.
    echo Falls Microsoft-Store-Stub aktiv ist: echtes Python von python.org installieren.
    goto :end
)

REM === 2) virtuelle Umgebung ===
echo.
echo [2/4] Pruefe virtuelle Umgebung .venv ...
if not exist ".venv\Scripts\python.exe" (
    echo        Erstelle .venv ^(einmalig^) ...
    %SYSPY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo [FEHLER] Konnte .venv nicht erstellen.
        goto :end
    )
)
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo.
    echo [FEHLER] .venv ist beschaedigt: %PY% nicht vorhanden.
    goto :end
)

REM === 3) Pakete pruefen / installieren ===
echo.
echo [3/4] Pruefe Pakete ^(PySide6, pandas, numpy, matplotlib^) ...
"%PY%" -c "import PySide6, pandas, numpy, matplotlib" >nul 2>nul
if errorlevel 1 (
    echo        Installiere/aktualisiere Pakete ^(einmalig, dauert 1-3 Minuten^) ...
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install --upgrade -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [FEHLER] Paketinstallation fehlgeschlagen. Internetverbindung pruefen.
        goto :end
    )
    "%PY%" -c "import PySide6, pandas, numpy, matplotlib" >nul 2>nul
    if errorlevel 1 goto :rebuild_venv
) else (
    echo        Alle Pakete vorhanden.
)
goto :run_app

:rebuild_venv
echo.
echo [REPAIR] Imports schlagen trotz Installation fehl - haeufig durch
echo          inkompatible numpy/pandas-Wheels einer Vor-Installation.
echo          Diagnose pro Modul:
echo.
"%PY%" -c "import numpy; print('  numpy OK', numpy.__version__)" 2>&1
"%PY%" -c "import pandas; print('  pandas OK', pandas.__version__)" 2>&1
"%PY%" -c "import PySide6; print('  PySide6 OK', PySide6.__version__)" 2>&1
"%PY%" -c "import matplotlib; print('  matplotlib OK', matplotlib.__version__)" 2>&1
echo.
echo [REPAIR] Loesche .venv und installiere von Null ...
rmdir /s /q .venv
if exist ".venv" (
    echo [FEHLER] Konnte .venv nicht loeschen ^(eventuell durch Antivirus blockiert^).
    echo Bitte den Ordner .venv manuell loeschen und run.bat erneut starten.
    goto :end
)
%SYSPY% -m venv .venv
if errorlevel 1 (
    echo [FEHLER] Konnte .venv nicht neu erstellen.
    goto :end
)
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [FEHLER] Paketinstallation in neuer .venv fehlgeschlagen.
    goto :end
)
"%PY%" -c "import PySide6, pandas, numpy, matplotlib"
if errorlevel 1 (
    echo.
    echo [FEHLER] Auch nach Neuaufbau schlagen Imports fehl.
    echo Falls "DLL load failed": Microsoft Visual C++ Redistributable installieren:
    echo https://aka.ms/vs/17/release/vc_redist.x64.exe
    goto :end
)

:run_app
echo.
echo [4/4] Starte SignalBacktest ...
echo.
"%PY%" signalbacktest.py
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo [FEHLER] App ist mit Fehlercode %RC% beendet.
)

:end
echo.
echo === Ende. Druecke eine Taste zum Schliessen. ===
pause >nul
endlocal
