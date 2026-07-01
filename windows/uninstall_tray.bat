@echo off
cd /d "%~dp0"

:: ── Auto-elevacion ───────────────────────────────────────────
net session >nul 2>&1
if errorlevel 1 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================
echo  DXpedition Tracker -- Rotor Tray Uninstall
echo ============================================
echo.

:: Parar proceso si está corriendo
taskkill /f /im python.exe /fi "WINDOWTITLE eq rotor_tray*" >nul 2>&1

:: Eliminar acceso directo de startup
set SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DXpeditionRotor.lnk
if exist "%SHORTCUT%" (
    del "%SHORTCUT%"
    echo [OK] Acceso directo eliminado.
) else (
    echo [INFO] No habia acceso directo en Inicio automatico.
)

echo.
echo [OK] Desinstalacion completada.
pause
