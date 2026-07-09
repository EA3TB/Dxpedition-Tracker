# DXpedition Tracker — Windows v25

Aplicacion de escritorio para el seguimiento de DXpediciones.
Incluye dashboard web (FastAPI + HTML/JS), propagacion HF, calendario DX,
spots de cluster en vivo, control de rotor ARS-USB y system tray.

---

## Stack

- Backend  : FastAPI + uvicorn (embebido en el .exe)
- Frontend : HTML/JS puro, SortableJS 1.14.0 para drag & drop
- Puerto   : 8766 (dashboard) / 8767 (rotor)
- Datos    : %APPDATA%\DXpeditionTracker\

---

## Novedades v25 (respecto a v24a)

- **Filtro de bandas**: checkboxes en la UI para HF 6–160m, persistido en config
- **Calendario DX**: scraping de NG3K ADXO (`dx_calendar.py`) — crea/elimina automáticamente
  tarjetas de expediciones activas, ordenadas por fecha de fin
- **Spots DX**: datos en vivo del cluster dxwatch.com (`dx_spots.py`), mostrados en popup
  flotante al pulsar el botón "DX" de cada tarjeta
- Toolbar reorganizada en formato compacto multi-fila
- Límite de tarjetas de expedición elevado de 20 a 50
- Módulo unificado `hf_propagation.py` (reemplaza el modelo dual SP/LP inline anterior),
  compartido con la versión Docker
- SSN obtenido de HamQSL (`<sunspots>`) junto con SFI y Kp

### Correcciones
- `hrd_parser.py`: bug de mapeo de banda — 160m y 6m se mapeaban incorrectamente a 80m y 10m
- Textos de UI corregidos de español a inglés
- `rotor_tray_win.py`: crash al abrir el diálogo de Configuración — creaba un segundo `tk.Tk()`
- Bug de cálculo LUF: `luf_adj` se calculaba pero nunca se aplicaba
- Fórmula de longitud del punto medio LP incorrecta para valores positivos de `mid_lon_sp`
  (rutas Europa → África oriental); corregida a:
  `mid_lon_lp = mid_lon_sp + 180; if mid_lon_lp > 180: mid_lon_lp -= 360`
- `AbortSignal.timeout()` sustituido por `AbortController + setTimeout` (compatibilidad Firefox)
- Etiquetas de banda en celdas de tarjeta sin sufijo "m" (solo las píldoras de filtro lo llevan)

> Nota: `index_win.html` mantiene adiciones propias no presentes en la versión Docker
> (campo qth, fallback `activeModes`, `fetchPropForExp`, `fetchAllProp`) — no sincronizar
> funciones de Docker a ciegas sobre este fichero.

---

## Estructura de ficheros

```
dxpedition_win/
├── run_win.py               <- launcher principal
├── rotor_tray_win.py        <- system tray del rotor
├── dxpedition_win.spec      <- configuracion PyInstaller (2 exe)
├── requirements_win.txt     <- dependencias Python
├── BUILD_WINDOWS.bat        <- script de compilacion
├── dxp_icon.ico
├── backend/
│   ├── main_win.py          <- API FastAPI Windows
│   ├── persistence_win.py   <- capa de datos (APPDATA)
│   ├── hrd_parser.py
│   ├── cty_parser.py
│   ├── log_readers.py
│   ├── hf_propagation.py
│   ├── dx_calendar.py
│   ├── dx_spots.py
│   └── __init__.py
├── rotor/
│   ├── __init__.py          <- interfaz base RotorController, RotorStatus, get_available_controllers()
│   ├── gs232a.py            <- controlador ARS-USB (GS-232A)
│   └── dummy.py
└── frontend/
    └── index_win.html
```

---

## Compilacion

```bat
BUILD_WINDOWS.bat
```

PyInstaller con `--onedir` (no `--onefile`, evita el "(2)" en el Administrador de tareas).
Genera en dist\:
- DXpeditionTracker.exe  — dashboard principal
- rotor_tray_exe.exe     — tray del rotor (lanzado automaticamente)

