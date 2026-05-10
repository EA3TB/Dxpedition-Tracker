@echo off
setlocal EnableDelayedExpansion
:: ============================================================
::  install_tray.bat
::  Instala el Rotor Tray para DXpedition Tracker (Docker/NAS)
::  Compila rotor_tray_win.py como DXpeditionRotor.exe
::  Solo pide admin en la primera instalacion.
:: ============================================================
cd /d "%~dp0"

set TRAY_EXE=%~dp0dist\DXpeditionRotor\DXpeditionRotor.exe

:: ── Si el exe ya existe, saltar compilacion (no necesita admin) ──
if exist "%TRAY_EXE%" goto :configure

:: ── Primera instalacion: necesita admin para compilar ────────
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
echo [OK] Python: %PYTHON%

:: ── Instalar dependencias ────────────────────────────────────
echo.
echo [1/3] Instalando dependencias...
pip install pyserial==3.5 pystray pillow pyinstaller --quiet --user
if errorlevel 1 (
    echo ERROR: Fallo al instalar dependencias.
    pause
    exit /b 1
)
echo [OK] Dependencias instaladas.

:: ── Compilar exe ─────────────────────────────────────────────
echo.
echo [2/3] Compilando DXpeditionRotor.exe...
python "%~dp0make_spec.py"
if errorlevel 1 (
    echo ERROR: No se pudo generar el fichero spec.
    pause
    exit /b 1
)

pyinstaller "%~dp0DXpeditionRotor.spec" --distpath "%~dp0dist" --workpath "%~dp0build" --noconfirm --clean
if errorlevel 1 (
    echo ERROR: Fallo en la compilacion.
    pause
    exit /b 1
)

if not exist "%TRAY_EXE%" (
    echo ERROR: No se encontro el exe compilado.
    pause
    exit /b 1
)
echo [OK] DXpeditionRotor.exe compilado correctamente.

:: ── Configuracion ────────────────────────────────────────────
:configure
echo.
echo ============================================
echo  DXpedition Tracker -- Rotor Tray Setup
echo ============================================
echo.
echo [3/3] Configuracion

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
    echo [OK] Config existente encontrada. Puerto: !COM_PORT!
    goto :install_shortcut
)

:: ── Detectar puertos COM ─────────────────────────────────────
echo Detectando puertos COM...
echo.
for /f "tokens=1,2 delims==" %%A in ('wmic path Win32_SerialPort get DeviceID /format:list 2^>nul') do (
    if "%%A"=="DeviceID" echo   %%B
)
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

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%SHORTCUT%'); $sc.TargetPath = '%TRAY_EXE%'; $sc.Arguments = ''; $sc.WorkingDirectory = '%~dp0dist\DXpeditionRotor'; $sc.WindowStyle = 7; $sc.Description = 'DXpedition Rotor Tray'; $sc.IconLocation = '%TRAY_EXE%,0'; $sc.Save()"

if exist "%SHORTCUT%" (
    echo [OK] Acceso directo creado en Inicio automatico.
) else (
    echo [WARN] No se pudo crear acceso directo.
)

:: ── Cerrar instancia anterior y arrancar ──────────────────────
taskkill /IM DXpeditionRotor.exe /F >nul 2>&1
timeout /t 1 /nobreak >nul

:: Lanzar sin privilegios de admin usando PowerShell runas normal
powershell -Command "Start-Process '%TRAY_EXE%'"

echo.
echo ============================================
echo  Instalacion completada.
echo  Puerto: !COM_PORT!  ^|  HTTP: localhost:8767
echo  Icono en bandeja: verde/amarillo/rojo
echo ============================================
echo.
