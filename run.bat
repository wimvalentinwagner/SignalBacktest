@echo off
setlocal enableextensions
chcp 65001 >nul
title SignalBacktest

cd /d "%~dp0"

REM === 1) Python finden ===
set "SYSPY="
where python >nul 2>nul && set "SYSPY=python"
if not defined SYSPY (
    where py >nul 2>nul && set "SYSPY=py -3"
)
if not defined SYSPY (
    echo.
    echo [FEHLER] Python wurde nicht gefunden.
    echo Bitte Python 3.10 oder neuer installieren: https://www.python.org/downloads/
    echo Beim Setup unbedingt "Add Python to PATH" aktivieren.
    echo.
    pause
    exit /b 1
)

REM === 2) virtuelle Umgebung anlegen, falls nicht vorhanden ===
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Erstelle virtuelle Python-Umgebung in .venv ...
    %SYSPY% -m venv .venv
    if errorlevel 1 (
        echo [FEHLER] Konnte .venv nicht erstellen.
        pause
        exit /b 1
    )
)

set "PY=.venv\Scripts\python.exe"

REM === 3) Abhaengigkeiten pruefen, fehlende installieren ===
"%PY%" -c "import PySide6, pandas, numpy, matplotlib" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installiere benoetigte Pakete (einmalig, dauert 1-3 Minuten) ...
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [FEHLER] Paketinstallation fehlgeschlagen. Internetverbindung pruefen.
        pause
        exit /b 1
    )
)

REM === 4) App starten ===
echo [INFO] Starte SignalBacktest ...
"%PY%" signalbacktest.py
if errorlevel 1 (
    echo.
    echo [FEHLER] App ist mit Fehlercode %errorlevel% beendet.
    pause
)

endlocal