---

## Fuentes de log soportadas

| Tipo | Extension | Seleccion |
|------|-----------|-----------|
| HRD XML | .xml | Carpeta — carga el mas reciente automaticamente |
| Swisslog MDB | .mdb | Fichero individual (requiere Microsoft Access Database Engine) |
| Log4OM SQLite | .sqlite / .db | Fichero individual |
| ADIF | .adi / .adif | Fichero individual |

Al seleccionar Swisslog MDB se verifica si Access Database Engine esta instalado.

---

## Propagación HF, Calendario DX y Spots DX

- `hf_propagation.py`: módulo unificado SP/LP, LUF/MUF, punto medio geográfico.
  Fuentes: NOAA (Kp), HamQSL (SFI, SSN)
- `dx_calendar.py`: scraping NG3K ADXO, tarjetas automáticas ordenadas por fecha de fin
- `dx_spots.py`: cluster dxwatch.com, popup flotante vía botón "DX" en cada tarjeta

---

## Control de rotor

### Arquitectura

```
DXpeditionTracker.exe
  ├── FastAPI :8766
  └── rotor_tray_exe.exe
        ├── HTTP :8767
        └── COM10 (Eltima) -> ARS-USB -> Prosistel
```

### Protocolo GS-232A (EA4TX ARS-USB)
- Comando movimiento : M + 3 digitos (ej: M338)
- Comando posicion   : C -> respuesta +XXXX
- Comando stop       : S (Stop all — el comando A es ignorado por el Prosistel)
- Rango              : 0-360 grados, tope en Norte (0/360)
- Direccion          : arco mas corto que no cruce el tope

### Notas de instalacion
- Puerto fisico : COM6 (USB Serial Port)
- Puerto virtual: COM10 (Eltima — comparte COM6 con ARS-USB Setup)

### Menu del tray
- Estado del rotor
- Ver log
- Configuracion — cambiar puerto COM sin reinstalar (fix: ya no crea un segundo tk.Tk())
- Iniciar / Detener / Salir

---

## Datos

| Fichero | Ruta |
|---------|------|
| config.json | %APPDATA%\DXpeditionTracker\config.json |
| expeditions.json | %APPDATA%\DXpeditionTracker\expeditions.json |
| cty.dat | %APPDATA%\DXpeditionTracker\cty.dat |
| rotor_config.json | %APPDATA%\DXpeditionTracker\rotor_config.json |

---

## MD5 ficheros fuente (v25)

```
74570aefffdc0c71552cf93eb741813c  frontend/index_win.html
cee1414cf1da41a22d70557ae647f14a  backend/main_win.py
56f2dc85694a1e823c47a3ea4b75d6ac  backend/persistence_win.py
737d8efcf87173d77c5d5a6e21ffafbc  backend/hrd_parser.py
934733043312f1027987068a86b17810  backend/hf_propagation.py
d6785a06550c8ab28b1dee68d68edd55  backend/dx_calendar.py
aafc520857eae57554505fcf31504150  backend/dx_spots.py
10553d7ed73d3ce51b37a20daed7952c  backend/cty_parser.py
bd5afbf99f9ef6de02c1628570a9c21d  backend/log_readers.py
235867da784bbc6b126ffde518df31ad  rotor_tray_win.py
7cee265ff8ac3fc503a2569d9b444ecd  run_win.py
a5fa4f25adaeb6e0574c4a6817af2359  rotor/gs232a.py
36e8e8117159f6d724c55b9ed7a45dc3  requirements_win.txt
```

Verificar en PowerShell:
```powershell
Get-FileHash "frontend\index_win.html" -Algorithm MD5
Get-FileHash "backend\main_win.py" -Algorithm MD5
Get-FileHash "backend\hf_propagation.py" -Algorithm MD5
Get-FileHash "rotor_tray_win.py" -Algorithm MD5
```
