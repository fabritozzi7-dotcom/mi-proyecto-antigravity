"""
dux_export.py — Generador de Excel ENC/DET para importación en Dux.

Toma rendiciones de RENDICIONES_LOG (Google Sheets) y produce un .xlsx
con el formato de 28 columnas (A-AB) que exige el sistema contable Dux.

Uso standalone:  python dux_export.py
Uso importado:   from dux_export import exportar_dux_desde_sheets
"""

import logging
from datetime import datetime
from collections import OrderedDict

logger = logging.getLogger(__name__)

# ==========================================
# CONSTANTES Y MAPEOS
# ==========================================

# Código AFIP → Tipo FP en Dux
AFIP_A_TIPO_FP = {
    "001": "F", "006": "F", "011": "F", "051": "F",
    "002": "ND", "007": "ND", "012": "ND", "052": "ND",
    "003": "NC", "008": "NC", "013": "NC", "053": "NC",
}

# Código jurisdicción interno → nombre Dux
JURISDICCION_A_DUX = {
    "CF": "CABA",
    "BA": "BS AS",
    "OB": "CORDOBA",
    "SF": "SANTA FE",
    "MZ": "MENDOZA",
}

# Headers de RENDICIONES_LOG → clave interna (por posición 0-30)
# Maps RENDICIONES_LOG columns by position (0-indexed) to internal keys.
# Must match the REAL production header order exactly.
# Used by fila_a_dict() for positional row parsing.
HEADER_MAP = [
    "id_operacion",        # 0  (A)  ID Operación
    "fecha",               # 1  (B)  Fecha
    "usuario",             # 2  (C)  Usuario
    "oficina",             # 3  (D)  Oficina
    "numero_carpeta",      # 4  (E)  Número de Carpeta (Obligatorio)
    "tipo_operacion",      # 5  (F)  Tipo de Operación
    "cliente",             # 6  (G)  Cliente
    "concepto",            # 7  (H)  Concepto
    "monto_concepto",      # 8  (I)  Monto Concepto
    "factura_tipo",        # 9  (J)  factura_tipo
    "codigo_afip",         # 10 (K)  Código AFIP
    "sucursal",            # 11 (L)  Sucursal
    "numero_factura",      # 12 (M)  Número_de_factura
    "n_comprobante",       # 13 (N)  N°Comprobante
    "proveedor_validado",  # 14 (O)  Proveedor_Validado
    "cuit_proveedor",      # 15 (P)  Cuit_Proveedor_AI
    "neto_gravado",        # 16 (Q)  Gravado
    "no_gravado",          # 17 (R)  No_Gravado
    "iva_21",              # 18 (S)  IVA_21 (VALOR IVA SOBRE VALOR GRAVADO)
    "iva_105",             # 19 (T)  IVA_10_5
    "iva_27",              # 20 (U)  IVA_27
    "perc_iva",            # 21 (V)  Percepción_IVA
    "perc_ganancias",      # 22 (W)  Percepción_Ganancia
    "perc_iibb",           # 23 (X)  Percepción IIBB
    "jurisdiccion",        # 24 (Y)  Provincia/Jurisdicción
    "monto_total_ticket",  # 25 (Z)  Monto Ticket
    "monto_a_imputar",     # 26 (AA) Monto a Imputar
    "ticket_url",          # 27 (AB) Ticket URL
    "estado",              # 28 (AC) Estado Saldos
    "clave_maestra",       # 29 (AD) Clave Maestra
    "observaciones",       # 30 (AE) Observaciones
    "aviso_mail",          # 31 (AF) Aviso_Mail
    "cuit_cliente",        # 32 (AG) Cuit_Cliente
    "motivo_rechazo",      # 33 (AH) Motivo_Rechazo
    "revisado_por",        # 34 (AI) Revisado_Por
    "perc_iibb_2",         # 35 (AJ) Perc_IIBB_2
    "jurisdiccion_iibb_2", # 36 (AK) Jurisdiccion_IIBB_2
    "perc_municipal",      # 37 (AL) Perc_Municipal
    "jurisdiccion_municipal", # 38 (AM) Jurisdiccion_Municipal
    "fecha_revision",      # 39 (AN) Fecha_Revision
]

