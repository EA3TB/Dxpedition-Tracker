#!/usr/bin/env python3
"""
log_readers.py — Lectores de log para DX Monitor v1.1
Soporta: HRD XML, Swisslog MDB, Log4OM SQLite, ADIF
"""

import xml.etree.ElementTree as ET
import glob, os, re, json, logging, sqlite3, sys
from collections import defaultdict

log = logging.getLogger("dxmonitor")

# ── Normalización de modo ─────────────────────────────────────────────────────
def normalizar_modo(m):
    if not m: return ""
    m = str(m).upper().strip()
    if re.search(r"FT\s*8", m): return "FT8"
    if re.search(r"FT\s*4", m): return "FT4"
    if "MFSK" in m: return "FT4"
    if "RTTY" in m or "PSK" in m or "BPSK" in m: return "RTTY"
    if m in ("USB","LSB","SSB","AM","FM","PHONE"): return "SSB"
    if m in ("CW","CW-R"): return "CW"
    if re.search(r"^[+-]\d{2}\b", m): return "FT8"
    return m

# ── Normalización de banda ────────────────────────────────────────────────────
def normalizar_banda(b):
    if not b: return ""
    b = str(b).strip().lower()
    if b.isdigit(): b = b + "m"
    return b

# ── Prefijo CTY → número DXCC ─────────────────────────────────────────────────
def pfx_cty_a_dxcc_num(pfx_dxcc_str, pfx_cty_map):
    """
    Swisslog almacena P_DXCC como prefijo de cty.dat (ej: 'EA', 'S9').
    Buscamos ese prefijo en pfx_cty para obtener nombre DXCC,
    y luego buscamos ese nombre en pfx_a_dxcc para obtener el número.
    Como fallback, buscamos coincidencia directa por prefijo en pfx_cty.
    """
    if not pfx_dxcc_str or not pfx_cty_map:
        return None
    pfx = pfx_dxcc_str.strip().upper()
    # Búsqueda directa en cty.dat por prefijo exacto
    if pfx in pfx_cty_map:
        return pfx_cty_map[pfx]
    # Búsqueda por prefijo más largo que coincida
    for n in range(len(pfx), 0, -1):
        if pfx[:n] in pfx_cty_map:
            return pfx_cty_map[pfx[:n]]
    return None


# ══════════════════════════════════════════════════════════════════════════════
# LECTOR HRD XML
# ══════════════════════════════════════════════════════════════════════════════
def leer_hrd_xml(xml_dir, xml_glob, host_path_fn=None):
    """
    Lee QSOs desde el XML más reciente de HRD.
    host_path_fn: función que convierte path del host a path del contenedor (Docker).
    """
    if host_path_fn:
        search_dir = host_path_fn(xml_dir)
    else:
        search_dir = xml_dir

    if not xml_dir:
        log.error("XML directory not configured.")
        return None, None, None, None, None

    ficheros = glob.glob(os.path.join(search_dir, xml_glob))
    if not ficheros:
        log.error("No XML found in %s", os.path.join(search_dir, xml_glob))
        return None, None, None, None, None

    path = max(ficheros, key=os.path.getmtime)
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except ET.ParseError as e:
        log.error("XML parse error: %s", e)
        return None, None, None, None, None

    registros = root.findall("LogbookBackup/Record")
    log.info("XML: %s — %d records", os.path.basename(path), len(registros))

    cn = defaultdict(lambda: defaultdict(set))
    tn = defaultdict(lambda: defaultdict(set))
    sin = 0

    for rec in registros:
        a = rec.attrib
        dxcc  = a.get("COL_DXCC","").strip()
        banda = a.get("COL_BAND","").strip().lower()
        modo  = normalizar_modo(a.get("COL_MODE",""))
        qsl   = (a.get("COL_QSL_RCVD","").upper()=="Y" or
                 a.get("COL_LOTW_QSL_RCVD","").upper() in ("Y","V"))
        if not dxcc or not banda or not modo: sin += 1; continue
        try: dn = int(dxcc)
        except: sin += 1; continue
        tn[dn][banda].add(modo)
        if qsl: cn[dn][banda].add(modo)

    stats = {
        "qsos_total": len(registros),
        "xml_hrd_path": os.path.basename(path),
        "log_source": "hrd_xml",
    }
    log.info("Log loaded: %d DXCC confirmed, %d worked. No DXCC: %d", len(cn), len(tn), sin)
    return cn, tn, stats, registros, path


