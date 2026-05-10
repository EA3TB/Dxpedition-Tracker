@echo off
cd /d "%~dp0"

echo ============================================
echo  DXpedition Tracker -- Build Windows .exe
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado.
    pause
    exit /b 1
)

echo [1/3] Instalando dependencias...
pip install -r requirements_win.txt --quiet --user
if errorlevel 1 (
    echo ERROR: Fallo al instalar dependencias.
    pause
    exit /b 1
)

echo [2/3] Compilando ejecutable...
pyinstaller dxpedition_win.spec --clean --noconfirm
if errorlevel 1 (
    echo ERROR: Fallo en PyInstaller.
    pause
    exit /b 1
)

echo [3/3] Listo!
echo.
echo Ejecutable: %~dp0dist\DXpeditionTracker.exe
echo Datos:      %APPDATA%\DXpeditionTracker\
echo.
pause