DUX_HEADERS = [
    "Tipo Renglón",    # A
    "Tipo Factura",    # B
    "Fecha",           # C
    "Tipo FP",         # D
    "Letra FP",        # E
    "Suc. FP",         # F
    "Nro. FP",         # G
    "Concepto",        # H
    "CUIT",            # I
    "Op.",             # J
    "Mon.",            # K
    "Importe",         # L
    "Detalle",         # M
    "idEmpleado",      # N
    "netoGravado",     # O
    "noGravado",       # P
    "exento",          # Q
    "iva21",           # R
    "iva10",           # S
    "iva27",           # T
    "iibb importe 1",  # U
    "iibb provincia 1",# V
    "iibb importe 2",  # W
    "iibb provincia 2",# X
    "iibb importe 3",  # Y
    "iibb provincia 3",# Z
    "percepcion iva",  # AA
    "percepcion ganancias",  # AB
]


# ==========================================
# HELPERS
# ==========================================

def safe_float(value, default=0.0):
    """Convierte a float de forma segura, limpiando $ y comas."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def safe_int(value, default=0):
    """Convierte a int, removiendo ceros a la izquierda."""
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip().lstrip("0") or "0")
    except (ValueError, TypeError):
        return default


def resolver_tipo_fp(codigo_afip):
    """Código AFIP → Tipo FP (F, ND, NC). Default F."""
    code = str(codigo_afip or "").strip().zfill(3)
    return AFIP_A_TIPO_FP.get(code, "F")


def resolver_id_tesoreria(nombre_usuario, codigo_empleado_fn=None):
    """Nombre de usuario → ID cuenta tesorería Dux.

    Args:
        nombre_usuario: user name from RENDICIONES_LOG.
        codigo_empleado_fn: callable(nombre) -> int|None, dynamic lookup.
            If None, returns "" (no mapping available).
    """
    if codigo_empleado_fn is None:
        return ""
    code = codigo_empleado_fn(nombre_usuario)
    return str(code) if code is not None else ""


def resolver_jurisdiccion_dux(codigo_interno):
    """Código interno (CF, OB, etc.) → nombre Dux (CABA, CORDOBA, etc.)."""
    code = str(codigo_interno or "").strip().upper()
    return JURISDICCION_A_DUX.get(code, code)


def es_propia(cuit_cliente, cuits_propios=None):
    """Determina si un comprobante es PROPIA.

    PROPIA when the client (receiver) CUIT matches any of Expoconsult's CUITs.
    Does NOT depend on factura_tipo — only on who the invoice is addressed to.

    Args:
        cuit_cliente: CUIT of the client/receiver from the invoice.
        cuits_propios: list of Expoconsult CUITs (from CONFIG_EMPRESA).
    """
    if not cuit_cliente or not cuits_propios:
        return False
    clean = str(cuit_cliente).replace("-", "").replace(" ", "").strip()
    if not clean:
        return False
    return clean in cuits_propios


def fila_a_dict(row):
    """Convierte una fila (lista) de RENDICIONES_LOG a dict con claves internas."""
    d = {}
    for i, key in enumerate(HEADER_MAP):
        d[key] = row[i] if i < len(row) else ""
    return d


# ==========================================
# LECTURA DESDE GOOGLE SHEETS
# ==========================================

def leer_rendiciones_desde_sheet(client, sheet_id, sheet_name):
    """
    Lee RENDICIONES_LOG y devuelve lista de dicts con claves internas.

    Args:
        client: gspread client autorizado.
        sheet_id: ID del spreadsheet (o None para abrir por nombre).
        sheet_name: Nombre del spreadsheet (fallback si no hay ID).

    Returns:
        Lista de dicts, uno por fila (sin header).
    """
    if sheet_id:
        sh = client.open_by_key(sheet_id)
    else:
        sh = client.open(sheet_name)

    ws = sh.worksheet("RENDICIONES_LOG")
    all_rows = ws.get_all_values(value_render_option="UNFORMATTED_VALUE")

    if len(all_rows) < 2:
        return []

    # Saltar header (fila 0)
    return [fila_a_dict(row) for row in all_rows[1:]]


# ==========================================
# AGRUPACIÓN POR COMPROBANTE
# ==========================================

def _clave_comprobante(rend):
    """Genera clave de agrupación: CUIT + tipo + sucursal + número."""
    cuit = str(rend.get("cuit_proveedor", "")).replace("-", "").strip()
    tipo = str(rend.get("factura_tipo", "")).strip().upper()
    suc = str(rend.get("sucursal", "")).strip()
    num = str(rend.get("numero_factura", "")).strip()
    return f"{cuit}|{tipo}|{suc}|{num}"


def agrupar_por_comprobante(rendiciones):
    """
    Agrupa rendiciones por clave de comprobante.

    Args:
        rendiciones: lista de dicts (salida de leer_rendiciones_desde_sheet
                     o de fila_a_dict).

    Returns:
        OrderedDict {clave: [lista de dicts del grupo]}, preservando orden
        de aparición.
    """
    grupos = OrderedDict()
    for rend in rendiciones:
        clave = _clave_comprobante(rend)
        if not clave or clave == "|||":
            continue
        grupos.setdefault(clave, []).append(rend)
    return grupos


# ==========================================
# GENERACIÓN DE FILAS DUX
# ==========================================

def _construir_enc(grupo, tipo_factura_dux, total_importe, desglose_sumado):
    """Construye la fila ENC a partir de un grupo de rendiciones."""
    primer = grupo[0]
    fecha_raw = str(primer.get("fecha", "")).strip()

    # Normalizar fecha a DD/MM/AAAA
    fecha = fecha_raw
    if "-" in fecha_raw:
        try:
            dt = datetime.strptime(fecha_raw[:10], "%Y-%m-%d")
            fecha = dt.strftime("%d/%m/%Y")
        except ValueError:
            pass

    tipo_fp = resolver_tipo_fp(primer.get("codigo_afip"))
    letra = str(primer.get("factura_tipo", "C")).strip().upper()
    if letra not in ("A", "B", "C", "M"):
        letra = "C"

    suc = safe_int(primer.get("sucursal"))
    nro = safe_int(primer.get("numero_factura"))
    cuit_proveedor = str(primer.get("cuit_proveedor", "")).replace("-", "").replace(" ", "").strip()

    is_propia = tipo_factura_dux == "PROPIA"

    fila = [""] * len(DUX_HEADERS)
    fila[0] = "ENC"                              # A: Tipo Renglón
    fila[1] = tipo_factura_dux                    # B: TERCEROS / PROPIA
    fila[2] = fecha                               # C: Fecha
    fila[3] = tipo_fp                             # D: Tipo FP
    fila[4] = letra                               # E: Letra FP
    fila[5] = suc                                 # F: Suc. FP
    fila[6] = nro                                 # G: Nro. FP
    # H: Concepto — vacío en ENC
    fila[8] = cuit_proveedor                      # I: CUIT — ALWAYS (obligatorio en ENC)
    # J: Op. — vacío en ENC
    fila[10] = "PES"                              # K: Mon.
    fila[11] = round(total_importe, 2)            # L: Importe

    # M: Detalle — "CONSUMIDOR FINAL" si TERCEROS sin CUIT
    if not is_propia and not cuit_proveedor:
        fila[12] = "CONSUMIDOR FINAL"

    # O-AB: Desglose fiscal (solo PROPIA)
    if is_propia:
        fila[14] = round(desglose_sumado["neto_gravado"], 2)       # O
        fila[15] = round(desglose_sumado["no_gravado"], 2)         # P
        fila[16] = round(desglose_sumado["exento"], 2)             # Q
        fila[17] = round(desglose_sumado["iva_21"], 2)             # R
        fila[18] = round(desglose_sumado["iva_105"], 2)            # S
        fila[19] = round(desglose_sumado["iva_27"], 2)             # T

        # IIBB: hasta 3 jurisdicciones con importe
        iibb_list = desglose_sumado.get("iibb", [])
        for slot_idx, iibb_entry in enumerate(iibb_list[:3]):
            base_col = 20 + slot_idx * 2  # U/W/Y = 20/22/24
            fila[base_col] = round(iibb_entry["importe"], 2)
            fila[base_col + 1] = iibb_entry["provincia"]

        fila[26] = round(desglose_sumado["perc_iva"], 2)          # AA
        fila[27] = round(desglose_sumado["perc_ganancias"], 2)    # AB

    return fila


def _construir_det(rend, tipo_factura_dux, codigo_concepto_fn=None, codigo_empleado_fn=None):
    """Construye una fila DET a partir de una rendición individual.

    Args:
        rend: dict with internal keys.
        tipo_factura_dux: "PROPIA" or "TERCEROS" (from group-level decision).
        codigo_concepto_fn: callable(concepto) -> int|None.
        codigo_empleado_fn: callable(usuario) -> int|None.
    """
    fecha_raw = str(rend.get("fecha", "")).strip()
    fecha = fecha_raw
    if "-" in fecha_raw:
        try:
            dt = datetime.strptime(fecha_raw[:10], "%Y-%m-%d")
            fecha = dt.strftime("%d/%m/%Y")
        except ValueError:
            pass

    tipo_fp = resolver_tipo_fp(rend.get("codigo_afip"))
    letra = str(rend.get("factura_tipo", "C")).strip().upper()
    if letra not in ("A", "B", "C", "M"):
        letra = "C"

    suc = safe_int(rend.get("sucursal"))
    nro = safe_int(rend.get("numero_factura"))

    importe = safe_float(rend.get("monto_a_imputar"))
    concepto_interno = str(rend.get("concepto", "")).strip()
    carpeta = str(rend.get("numero_carpeta", "")).strip()
    usuario = str(rend.get("usuario", "")).strip()

    # DUX code lookups
    codigo_concepto = ""
    if codigo_concepto_fn:
        code = codigo_concepto_fn(concepto_interno)
        if code is not None:
            codigo_concepto = code  # int

    id_tesoreria = resolver_id_tesoreria(usuario, codigo_empleado_fn)

    fila = [""] * len(DUX_HEADERS)
    fila[0] = "DET"                               # A
    fila[1] = tipo_factura_dux                     # B
    fila[2] = fecha                                # C
    fila[3] = tipo_fp                              # D
    fila[4] = letra                                # E
    fila[5] = suc                                  # F
    fila[6] = nro                                  # G
    fila[7] = codigo_concepto                      # H: DUX concept code (numeric)
    # I: CUIT — vacío en DET
    fila[9] = carpeta                              # J: Op. (número de carpeta)
    # K: Mon. — vacío en DET
    fila[11] = round(importe, 2)                   # L
    # M: Detalle
    fila[12] = f"Rendición {usuario} - {concepto_interno}" if concepto_interno else ""
    fila[13] = id_tesoreria                        # N: idEmpleado (DUX code)
    # O-AB: vacío en DET

    return fila


def _sumar_desglose(grupo):
    """Suma los desgloses fiscales prorrateados de un grupo de rendiciones.

    IMPORTANT: This function expects dicts already mapped to INTERNAL keys
    via SHEET_KEY_MAP (e.g. "neto_gravado", "perc_iibb", "jurisdiccion").
    If it receives dicts with raw sheet headers, it will silently return 0s.
    The mapping chain is: get_all_records() → SHEET_KEY_MAP → this function.
    """
    totales = {
        "neto_gravado": 0.0,
        "no_gravado": 0.0,
        "exento": 0.0,
        "iva_21": 0.0,
        "iva_105": 0.0,
        "iva_27": 0.0,
        "perc_iva": 0.0,
        "perc_ganancias": 0.0,
    }

    # Acumular IIBB por jurisdicción
    iibb_por_juris = {}

    for rend in grupo:
        totales["neto_gravado"] += safe_float(rend.get("neto_gravado"))
        totales["no_gravado"] += safe_float(rend.get("no_gravado"))
        totales["iva_21"] += safe_float(rend.get("iva_21"))
        totales["iva_105"] += safe_float(rend.get("iva_105"))
        totales["iva_27"] += safe_float(rend.get("iva_27"))
        totales["perc_iva"] += safe_float(rend.get("perc_iva"))
        totales["perc_ganancias"] += safe_float(rend.get("perc_ganancias"))

        perc_iibb = safe_float(rend.get("perc_iibb"))
        juris_code = str(rend.get("jurisdiccion", "")).strip().upper()
        if perc_iibb > 0 and juris_code:
            provincia_dux = resolver_jurisdiccion_dux(juris_code)
            iibb_por_juris[provincia_dux] = iibb_por_juris.get(provincia_dux, 0.0) + perc_iibb

    totales["iibb"] = [
        {"provincia": prov, "importe": imp}
        for prov, imp in iibb_por_juris.items()
    ]

    return totales


def generar_filas_dux(grupos, cuits_propios=None, codigo_concepto_fn=None,
                      codigo_empleado_fn=None):
    """
    Genera la lista completa de filas ENC/DET para el Excel Dux.

    Args:
        grupos: OrderedDict de {clave: [rendiciones]}, salida de
                agrupar_por_comprobante.
        cuits_propios: list of Expoconsult CUITs for PROPIA detection.
        codigo_concepto_fn: callable(concepto) -> int|None.
        codigo_empleado_fn: callable(usuario) -> int|None.

    Returns:
        Lista de listas (cada sublista = 28 celdas, una fila del Excel).

    Raises:
        ValueError: if sum(DET) differs from ENC by more than $1 for any group.
    """
    filas = []

    for clave, grupo in grupos.items():
        first = grupo[0]
        cuit_cliente = str(first.get("cuit_cliente", "")).replace("-", "").replace(" ", "").strip()
        is_propia_flag = es_propia(cuit_cliente, cuits_propios)
        tipo_factura_dux = "PROPIA" if is_propia_flag else "TERCEROS"

        # Total ENC = suma de montos a imputar de todas las filas del grupo
        total_importe = sum(safe_float(r.get("monto_a_imputar")) for r in grupo)

        # Desglose sumado (solo relevante para PROPIA, pero lo calculamos siempre)
        desglose_sumado = _sumar_desglose(grupo)

        # Fila ENC
        enc = _construir_enc(grupo, tipo_factura_dux, total_importe, desglose_sumado)
        filas.append(enc)

        # Filas DET (una por rendición/carpeta)
        det_importes = []
        for rend in grupo:
            det = _construir_det(rend, tipo_factura_dux, codigo_concepto_fn, codigo_empleado_fn)
            filas.append(det)
            det_importes.append(safe_float(det[11]))

        # Validate sum(DET) == ENC
        sum_det = sum(det_importes)
        diff = abs(total_importe - sum_det)
        if diff > 1.0:
            cuit_prov = str(first.get("cuit_proveedor", "")).strip()
            raise ValueError(
                f"Sum(DET)={sum_det:.2f} != ENC={total_importe:.2f} "
                f"(diff=${diff:.2f}) for {cuit_prov} — aborting"
            )
        elif diff > 0.005:
            # Adjust last DET for rounding
            last_det_idx = len(filas) - 1
            filas[last_det_idx][11] = round(filas[last_det_idx][11] + (total_importe - sum_det), 2)

    return filas


# ==========================================
# VALIDACIÓN PRE-EXPORT
# ==========================================


def validar_rendiciones_para_export(rendiciones, codigo_concepto_fn=None,
                                    codigo_empleado_fn=None, cuits_propios=None):
    """Validates renditions before DUX export.

    Returns:
        list[dict]: errors. Each dict has keys: tipo, mensaje, filas_afectadas.
        Empty list means validation passed.
    """
    errors = []

    # Group errors by type
    sin_cuit = []
    sin_concepto_dux = {}  # concepto -> [row indices/ids]
    sin_empleado_dux = {}  # usuario -> [row indices/ids]
    propia_sin_desglose = []
    sin_carpeta = []

    for i, rend in enumerate(rendiciones):
        row_ref = rend.get("id_operacion", f"fila {i+1}")

        # 1. CUIT proveedor (11 digits)
        cuit = str(rend.get("cuit_proveedor", "")).replace("-", "").replace(" ", "").strip()
        if not cuit or len(cuit) != 11 or not cuit.isdigit():
            sin_cuit.append(f"row {row_ref}: CUIT='{cuit}'")

        # 2. Concepto → codigo DUX
        concepto = str(rend.get("concepto", "")).strip()
        if concepto and codigo_concepto_fn:
            code = codigo_concepto_fn(concepto)
            if code is None:
                sin_concepto_dux.setdefault(concepto, []).append(row_ref)

        # 3. Usuario → idEmpleado DUX
        usuario = str(rend.get("usuario", "")).strip()
        if usuario and codigo_empleado_fn:
            code = codigo_empleado_fn(usuario)
            if code is None:
                sin_empleado_dux.setdefault(usuario, []).append(row_ref)

        # 4. PROPIA sin desglose
        cuit_cliente = str(rend.get("cuit_cliente", "")).replace("-", "").replace(" ", "").strip()
        if es_propia(cuit_cliente, cuits_propios):
            neto = safe_float(rend.get("neto_gravado"))
            no_grav = safe_float(rend.get("no_gravado"))
            if neto == 0 and no_grav == 0:
                propia_sin_desglose.append(f"row {row_ref}")

        # 5. Carpeta
        carpeta = str(rend.get("numero_carpeta", "")).strip()
        if not carpeta:
            sin_carpeta.append(f"row {row_ref}")

    # Build error list
    if sin_cuit:
        errors.append({
            "tipo": "CUIT proveedor inválido",
            "mensaje": f"{len(sin_cuit)} rendiciones con CUIT vacío o inválido (debe ser 11 dígitos)",
            "filas_afectadas": sin_cuit,
            "accion": "Editá la rendición y completá el CUIT manualmente",
        })

    if sin_concepto_dux:
        total = sum(len(v) for v in sin_concepto_dux.values())
        detalles = []
        for conc, rows in sin_concepto_dux.items():
            detalles.append(f'  "{conc}" (rows: {", ".join(str(r) for r in rows[:5])})')
        errors.append({
            "tipo": "Concepto sin código DUX",
            "mensaje": f"{total} rendiciones con concepto sin mapear",
            "filas_afectadas": detalles,
            "accion": "Completá la columna 'concepto_interno' en MAESTRO_CONCEPTOS_DUX",
        })

    if sin_empleado_dux:
        total = sum(len(v) for v in sin_empleado_dux.values())
        detalles = []
        for usr, rows in sin_empleado_dux.items():
            detalles.append(f'  "{usr}" (rows: {", ".join(str(r) for r in rows[:5])})')
        errors.append({
            "tipo": "Usuario sin idEmpleado DUX",
            "mensaje": f"{total} rendiciones con usuario sin código de tesorería",
            "filas_afectadas": detalles,
            "accion": "Completá 'codigo_dux' en la hoja USUARIOS",
        })

    if propia_sin_desglose:
        errors.append({
            "tipo": "Factura PROPIA sin desglose impositivo",
            "mensaje": f"{len(propia_sin_desglose)} facturas PROPIA sin netoGravado ni noGravado",
            "filas_afectadas": propia_sin_desglose,
            "accion": "Editá la rendición y completá los importes de desglose",
        })

    if sin_carpeta:
        errors.append({
            "tipo": "Rendición sin número de carpeta",
            "mensaje": f"{len(sin_carpeta)} rendiciones sin carpeta (col J del DET quedará vacía)",
            "filas_afectadas": sin_carpeta,
            "accion": "Editá la rendición y completá el número de carpeta",
        })

    # WARNING (not error): IIBB_2 or Perc_Municipal present but not exported
    con_perc_extra = []
    for i, rend in enumerate(rendiciones):
        row_ref = rend.get("id_operacion", f"fila {i+1}")
        iibb2 = safe_float(rend.get("perc_iibb_2"))
        muni = safe_float(rend.get("perc_municipal"))
        if iibb2 > 0 or muni > 0:
            parts = []
            if iibb2 > 0:
                parts.append(f"IIBB_2=${iibb2:,.2f}")
            if muni > 0:
                parts.append(f"Municipal=${muni:,.2f}")
            con_perc_extra.append(f"row {row_ref}: {', '.join(parts)}")

    if con_perc_extra:
        errors.append({
            "tipo": "Percepciones no incluidas en export DUX (WARNING)",
            "mensaje": (
                f"{len(con_perc_extra)} rendiciones tienen percepciones IIBB de 2 jurisdicciones "
                f"y/o Percepción Municipal. El export DUX actual NO incluye estos datos en el archivo."
            ),
            "filas_afectadas": con_perc_extra,
            "accion": "Considerá agregarlos manualmente en DUX o esperá la próxima actualización del export.",
        })

    return errors


# ==========================================
# EXPORTACIÓN A EXCEL
# ==========================================

def exportar_excel_dux(filas, output_path):
    """
    Escribe las filas ENC/DET en un Excel formateado para Dux.

    Args:
        filas: lista de listas (salida de generar_filas_dux).
        output_path: ruta del archivo .xlsx de salida.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Dux Import"

    # — Estilos —
    header_font = Font(name="Calibri", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    enc_fill = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")
    det_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    thin_border = Border(
        left=Side(style="thin", color="B4C6E7"),
        right=Side(style="thin", color="B4C6E7"),
        top=Side(style="thin", color="B4C6E7"),
        bottom=Side(style="thin", color="B4C6E7"),
    )

    number_fmt = '#,##0.00'

    # — Header (fila 1) —
    for col_idx, header in enumerate(DUX_HEADERS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # — Datos (fila 2+) —
    for row_idx, fila in enumerate(filas, start=2):
        is_enc = (fila[0] == "ENC") if fila else False
        row_fill = enc_fill if is_enc else det_fill

        for col_idx, valor in enumerate(fila, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=valor)
            cell.border = thin_border
            cell.fill = row_fill

            # Formato numérico para columnas de importes
            if isinstance(valor, (int, float)) and valor != 0:
                cell.number_format = number_fmt
                cell.alignment = Alignment(horizontal="right")
            elif col_idx == 1:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="center")

    # — Anchos de columna —
    col_widths = {
        1: 14,   # Tipo Renglón
        2: 14,   # Tipo Factura
        3: 12,   # Fecha
        4: 8,    # Tipo FP
        5: 8,    # Letra FP
        6: 8,    # Suc.
        7: 12,   # Nro.
        8: 30,   # Concepto
        9: 14,   # CUIT
        10: 18,  # Op. (carpeta)
        11: 6,   # Mon.
        12: 14,  # Importe
        13: 20,  # Detalle
        14: 12,  # idEmpleado
    }
    for col_idx in range(15, 29):
        col_widths[col_idx] = 14

    for col_idx, width in col_widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # — Freeze panes (fijar header) —
    ws.freeze_panes = "A2"

    # — Autofiltro —
    last_col_letter = get_column_letter(len(DUX_HEADERS))
    last_row = len(filas) + 1
    ws.auto_filter.ref = f"A1:{last_col_letter}{last_row}"

    wb.save(output_path)
    logger.info(f"Excel Dux exportado: {output_path} ({len(filas)} filas)")


# ==========================================
# PIPELINE COMPLETO
# ==========================================

def exportar_dux_desde_sheets(rendiciones_raw, output_path, cuits_propios=None,
                              codigo_concepto_fn=None, codigo_empleado_fn=None):
    """
    Pipeline completo: recibe lista de dicts (rendiciones), agrupa,
    genera filas ENC/DET y escribe el Excel.

    Args:
        rendiciones_raw: lista de dicts con claves internas (HEADER_MAP).
        output_path: ruta del .xlsx de salida.
        cuits_propios: list of Expoconsult CUITs.
        codigo_concepto_fn: callable(concepto) -> int|None.
        codigo_empleado_fn: callable(usuario) -> int|None.

    Returns:
        int — cantidad de filas generadas (sin contar header).
    """
    grupos = agrupar_por_comprobante(rendiciones_raw)
    filas = generar_filas_dux(grupos, cuits_propios, codigo_concepto_fn, codigo_empleado_fn)
    exportar_excel_dux(filas, output_path)
    return len(filas)


# ==========================================
# BLOQUE DE PRUEBA STANDALONE
# ==========================================

if __name__ == "__main__":
    print("=== Test standalone dux_export (refactored) ===\n")

    CUITS_PROPIOS = ["30570717630"]

    # Mock lookup functions
    MOCK_CONCEPTOS = {"FLETE TERRESTRE": 911, "HONORARIOS DESPACHANTE": 5102}
    MOCK_EMPLEADOS = {"DAVID REQUELME": 319, "FABRICIO DAURIA": 361}

    def mock_concepto_fn(c):
        return MOCK_CONCEPTOS.get(str(c).strip().upper())

    def mock_empleado_fn(u):
        return MOCK_EMPLEADOS.get(str(u).strip().upper())

    # Caso 1: TERCEROS con 2 carpetas — cuit_cliente vacío (B invoice)
    terceros_base = {
        "id_operacion": "20260215120000", "fecha": "2026-02-15",
        "usuario": "DAVID REQUELME", "oficina": "BUENOS AIRES",
        "tipo_operacion": "Importación", "cliente": "Importadora X",
        "concepto": "Flete terrestre", "monto_concepto": "50000",
        "factura_tipo": "B", "codigo_afip": "006",
        "sucursal": "00010", "numero_factura": "00045678",
        "n_comprobante": "0001000045678", "proveedor_validado": "Sí",
        "cuit_proveedor": "30712345678", "cuit_cliente": "",
        "neto_gravado": 0, "no_gravado": 50000.0,
        "iva_21": 0, "iva_105": 0, "iva_27": 0,
        "perc_iva": 0, "perc_ganancias": 0, "perc_iibb": 0,
        "jurisdiccion": "", "monto_total_ticket": 50000.0,
        "monto_a_imputar": 25000.0, "ticket_url": "", "estado": "CERRADO",
        "clave_maestra": "30712345678B0001000045678", "observaciones": "",
    }
    terceros_1 = {**terceros_base, "numero_carpeta": "IMP-2026-001"}
    terceros_2 = {**terceros_base, "numero_carpeta": "IMP-2026-002"}

    # Caso 2: PROPIA con 3 carpetas — cuit_cliente = Expoconsult
    propia_base = {
        "id_operacion": "20260215130000", "fecha": "2026-02-15",
        "usuario": "FABRICIO DAURIA", "oficina": "BUENOS AIRES",
        "tipo_operacion": "Exportación", "cliente": "Exportadora Y",
        "concepto": "Honorarios despachante", "monto_concepto": "120000",
        "factura_tipo": "A", "codigo_afip": "001",
        "sucursal": "00003", "numero_factura": "00099001",
        "n_comprobante": "0000300099001", "proveedor_validado": "Sí",
        "cuit_proveedor": "20345678901", "cuit_cliente": "30570717630",
        "neto_gravado": 33057.85, "no_gravado": 0,
        "iva_21": 6942.15, "iva_105": 0, "iva_27": 0,
        "perc_iva": 0, "perc_ganancias": 0, "perc_iibb": 1320.23,
        "jurisdiccion": "CF", "monto_total_ticket": 41320.23,
        "monto_a_imputar": 13773.41, "ticket_url": "", "estado": "CERRADO",
        "clave_maestra": "20345678901A0000300099001", "observaciones": "",
    }
    propia_1 = {**propia_base, "numero_carpeta": "EXP-2026-010"}
    propia_2 = {**propia_base, "numero_carpeta": "EXP-2026-011"}
    propia_3 = {**propia_base, "numero_carpeta": "EXP-2026-012",
                "monto_a_imputar": 13773.41}

    # Caso 3: Factura A con cuit_cliente != Expoconsult → TERCEROS
    otra_base = {**propia_base,
        "id_operacion": "20260215140000",
        "cuit_proveedor": "20999888777",
        "cuit_cliente": "20111222333",  # NOT Expoconsult
        "monto_a_imputar": 41320.23,
        "clave_maestra": "20999888777A0000300099002",
        "sucursal": "00003", "numero_factura": "00099002",
    }
    otra_1 = {**otra_base, "numero_carpeta": "EXP-2026-020"}

    rendiciones = [terceros_1, terceros_2, propia_1, propia_2, propia_3, otra_1]

    # Test generation
    grupos = agrupar_por_comprobante(rendiciones)
    filas = generar_filas_dux(grupos, CUITS_PROPIOS, mock_concepto_fn, mock_empleado_fn)

    print(f"Generadas {len(filas)} filas")
    print(f"  - Esperado: 3 ENC + 6 DET = 9 filas\n")

    for fila in filas:
        tipo_r = fila[0]
        tipo_f = fila[1]
        cuit_i = fila[8]
        concepto_h = fila[7]
        empleado_n = fila[13]
        importe = fila[11]
        carpeta = fila[9]
        print(f"  {tipo_r:3s} | {tipo_f:8s} | CUIT={str(cuit_i):13s} | H={str(concepto_h):6s} | N={str(empleado_n):4s} | L=${importe:>10} | J={carpeta}")

    # Assertions
    enc_rows = [f for f in filas if f[0] == "ENC"]
    det_rows = [f for f in filas if f[0] == "DET"]
    assert len(enc_rows) == 3, f"Expected 3 ENC, got {len(enc_rows)}"
    assert len(det_rows) == 6, f"Expected 6 DET, got {len(det_rows)}"

    # ENC always has CUIT (col I)
    for enc in enc_rows:
        assert enc[8] != "", f"ENC missing CUIT: {enc}"

    # TERCEROS: cuit_cliente empty → should be TERCEROS
    assert enc_rows[0][1] == "TERCEROS", f"Expected TERCEROS, got {enc_rows[0][1]}"

    # PROPIA: cuit_cliente = 30570717630 → should be PROPIA
    assert enc_rows[1][1] == "PROPIA", f"Expected PROPIA, got {enc_rows[1][1]}"

    # Factura A with different cuit_cliente → TERCEROS (key test!)
    assert enc_rows[2][1] == "TERCEROS", f"Expected TERCEROS for non-Expoconsult client, got {enc_rows[2][1]}"

    # DET has concepto code (not text)
    assert det_rows[0][7] == 911, f"Expected 911, got {det_rows[0][7]}"
    assert det_rows[2][7] == 5102, f"Expected 5102, got {det_rows[2][7]}"

    # DET has empleado code (not name)
    assert det_rows[0][13] == "319", f"Expected 319, got {det_rows[0][13]}"
    assert det_rows[2][13] == "361", f"Expected 361, got {det_rows[2][13]}"

    # DET has carpeta in col J
    assert det_rows[0][9] == "IMP-2026-001"

    # Validation test
    print("\n=== Validation test ===")
    errors = validar_rendiciones_para_export(
        rendiciones, mock_concepto_fn, mock_empleado_fn, CUITS_PROPIOS
    )
    print(f"  Errors: {len(errors)}")
    for e in errors:
        print(f"  - {e['tipo']}: {e['mensaje']}")

    print("\nALL ASSERTIONS PASSED")