# ══════════════════════════════════════════════════════════════════════════════
# LECTOR SWISSLOG MDB
# ══════════════════════════════════════════════════════════════════════════════
def leer_swisslog_mdb(mdb_path, pfx_a_dxcc=None):
    """
    Lee QSOs desde una base de datos Swisslog (.mdb).
    pfx_cty_num_map: dict {prefijo_cty: dxcc_num} construido desde cty.dat.
    Usa pyodbc en Windows y mdbtools en Linux/Docker.
    """
    if not mdb_path or not os.path.exists(mdb_path):
        log.error("MDB file not found: %s", mdb_path)
        return None, None, None

    cn = defaultdict(lambda: defaultdict(set))
    tn = defaultdict(lambda: defaultdict(set))

    try:
        if sys.platform == "win32":
            rows, total = _leer_mdb_pyodbc(mdb_path)
        else:
            rows, total = _leer_mdb_mdbtools(mdb_path)

        sin = 0
        for pfx_dxcc, banda, modo, qsl in rows:
            # Resolver prefijo CTY → número DXCC
            if pfx_a_dxcc and pfx_dxcc:
                dxcc_num, _ = pfx_a_dxcc.get(pfx_dxcc.upper(), (None, ""))
            else:
                dxcc_num = None
            if not dxcc_num or not banda or not modo: sin += 1; continue
            tn[dxcc_num][banda].add(modo)
            if qsl: cn[dxcc_num][banda].add(modo)

        stats = {
            "qsos_total": total,
            "xml_hrd_path": os.path.basename(mdb_path),
            "log_source": "swisslog_mdb",
        }
        log.info("Swisslog MDB loaded: %d DXCC confirmed, %d worked. No DXCC: %d",
                 len(cn), len(tn), sin)
        return cn, tn, stats

    except Exception as e:
        log.error("Error reading Swisslog MDB: %s", e)
        return None, None, None


def _leer_mdb_pyodbc(mdb_path):
    """Lee Swisslog MDB en Windows usando DAO via comtypes + GetRows()."""
    try:
        import comtypes.client
    except ImportError:
        raise RuntimeError("Libreria comtypes no instalada. Ejecuta: pip install comtypes")

    dao_engine = None
    for prog_id in ("DAO.DBEngine.120", "DAO.DBEngine.36"):
        try:
            dao_engine = comtypes.client.CreateObject(prog_id)
            break
        except Exception:
            continue

    if dao_engine is None:
        raise RuntimeError(
            "No se encontro motor DAO en este sistema. "
            "Instala Microsoft Access Database Engine 2016: "
            "https://www.microsoft.com/en-us/download/details.aspx?id=54920"
        )

    try:
        db = dao_engine.OpenDatabase(mdb_path, False, True)
    except Exception as e:
        if "locked" in str(e).lower() or "exclusive" in str(e).lower():
            log.warning("MDB locked by Swisslog, retrying in 60s.")
        raise

    # dbOpenSnapshot=4, dbReadOnly=4
    def get_rows(sql):
        """Descarga toda la tabla en una sola llamada COM via GetRows()."""
        rs = db.OpenRecordset(sql, 4, 4)
        if rs.EOF:
            rs.Close()
            return []
        data = rs.GetRows(100000)  # max 100k filas
        rs.Close()
        # GetRows devuelve (col0_vals, col1_vals, ...) — transponer a lista de filas
        return list(zip(*data))

    bands    = {r[0]: normalizar_banda(r[1])
                for r in get_rows("SELECT BANDID, BAND FROM BANDS")}
    modes    = {r[0]: normalizar_modo(r[1])
                for r in get_rows("SELECT MODEID, MODE FROM MODES")}
    dxcc_map = {r[0]: str(r[1]).strip()
                for r in get_rows("SELECT P_QTHID, P_DXCC FROM PQTH")
                if r[0] is not None and r[1] and str(r[1]).strip()}

    rows_raw = get_rows(
        "SELECT L_QTHID,L_BANDID,L_MODEID,L_QSL_RECEIVED,L_LOTW_RECEIVED FROM LOGBOOK"
    )
    db.Close()

    total = len(rows_raw)
    result = []
    for qthid, bandid, modeid, qsl_rcv, lotw_rcv in rows_raw:
        pfx_dxcc = dxcc_map.get(qthid, "")
        banda    = bands.get(bandid, "")
        modo     = modes.get(modeid, "")
        qsl      = (str(qsl_rcv) == "1" or str(lotw_rcv) == "1" or
                    str(qsl_rcv).lower() in ("true","-1") or
                    str(lotw_rcv).lower() in ("true","-1"))
        result.append((pfx_dxcc, banda, modo, qsl))
    log.info("Swisslog MDB read: %d rows in total", total)
    return result, total


