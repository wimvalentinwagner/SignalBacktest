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

REM Pruefen ob Python wirklich startet (Microsoft-Store-Stub erkennen)
%SYSPY% -V
if errorlevel 1 (
    echo.
    echo [FEHLER] Python wurde gefunden, laesst sich aber nicht ausfuehren.
    echo Falls Microsoft-Store-Stub aktiv ist: echtes Python von python.org installieren
    echo und in den App-Ausfuehrungsaliassen ^(Einstellungen^) deaktivieren.
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
    echo Loesche den Ordner .venv im Projektverzeichnis und starte run.bat neu.
    goto :end
)

REM === 3) Pakete pruefen / installieren ===
echo.
echo [3/4] Pruefe Pakete ^(PySide6, pandas, numpy, matplotlib^) ...
"%PY%" -c "import PySide6, pandas, numpy, matplotlib" >nul 2>nul
if errorlevel 1 (
    echo        Installiere benoetigte Pakete einmalig - kann 1-3 Minuten dauern ...
    "%PY%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo.
        echo [FEHLER] pip-Upgrade fehlgeschlagen. Internetverbindung pruefen.
        goto :end
    )
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [FEHLER] Paketinstallation fehlgeschlagen. Internetverbindung pruefen.
        goto :end
    )
    "%PY%" -c "import PySide6, pandas, numpy, matplotlib"
    if errorlevel 1 (
        echo.
        echo [FEHLER] Pakete sind installiert, aber Import schlaegt fehl.
        goto :end
    )
) else (
    echo        Alle Pakete vorhanden.
)

REM === 4) App starten ===
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
echo === Ende. Fenster bleibt offen, druecke eine Taste zum Schliessen. ===
pause >nul
endlocal
