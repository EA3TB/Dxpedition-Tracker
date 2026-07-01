@echo off
setlocal EnableDelayedExpansion
:: ============================================================
::  install_tray.bat
::  Instala el Rotor Tray para DXpedition Tracker
::  No requiere NSSM ni servicios Windows
:: ============================================================
cd /d "%~dp0"

:: ── Auto-elevacion ───────────────────────────────────────────
net session >nul 2>&1
if errorlevel 1 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================
echo  DXpedition Tracker -- Rotor Tray Setup
echo ============================================
echo.

:: ── Verificar Python ─────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('where python') do (
    set PYTHON=%%i
    goto :found_python
)
:found_python

:: Usar pythonw.exe para arrancar sin ventana de consola
set PYTHONW=%PYTHON:python.exe=pythonw.exe%
if not exist "%PYTHONW%" set PYTHONW=%PYTHON%
echo [OK] Python: %PYTHON%
echo [OK] PythonW: %PYTHONW%

:: ── Instalar dependencias ────────────────────────────────────
echo.
echo [1/3] Instalando dependencias...
pip install pyserial==3.5 pystray pillow --quiet --user
if errorlevel 1 (
    echo ERROR: Fallo al instalar dependencias.
    pause
    exit /b 1
)
echo [OK] pyserial + pystray + pillow instalados.

:: ── Comprobar config existente ───────────────────────────────
set CFG_DIR=%APPDATA%\DXpeditionTracker
set CFG_FILE=%CFG_DIR%\rotor_config.json
set COM_PORT=

if exist "%CFG_FILE%" (
    for /f "tokens=2 delims=:, " %%A in ('findstr /i "\"port\"" "%CFG_FILE%"') do (
        if "!COM_PORT!"=="" set COM_PORT=%%~A
    )
    set COM_PORT=!COM_PORT:"=!
    set COM_PORT=!COM_PORT: =!
)

if not "!COM_PORT!"=="" (
    echo [OK] Configuracion existente encontrada. Puerto: !COM_PORT!
    echo      Para cambiar el puerto usa el menu del tray: clic derecho ^> Configuracion
    goto :install_shortcut
)

:: ── Detectar puertos COM ─────────────────────────────────────
echo.
echo [2/3] Detectando puertos COM...
echo.
for /f "tokens=1,2 delims==" %%A in ('wmic path Win32_SerialPort get DeviceID /format:list 2^>nul') do (
    if "%%A"=="DeviceID" echo   %%B
)
echo.

:: ── Configuracion ────────────────────────────────────────────
echo [3/3] Configuracion del puerto COM
echo.

:ask_port
set COM_PORT=
set /p COM_PORT="  Puerto COM del ARS-USB (ej: COM6): "
echo !COM_PORT!| findstr /r "^[0-9][0-9]*$" >nul 2>&1
if not errorlevel 1 set COM_PORT=COM!COM_PORT!
echo !COM_PORT!| findstr /i /r "^COM[0-9]" >nul 2>&1
if errorlevel 1 (
    echo   [!] Formato invalido. Escribe 6 o COM6
    goto ask_port
)

:: ── Guardar config ────────────────────────────────────────────
if not exist "%CFG_DIR%" mkdir "%CFG_DIR%"

(
echo {
echo   "enabled": true,
echo   "controller": "gs232a",
echo   "port": "!COM_PORT!",
echo   "rotor_min": 0,
echo   "rotor_max": 360,
echo   "resolution": 5
echo }
) > "%CFG_FILE%"
echo [OK] Config guardada en %CFG_FILE%

:: ── Acceso directo en startup ─────────────────────────────────
:install_shortcut
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT=%STARTUP%\DXpeditionRotor.lnk
set TRAY_SCRIPT=%~dp0rotor_tray_win.py

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%SHORTCUT%'); $sc.TargetPath = '%PYTHONW%'; $sc.Arguments = '\"%TRAY_SCRIPT%\"'; $sc.WorkingDirectory = '%~dp0'; $sc.WindowStyle = 7; $sc.Description = 'DXpedition Rotor Tray'; $sc.Save()"

if exist "%SHORTCUT%" (
    echo [OK] Acceso directo creado en Inicio automatico.
) else (
    echo [WARN] No se pudo crear acceso directo automaticamente.
)

:: ── Arrancar ahora sin ventana ────────────────────────────────
echo.
echo Arrancando tray app...
start "" "%PYTHONW%" "%TRAY_SCRIPT%"

echo.
echo ============================================
echo  Instalacion completada.
echo.
echo  Puerto        : !COM_PORT!
echo  HTTP          : http://localhost:8767
echo  Inicio auto   : Si (shell:startup)
echo  Sin ventana   : Si (pythonw.exe)
echo.
echo  Icono en la bandeja: verde/amarillo/rojo
echo  Menu: clic derecho en el icono
echo ============================================
echo.