def _leer_mdb_mdbtools(mdb_path):
    """Lee Swisslog MDB usando mdbtools (Linux/Docker)."""
    import subprocess, csv, io

    def mdb_export(table):
        r = subprocess.run(
            ["mdb-export", mdb_path, table],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            raise RuntimeError(f"mdb-export {table} failed: {r.stderr}")
        return list(csv.DictReader(io.StringIO(r.stdout)))

    bands_rows = mdb_export("BANDS")
    modes_rows = mdb_export("MODES")
    pqth_rows  = mdb_export("PQTH")
    log_rows   = mdb_export("LOGBOOK")

    bands = {r["BANDID"]: normalizar_banda(r["BAND"]) for r in bands_rows}
    modes = {r["MODEID"]: normalizar_modo(r["MODE"])  for r in modes_rows}
    dxcc_map = {r["P_QTHID"]: r["P_DXCC"].strip()
                for r in pqth_rows if r.get("P_DXCC","").strip()}

    total  = len(log_rows)
    result = []
    for r in log_rows:
        pfx_dxcc = dxcc_map.get(r.get("L_QTHID",""), "")
        banda    = bands.get(r.get("L_BANDID",""), "")
        modo     = modes.get(r.get("L_MODEID",""), "")
        qsl      = (r.get("L_QSL_RECEIVED","0") == "1" or
                    r.get("L_LOTW_RECEIVED","0") == "1")
        result.append((pfx_dxcc, banda, modo, qsl))
    return result, total


# ══════════════════════════════════════════════════════════════════════════════
# LECTOR LOG4OM SQLITE
# ══════════════════════════════════════════════════════════════════════════════
def leer_log4om_sqlite(db_path):
    """
    Lee QSOs desde base de datos SQLite de Log4OM v2.
    Tabla Log — campos: band, mode, dxcc, qsoconfirmations
    """
    if not db_path or not os.path.exists(db_path):
        log.error("SQLite file not found: %s", db_path)
        return None, None, None

    cn = defaultdict(lambda: defaultdict(set))
    tn = defaultdict(lambda: defaultdict(set))

    try:
        # mode=ro permite lectura con Log4OM abierto
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        cur  = conn.cursor()
        cur.execute("SELECT band, mode, dxcc, qsoconfirmations FROM Log")
        rows = cur.fetchall()
        conn.close()

        sin = 0
        for banda, modo, dxcc, confirmations in rows:
            banda = normalizar_banda(banda)
            modo  = normalizar_modo(modo)
            if not dxcc or not banda or not modo: sin += 1; continue
            try: dxcc_num = int(dxcc)
            except: sin += 1; continue

            qsl = False
            if confirmations:
                try:
                    for c in json.loads(confirmations):
                        if c.get("CT") in ("QSL","LOTW") and c.get("R") == "Yes":
                            qsl = True; break
                except: pass

            tn[dxcc_num][banda].add(modo)
            if qsl: cn[dxcc_num][banda].add(modo)

        stats = {
            "qsos_total": len(rows),
            "xml_hrd_path": os.path.basename(db_path),
            "log_source": "log4om_sqlite",
        }
        log.info("Log4OM SQLite loaded: %d DXCC confirmed, %d worked. No DXCC: %d",
                 len(cn), len(tn), sin)
        return cn, tn, stats

    except Exception as e:
        log.error("Error reading Log4OM SQLite: %s", e)
        return None, None, None


# ══════════════════════════════════════════════════════════════════════════════
# LECTOR ADIF
# ══════════════════════════════════════════════════════════════════════════════
def leer_adif(adif_path):
    """
    Lee QSOs desde fichero ADIF (.adi / .adif).
    Compatible con cualquier programa que exporte ADIF estándar.
    """
    if not adif_path or not os.path.exists(adif_path):
        log.error("ADIF file not found: %s", adif_path)
        return None, None, None

    cn = defaultdict(lambda: defaultdict(set))
    tn = defaultdict(lambda: defaultdict(set))

    try:
        with open(adif_path, "r", encoding="utf-8", errors="ignore") as f:
            contenido = f.read()

        # Saltar cabecera ADIF (antes de <EOH>)
        eoh = re.search(r"<eoh>", contenido, re.IGNORECASE)
        if eoh:
            contenido = contenido[eoh.end():]

        registros = re.split(r"<eor>", contenido, flags=re.IGNORECASE)
        total = 0; sin = 0

        for registro in registros:
            if not registro.strip(): continue
            campos = {}
            for match in re.finditer(r"<(\w+)(?::\d+(?::[A-Z])?)?>([^<]*)", registro, re.IGNORECASE):
                campos[match.group(1).upper()] = match.group(2).strip()
            if not campos: continue
            total += 1

            dxcc  = campos.get("DXCC","").strip()
            banda = normalizar_banda(campos.get("BAND",""))
            modo  = normalizar_modo(campos.get("MODE",""))

            if not dxcc: sin += 1; continue
            try: dxcc_num = int(dxcc)
            except: sin += 1; continue
            if not banda or not modo: sin += 1; continue

            qsl_rcvd  = campos.get("QSL_RCVD","").upper()
            lotw_rcvd = campos.get("LOTW_QSL_RCVD","").upper()
            qsl = qsl_rcvd in ("Y","V") or lotw_rcvd in ("Y","V")

            tn[dxcc_num][banda].add(modo)
            if qsl: cn[dxcc_num][banda].add(modo)

        stats = {
            "qsos_total": total,
            "xml_hrd_path": os.path.basename(adif_path),
            "log_source": "adif",
        }
        log.info("ADIF loaded: %d DXCC confirmed, %d worked. No DXCC: %d",
                 len(cn), len(tn), sin)
        return cn, tn, stats

    except Exception as e:
        log.error("Error reading ADIF: %s", e)
        return None, None, None
