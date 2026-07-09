# DXpedition Tracker — Docker v25

Dashboard web para el seguimiento de DXpediciones, integrado con múltiples fuentes de log
(HRD XML, Swisslog MDB, Log4OM SQLite, ADIF), cty.dat, propagación HF, calendario DX,
spots de cluster en vivo y control de rotor de azimut.
Desplegado como contenedor Docker bajo OpenMediaVault (OMV).

---

## Stack

- Backend  : FastAPI + uvicorn (Python 3.12-slim)
- Frontend : HTML/JS puro, SortableJS 1.14.0 para drag & drop
- Puerto   : 8766
- Datos    : /opt/Dxpedition_Dashboard/
- NAS      : /mnt/nas (montado read-only)

---

## Novedades v25 (respecto a v24a)

- **Filtro de bandas**: checkboxes en la UI para HF 6–160m, persistido en config.json
- **Calendario DX**: scraping de NG3K ADXO (`dx_calendar.py`) — crea/elimina automáticamente
  tarjetas de expediciones activas, ordenadas por fecha de fin
- **Spots DX**: datos en vivo del cluster dxwatch.com (`dx_spots.py`), mostrados en popup
  flotante al pulsar el botón "DX" de cada tarjeta
- Toolbar reorganizada en formato compacto multi-fila
- Límite de tarjetas de expedición elevado de 20 a 50
- Módulo unificado `hf_propagation.py` (reemplaza el modelo dual SP/LP inline anterior),
  integrado tanto en Docker como en Windows
- SSN obtenido de HamQSL (`<sunspots>`) junto con SFI y Kp

### Correcciones
- `hrd_parser.py`: bug de mapeo de banda — 160m y 6m se mapeaban incorrectamente a 80m y 10m
- Textos de UI corregidos de español a inglés
- Bug de cálculo LUF: `luf_adj` se calculaba pero nunca se aplicaba
- Fórmula de longitud del punto medio LP incorrecta para valores positivos de `mid_lon_sp`
  (rutas Europa → África oriental); corregida a:
  `mid_lon_lp = mid_lon_sp + 180; if mid_lon_lp > 180: mid_lon_lp -= 360`
- `AbortSignal.timeout()` sustituido por `AbortController + setTimeout` (compatibilidad Firefox)
- Etiquetas de banda en celdas de tarjeta sin sufijo "m" (solo las píldoras de filtro lo llevan)

---

## Estructura de ficheros en el NAS

```
/srv/dev-disk-by-uuid-eded39cd-59b6-4936-92e5-c20d21683fd7/docker/
├── data/
│   └── Dxpedition_Dashboard/
│       ├── app/
│       │   ├── Dockerfile
│       │   ├── requirements.txt
│       │   ├── docker-compose.example.yml
│       │   ├── README.md
│       │   ├── backend/
│       │   │   ├── main.py
│       │   │   ├── hrd_parser.py
│       │   │   ├── log_readers.py
│       │   │   ├── cty_parser.py
│       │   │   ├── persistence.py
│       │   │   ├── hf_propagation.py
│       │   │   ├── dx_calendar.py
│       │   │   ├── dx_spots.py
│       │   │   └── __init__.py
│       │   └── frontend/
│       │       ├── index.html
│       │       └── static/
│       │           └── app.css
│       ├── config.json
│       ├── expeditions.json
│       └── cty.dat
└── compose_files/
    └── DXpedition/
        └── DXpedition.yml
```

---

## Despliegue y actualización

```bash
cd /srv/dev-disk-by-uuid-eded39cd-59b6-4936-92e5-c20d21683fd7/docker/data/Dxpedition_Dashboard/app
tar -xzf dxpedition_tracker_v25.tar.gz --strip-components=1
```

Gestión de Docker Compose exclusivamente vía UI de OMV (rebuild completo,
las carpetas van en `build:` context, no bind-mounted):

```bash
docker build ... && docker stop ... && docker rm ... && docker run ...
```

---

## Fuentes de log soportadas

| Tipo | Extensión | Selección |
|------|-----------|-----------|
| HRD XML | .xml | Carpeta — carga el más reciente automáticamente |
| Swisslog MDB | .mdb | Fichero individual |
| Log4OM SQLite | .sqlite / .db | Fichero individual |
| ADIF | .adi / .adif | Fichero individual |

---

## Propagación HF (`hf_propagation.py`)

- Módulo único compartido por Docker y Windows
- Calcula rutas de camino corto (SP) y largo (LP), LUF/MUF, punto medio geográfico
- Fuentes externas: NOAA (Kp), HamQSL (SFI, SSN)

## Calendario DX (`dx_calendar.py`)

- Scraping de la tabla "Announced DX Operations" de NG3K ADXO
- Crea y elimina automáticamente tarjetas de expediciones activas
- Ordenación por fecha de fin de expedición

## Spots DX (`dx_spots.py`)

- Consulta el cluster dxwatch.com
- Popup flotante por tarjeta, accesible mediante el botón "DX" en la cabecera

---

## Control de rotor ARS-USB

El widget del rotor se conecta a http://localhost:8767 en el PC del usuario.

### Arquitectura

```
NAS (Docker)                    PC Windows
─────────────────               ──────────────────────────
FastAPI :8766                   rotor_tray_win.py (tray)
index.html          ->          Navegador
                                      | localhost:8767
                                   HTTP rotor server
                                      | COM10 (Eltima)
                                   ARS-USB -> Prosistel
```

### Protocolo GS-232A (EA4TX ARS-USB)
- Comando movimiento : M + 3 digitos (ej: M338)
- Comando posicion   : C -> respuesta +XXXX
- Comando stop       : S (Stop all — el comando A es ignorado por el Prosistel)
- Rango              : 0-360 grados, tope en Norte (0/360)
- Direccion          : arco mas corto que no cruce el tope

### Instalacion del tray (usuarios Docker)

Descarga dxpedition_rotor_tray.zip y ejecuta install_tray.bat como administrador.
- Primera ejecucion: solicita el puerto COM
- Siguientes ejecuciones: usa la configuracion guardada automaticamente
- Para cambiar el puerto: menu del tray -> Configuracion

Iconos: verde=online / amarillo=conectando / rojo=offline
Arranque: automatico con Windows (shell:startup), sin ventana de consola

---

## MD5 ficheros (v25)

```
80a871a59f76d9ed79ea3fbe5098a097  frontend/index.html
30d9f409b0c6782c112877af58dc6d64  backend/main.py
d34a7aea636e7dd7f6fe4e16bf6829ba  backend/persistence.py
737d8efcf87173d77c5d5a6e21ffafbc  backend/hrd_parser.py
934733043312f1027987068a86b17810  backend/hf_propagation.py
d6785a06550c8ab28b1dee68d68edd55  backend/dx_calendar.py
aafc520857eae57554505fcf31504150  backend/dx_spots.py
10553d7ed73d3ce51b37a20daed7952c  backend/cty_parser.py
bd5afbf99f9ef6de02c1628570a9c21d  backend/log_readers.py
005db23aaf5f61f9ef70cba81b33276f  Dockerfile
61ba8fb18463b43de3cb6260528d3d34  requirements.txt
```

Verificar:
```bash
md5sum frontend/index.html backend/main.py backend/persistence.py backend/hf_propagation.py backend/dx_calendar.py backend/dx_spots.py Dockerfile
```
