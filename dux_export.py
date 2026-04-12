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

CUIT_EXPOCONSULT = "30570717630"

# Código AFIP → Tipo FP en Dux
AFIP_A_TIPO_FP = {
    "001": "F", "006": "F", "011": "F", "051": "F",
    "002": "ND", "007": "ND", "012": "ND", "052": "ND",
    "003": "NC", "008": "NC", "013": "NC", "053": "NC",
}

# Empleado (nombre en RENDICIONES_LOG) → ID cuenta tesorería Dux
EMPLEADO_A_ID_TESORERIA = {
    "DAVID REQUELME": "319",
    "PEDRO OVIEDO": "320",
    "CARLOS VALENZUELA": "322",
    "BRENDA FERNANDEZ": "359",
    "FABRICIO DAURIA": "361",
    "PABLO MUÑOZ": "364",
    "GABRIEL SALCES": "366",
    "JORGE ANGEL": "375",
    "PABLO ACOSTA": "376",
    "PABLO AACOSTA": "376",
    "LUCIANO CAMASSA": "558",
    "CRISTIAN CALDERON": "559",
    "JUAN PABLO": "563",
    "ALEJANDRO HONORATO": "903",
    "GRACIELA": "904",
    "GUSTAVO MASTRANGELO": "920",
    "LUCIANA SARAVIA": "930",
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
HEADER_MAP = [
    "id_operacion",        # 0  (A)
    "fecha",               # 1  (B)
    "usuario",             # 2  (C)
    "oficina",             # 3  (D)
    "numero_carpeta",      # 4  (E)
    "tipo_operacion",      # 5  (F)
    "cliente",             # 6  (G)
    "concepto",            # 7  (H)
    "monto_concepto",      # 8  (I)
    "factura_tipo",        # 9  (J)
    "codigo_afip",         # 10 (K)
    "sucursal",            # 11 (L)
    "numero_factura",      # 12 (M)
    "n_comprobante",       # 13 (N)
    "proveedor_validado",  # 14 (O)
    "cuit_proveedor",      # 15 (P)
    "neto_gravado",        # 16 (Q)
    "no_gravado",          # 17 (R)
    "iva_21",              # 18 (S)
    "iva_105",             # 19 (T)
    "iva_27",              # 20 (U)
    "perc_iva",            # 21 (V)
    "perc_ganancias",      # 22 (W)
    "perc_iibb",           # 23 (X)
    "jurisdiccion",        # 24 (Y)
    "monto_total_ticket",  # 25 (Z)
    "monto_a_imputar",     # 26 (AA)
    "ticket_url",          # 27 (AB)
    "estado",              # 28 (AC)
    "clave_maestra",       # 29 (AD)
    "observaciones",       # 30 (AE)
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


def resolver_id_tesoreria(nombre_usuario):
    """Nombre de usuario → ID cuenta tesorería Dux."""
    nombre = str(nombre_usuario or "").strip().upper()
    return EMPLEADO_A_ID_TESORERIA.get(nombre, "")


def resolver_jurisdiccion_dux(codigo_interno):
    """Código interno (CF, OB, etc.) → nombre Dux (CABA, CORDOBA, etc.)."""
    code = str(codigo_interno or "").strip().upper()
    return JURISDICCION_A_DUX.get(code, code)


def es_propia(cuit, tipo_factura=""):
    """Determina si un comprobante es PROPIA (Expo Consult + Factura A)."""
    clean = str(cuit or "").replace("-", "").replace(" ", "").strip()
    tipo = str(tipo_factura or "").strip().upper()
    return clean == CUIT_EXPOCONSULT and tipo == "A"


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
    cuit_raw = str(primer.get("cuit_proveedor", "")).replace("-", "").strip()

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
    fila[8] = cuit_raw if is_propia else ""       # I: CUIT (solo PROPIA)
    # J: Op. — vacío en ENC
    fila[10] = "PES"                              # K: Mon.
    fila[11] = round(total_importe, 2)            # L: Importe

    # M: Detalle — "CONSUMIDOR FINAL" si TERCEROS sin CUIT
    if not is_propia and not cuit_raw:
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


def _construir_det(rend):
    """Construye una fila DET a partir de una rendición individual."""
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
    cuit_raw = str(rend.get("cuit_proveedor", "")).replace("-", "").strip()
    tipo_raw = str(rend.get("factura_tipo", "")).strip().upper()
    tipo_factura_dux = "PROPIA" if es_propia(cuit_raw, tipo_raw) else "TERCEROS"

    importe = safe_float(rend.get("monto_a_imputar"))
    concepto = str(rend.get("concepto", "")).strip()
    carpeta = str(rend.get("numero_carpeta", "")).strip()
    usuario = str(rend.get("usuario", "")).strip()
    id_tesoreria = resolver_id_tesoreria(usuario)

    fila = [""] * len(DUX_HEADERS)
    fila[0] = "DET"                               # A
    fila[1] = tipo_factura_dux                     # B
    fila[2] = fecha                                # C
    fila[3] = tipo_fp                              # D
    fila[4] = letra                                # E
    fila[5] = suc                                  # F
    fila[6] = nro                                  # G
    fila[7] = concepto                             # H
    # I: CUIT — vacío en DET
    fila[9] = carpeta                              # J: Op.
    # K: Mon. — vacío en DET
    fila[11] = round(importe, 2)                   # L
    # M: Detalle — vacío por ahora
    fila[13] = id_tesoreria                        # N: idEmpleado
    # O-AB: vacío en DET

    return fila


def _sumar_desglose(grupo):
    """Suma los desgloses fiscales prorrateados de un grupo de rendiciones."""
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


def generar_filas_dux(grupos):
    """
    Genera la lista completa de filas ENC/DET para el Excel Dux.

    Args:
        grupos: OrderedDict de {clave: [rendiciones]}, salida de
                agrupar_por_comprobante.

    Returns:
        Lista de listas (cada sublista = 28 celdas, una fila del Excel).
    """
    filas = []

    for clave, grupo in grupos.items():
        first = grupo[0]
        cuit_raw = str(first.get("cuit_proveedor", "")).replace("-", "").strip()
        tipo_factura_raw = str(first.get("factura_tipo", "")).strip().upper()
        is_propia_flag = es_propia(cuit_raw, tipo_factura_raw)
        tipo_factura_dux = "PROPIA" if is_propia_flag else "TERCEROS"

        # Total ENC = suma de montos a imputar de todas las filas del grupo
        total_importe = sum(safe_float(r.get("monto_a_imputar")) for r in grupo)

        # Desglose sumado (solo relevante para PROPIA, pero lo calculamos siempre)
        desglose_sumado = _sumar_desglose(grupo)

        # Fila ENC
        enc = _construir_enc(grupo, tipo_factura_dux, total_importe, desglose_sumado)
        filas.append(enc)

        # Filas DET (una por rendición/carpeta)
        for rend in grupo:
            det = _construir_det(rend)
            filas.append(det)

    return filas


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

def exportar_dux_desde_sheets(rendiciones_raw, output_path):
    """
    Pipeline completo: recibe lista de dicts (rendiciones), agrupa,
    genera filas ENC/DET y escribe el Excel.

    Args:
        rendiciones_raw: lista de dicts con claves internas (HEADER_MAP).
        output_path: ruta del .xlsx de salida.

    Returns:
        int — cantidad de filas generadas (sin contar header).
    """
    grupos = agrupar_por_comprobante(rendiciones_raw)
    filas = generar_filas_dux(grupos)
    exportar_excel_dux(filas, output_path)
    return len(filas)


# ==========================================
# BLOQUE DE PRUEBA STANDALONE
# ==========================================

if __name__ == "__main__":
    print("=== Test standalone dux_export ===\n")

    # Caso 1: TERCEROS con 2 carpetas (prorrateo)
    terceros_base = {
        "id_operacion": "20260215120000",
        "fecha": "2026-02-15",
        "usuario": "DAVID REQUELME",
        "oficina": "BUENOS AIRES",
        "tipo_operacion": "Importación",
        "cliente": "Importadora X",
        "concepto": "Flete terrestre",
        "monto_concepto": "50000",
        "factura_tipo": "B",
        "codigo_afip": "006",
        "sucursal": "00010",
        "numero_factura": "00045678",
        "n_comprobante": "0001000045678",
        "proveedor_validado": "Sí",
        "cuit_proveedor": "30712345678",
        "neto_gravado": 0,
        "no_gravado": 50000.0,
        "iva_21": 0,
        "iva_105": 0,
        "iva_27": 0,
        "perc_iva": 0,
        "perc_ganancias": 0,
        "perc_iibb": 0,
        "jurisdiccion": "",
        "monto_total_ticket": 50000.0,
        "monto_a_imputar": 25000.0,
        "ticket_url": "",
        "estado": "CERRADO",
        "clave_maestra": "30712345678B0001000045678",
        "observaciones": "",
    }
    terceros_1 = {**terceros_base, "numero_carpeta": "IMP-2026-001"}
    terceros_2 = {**terceros_base, "numero_carpeta": "IMP-2026-002"}

    # Caso 2: PROPIA con 3 carpetas (prorrateo) — Factura A con desglose
    propia_base = {
        "id_operacion": "20260215130000",
        "fecha": "2026-02-15",
        "usuario": "FABRICIO DAURIA",
        "oficina": "BUENOS AIRES",
        "tipo_operacion": "Exportación",
        "cliente": "Exportadora Y",
        "concepto": "Honorarios despachante",
        "monto_concepto": "120000",
        "factura_tipo": "A",
        "codigo_afip": "001",
        "sucursal": "00003",
        "numero_factura": "00099001",
        "n_comprobante": "0000300099001",
        "proveedor_validado": "Sí",
        "cuit_proveedor": "30570717630",
        "neto_gravado": 33057.85,
        "no_gravado": 0,
        "iva_21": 6942.15,
        "iva_105": 0,
        "iva_27": 0,
        "perc_iva": 0,
        "perc_ganancias": 0,
        "perc_iibb": 1320.23,
        "jurisdiccion": "CF",
        "monto_total_ticket": 41320.23,
        "monto_a_imputar": 13773.41,
        "ticket_url": "",
        "estado": "CERRADO",
        "clave_maestra": "30570717630A0000300099001",
        "observaciones": "Liquidación mensual",
    }
    propia_1 = {**propia_base, "numero_carpeta": "EXP-2026-010"}
    propia_2 = {**propia_base, "numero_carpeta": "EXP-2026-011"}
    propia_3 = {**propia_base, "numero_carpeta": "EXP-2026-012",
                "monto_a_imputar": 13773.41}  # residuo ajustado

    rendiciones = [terceros_1, terceros_2, propia_1, propia_2, propia_3]

    output = "dux_test_output.xlsx"
    total = exportar_dux_desde_sheets(rendiciones, output)

    print(f"Generadas {total} filas en '{output}'")
    print(f"  - Esperado: 2 ENC + 5 DET = 7 filas")

    # Verificación rápida
    grupos = agrupar_por_comprobante(rendiciones)
    for clave, grupo in grupos.items():
        cuit = grupo[0]["cuit_proveedor"]
        tipo = "PROPIA" if es_propia(cuit) else "TERCEROS"
        total_enc = sum(safe_float(r["monto_a_imputar"]) for r in grupo)
        print(f"\n  Grupo [{tipo}] clave={clave}")
        print(f"    Filas: {len(grupo)}, ENC Importe: ${total_enc:,.2f}")
        for r in grupo:
            print(f"    DET carpeta={r['numero_carpeta']} importe=${safe_float(r['monto_a_imputar']):,.2f}")

    print(f"\nArchivo generado: {output}")
