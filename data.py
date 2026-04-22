import os
import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================================
# 1. LOCAL / STATIC DATA (HYBRID MODEL)
# ==========================================

# Dict of Users -> Office
# Fallback list used when the USUARIOS sheet is unavailable or empty
_USUARIOS_FALLBACK = {
    "MAICO BARROSO": "ALEJANDRO ROCA",
    "SANTIAGO QUIRIGA": "ALEJANDRO ROCA",
    "DANIEL BUSSETTO": "ALEJANDRO ROCA",
    "COMISIONISTA BS AS": "BUENOS AIRES",
    "BRENDA FERNANDEZ": "BUENOS AIRES",
    "CARLOS VALENZUELA": "BUENOS AIRES",
    "DAVID REQUELME": "BUENOS AIRES",
    "FABRICIO DAURIA": "BUENOS AIRES",
    "JORGE ANGEL": "BUENOS AIRES",
    "PABLO AACOSTA": "BUENOS AIRES",
    "PABLO MUÑOZ": "BUENOS AIRES",
    "PEDRO OVIEDO": "BUENOS AIRES",
    "LUCIANA SARAVIA": "BUENOS AIRES",
    "COMISIONISTA CORDOBA": "CORDOBA",
    "COMISIONISTA STA FE": "CORDOBA",
    "WALTER RIOS": "CORDOBA",
    "ALEJANDRO HONORATO": "CORDOBA",
    "MAXIMILIANO ALTAMIRANO": "CORDOBA",
    "FACUNDO MASTRANGELO": "CORDOBA",
    "GABRIEL SALCES": "GRAL DEHEZA",
    "OSCAR ORTIZ": "GRAL DEHEZA",
    "RODRIGO TORRES": "GRAL DEHEZA",
    "JULIAN DIEMA": "GRAL DEHEZA",
    "IGNACIO AGÜERO": "GRAL DEHEZA",
    "EFRAIN AGÜERO": "GRAL DEHEZA",
    "BRUNO CAUDANA": "GRAL DEHEZA",
    "LUCIANO CAMASSA": "MENDOZA",
    "CRISTIAN CALDERON": "MENDOZA",
    "GUSTAVO MASTRANGELO": "RIO IV",
    "CRISTIAN SIROLESI": "RIO IV"
}

# Active dict — starts with fallback, overwritten by sync_data_from_sheets
USUARIOS_DB = dict(_USUARIOS_FALLBACK)

# Operations (Fixed)
OPERACIONES_DB = ["Importación", "Exportación"]

# List of Clients (Can be extended or sync'd later, keeping static for now)
CLIENTES_DB = [
    "Cliente A S.A.",
    "Transportes B",
    "Logística Global",
    "Importadora X",
    "Exportadora Y",
    "Servicios Z",
    "Consumidor Final" 
]

# Concepts -> Suggested Amount
# Initial values start empty to enforce Source-of-Truth from Sheets
CONCEPTOS_DB = {}

# Metadata for concepts (e.g., Office filter)
CONCEPTOS_OFICINA_DB = {}

# Full mapping: (concepto, oficina) -> monto sugerido
# Supports same concept with different amounts per office
CONCEPTOS_MONTO_POR_OFICINA = {}


def get_monto_sugerido(concepto, oficina):
    """Look up monto sugerido by (concepto, oficina) with fallback.

    Priority:
    1. Exact match (concepto, oficina)
    2. Generic match (concepto, "Todas")
    3. Any entry for this concepto (first found)
    4. 0.0
    """
    # Exact match
    monto = CONCEPTOS_MONTO_POR_OFICINA.get((concepto, oficina))
    if monto is not None:
        return monto
    # Generic "Todas"
    monto = CONCEPTOS_MONTO_POR_OFICINA.get((concepto, "Todas"))
    if monto is not None:
        return monto
    # Fallback: any entry for this concept
    for (c, o), m in CONCEPTOS_MONTO_POR_OFICINA.items():
        if c == concepto:
            return m
    return 0.0


def get_conceptos_para_oficina(oficina):
    """Returns sorted list of concept names available for an office."""
    result = set()
    for (conc, ofi) in CONCEPTOS_MONTO_POR_OFICINA:
        if ofi in ("Todas", "---") or ofi == oficina or not oficina:
            result.add(conc)
    return sorted(result)

# Providers DB (Partial/Fallback)
# In production, this can be huge. We try to load from providers.txt first.
PROVEEDORES_DB = {}

def load_providers_from_file():
    """Loads providers from providers.txt as a fallback/initial DB"""
    try:
        if os.path.exists('providers.txt'):
            with open('providers.txt', 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split('\t')
                    if len(parts) == 2:
                        name, cuit = parts
                        PROVEEDORES_DB[cuit.strip()] = name.strip()
            logger.info(f"Loaded providers from file")
    except Exception as e:
        logger.error(f"Error loading providers.txt: {e}")

load_providers_from_file()

# ==========================================
# 2. GOOGLE SHEETS INTEGRATION
# ==========================================

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
# Credentials initialization
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file"
]

def get_creds():
    """Helper to get credentials for both Sheets and Drive"""
    creds = None
    if os.path.exists("service_account.json"):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", SCOPE)
        except Exception as e:
            logger.error(f"Error loading GCP creds: {e}")
    elif "gcp_service_account" in st.secrets:
        try:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        except:
            pass
    return creds

def get_gsheets_client():
    creds = get_creds()
    email = creds.service_account_email if hasattr(creds, 'service_account_email') else "Unknown"
    if creds:
        return gspread.authorize(creds), email
    return None, "No Credentials"

def sync_data_from_sheets():
    """
    Connects to GSheets and updates CONCEPTOS_DB and PROVEEDORES_DB.
    Expected Sheet Name: 'SISTEMA_RENDICIONES' (or configurable)
    """
    client, email = get_gsheets_client()
    if not client:
        return False, f"No se pudieron generar credenciales de Google. Email: {email}"

    try:
        # Priority: st.secrets > os.environ > Default
        sheet_id = os.getenv("GSHEET_ID")
        sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
        
        try:
            if "GSHEET_ID" in st.secrets:
                sheet_id = st.secrets["GSHEET_ID"]
            if "GSHEET_NAME" in st.secrets:
                sheet_name = st.secrets["GSHEET_NAME"]
        except: pass

        logger.info(f"Target Sheet: {sheet_id if sheet_id else sheet_name}")

        sh = None
        if sheet_id:
             try:
                 logger.info("Opening sheet by ID...")
                 sh = client.open_by_key(sheet_id)
                 logger.info(f"Opened sheet by ID: {sheet_id}")
             except Exception as e:
                 return False, f"Error abriendo por ID '{sheet_id}': {e}"
        else:
             logger.info("Opening sheet by Name...")
             sh = client.open(sheet_name)
             logger.info(f"Opened sheet by name: {sheet_name}")
        
        # 1. DB_PARAMETROS -> Update CONCEPTOS_DB
        try:
            logger.info("Syncing DB_PARAMETROS...")
            ws_params = sh.worksheet("DB_PARAMETROS")
            rows = ws_params.get_all_values(value_render_option='UNFORMATTED_VALUE')
            
            # Identify headers
            headers = [h.lower().strip() for h in rows[0]]
            try:
                idx_concepto = headers.index("concepto")
                idx_monto = headers.index("monto sugerido")
            except ValueError:
                idx_concepto = 0
                idx_monto = 1
            
            # Try to find 'oficina'
            try:
                idx_oficina = headers.index("oficina")
            except ValueError:
                idx_oficina = -1

            new_conceptos = {}
            new_oficinas = {}
            new_monto_por_oficina = {}

            for row in rows[1:]:
                if len(row) > idx_monto:
                    conc = str(row[idx_concepto]).strip()
                    if not conc: continue

                    raw_val = row[idx_monto]

                    # Office parsing
                    oficina = "Todas"
                    if idx_oficina != -1 and len(row) > idx_oficina:
                        val_of = str(row[idx_oficina]).strip()
                        if val_of: oficina = val_of

                    if isinstance(raw_val, (int, float)):
                        monto = float(raw_val)
                    else:
                        try:
                            clean_val = str(raw_val).replace("$", "").replace(",", "")
                            monto = float(clean_val)
                        except:
                            monto = 0.0

                    new_conceptos[conc] = monto
                    new_oficinas[conc] = oficina
                    # Full mapping with composite key (concepto, oficina)
                    new_monto_por_oficina[(conc, oficina)] = monto

            global CONCEPTOS_DB, CONCEPTOS_OFICINA_DB, CONCEPTOS_MONTO_POR_OFICINA
            CONCEPTOS_DB.clear()
            CONCEPTOS_DB.update(new_conceptos)

            CONCEPTOS_OFICINA_DB.clear()
            CONCEPTOS_OFICINA_DB.update(new_oficinas)

            CONCEPTOS_MONTO_POR_OFICINA.clear()
            CONCEPTOS_MONTO_POR_OFICINA.update(new_monto_por_oficina)
                            
            logger.info("Synced CONCEPTOS_DB from Sheets")
        except Exception as e:
            logger.warning(f"Could not sync DB_PARAMETROS: {type(e).__name__}: {e}")

        # 2. DB_PROVEEDORES -> Update PROVEEDORES_DB
        try:
            logger.info("Syncing DB_PROVEEDORES...")
            ws_prov = sh.worksheet("DB_PROVEEDORES")
            rows = ws_prov.get_all_values()
            for row in rows[1:]:
                if len(row) >= 2:
                    cuit = str(row[0]).strip()
                    name = str(row[1]).strip()
                    if cuit and name:
                        PROVEEDORES_DB[cuit] = name
            logger.info(f"Synced {len(rows)-1} providers from Sheets")
        except Exception as e:
             logger.warning(f"Could not sync DB_PROVEEDORES: {e}")

        # 3. DB_CLIENTE -> Update CLIENTES_DB
        try:
            logger.info("Syncing DB_CLIENTE...")
            ws_cli = sh.worksheet("DB_CLIENTE")
            rows_cli = ws_cli.get_all_values()
            new_clients = []
            for row in rows_cli[1:]: 
                if len(row) >= 1:
                    cli_name = str(row[0]).strip()
                    if cli_name:
                        new_clients.append(cli_name)
            
            if new_clients:
                global CLIENTES_DB
                CLIENTES_DB.clear()
                CLIENTES_DB.extend(sorted(new_clients))
                logger.info(f"Synced {len(new_clients)} clients from Sheets")
                
        except Exception as e:
            logger.warning(f"Could not sync DB_CLIENTE: {e}")

        # 4. USUARIOS -> Update USUARIOS_DB
        try:
            logger.info("Syncing USUARIOS...")
            try:
                ws_users = sh.worksheet("USUARIOS")
            except gspread.exceptions.WorksheetNotFound:
                logger.info("USUARIOS sheet not found — creating it...")
                ws_users = sh.add_worksheet(title="USUARIOS", rows=100, cols=3)
                ws_users.update(range_name="A1:C1", values=[["Nombre", "Email", "Oficina"]])
                # Seed with current fallback data so Fabián has a starting point
                seed_rows = [[name, "", office] for name, office in _USUARIOS_FALLBACK.items()]
                if seed_rows:
                    ws_users.update(range_name=f"A2:C{1 + len(seed_rows)}", values=seed_rows)
                logger.info(f"USUARIOS sheet created and seeded with {len(seed_rows)} rows")

            rows_users = ws_users.get_all_values()
            if len(rows_users) > 1:
                headers_u = [h.lower().strip() for h in rows_users[0]]
                try:
                    idx_nombre = headers_u.index("nombre")
                except ValueError:
                    idx_nombre = 0
                try:
                    idx_oficina_u = headers_u.index("oficina")
                except ValueError:
                    idx_oficina_u = 2

                new_usuarios = {}
                for row in rows_users[1:]:
                    if len(row) > max(idx_nombre, idx_oficina_u):
                        nombre = str(row[idx_nombre]).strip().upper()
                        oficina_u = str(row[idx_oficina_u]).strip().upper()
                        if nombre and oficina_u:
                            new_usuarios[nombre] = oficina_u

                if new_usuarios:
                    global USUARIOS_DB
                    USUARIOS_DB.clear()
                    USUARIOS_DB.update(new_usuarios)
                    logger.info(f"Synced {len(new_usuarios)} users from USUARIOS sheet")
                else:
                    logger.warning("USUARIOS sheet has no valid rows — keeping fallback data")
            else:
                logger.warning("USUARIOS sheet is empty — keeping fallback data")

        except Exception as e:
            logger.warning(f"Could not sync USUARIOS: {e} — keeping fallback data")

        # 5. Retroactive Validation (New Feature)
        try:
            count_fixed = _revalidate_log(client, sheet_id if sheet_id else sheet_name, PROVEEDORES_DB)
            if count_fixed > 0:
                logger.info(f"Retro-validation: {count_fixed} rows updated to 'Sí'")
        except Exception as e:
            logger.error(f"Retro-validation error: {e}")

        # 6. CONFIG_NOTIFICACIONES — Create if missing (same pattern as USUARIOS)
        try:
            from notificaciones import crear_hoja_config_notificaciones
            crear_hoja_config_notificaciones(sh)
        except Exception as e:
            logger.warning(f"Could not ensure CONFIG_NOTIFICACIONES: {e}")

        return True, "Sync OK"

    except gspread.exceptions.SpreadsheetNotFound:
        return False, f"SpreadsheetNotFound: Planilla ID '{sheet_id if sheet_id else sheet_name}' no encontrada. [Email activo: {email}]"
    except Exception as e:
        err_msg = f"{type(e).__name__}: {str(e)} [Email activo: {email}]"
        logger.error(f"GSheets Sync Error: {err_msg}")
        return False, err_msg
# Drive configuration constants
DRIVE_FOLDER_ID_CONST = "1y5W...PASTE_ID_HERE" # User should replace this or set env var
from google.oauth2.credentials import Credentials as UserCredentials

def get_drive_creds():
    """
    Returns credentials for Drive Upload. 
    Prioritizes 'token.json' (User Auth) to avoid Quota issues.
    Fallbacks to Service Account.
    """
    # 1. Try User Token (OAuth2)
    # A. From FILE (Local)
    if os.path.exists('token.json'):
        try:
            return UserCredentials.from_authorized_user_file('token.json', SCOPE)
        except Exception as e:
            logger.warning(f"Invalid token.json: {e}")
            
    # B. From SECRETS (Cloud)
    elif "gcp_user_token" in st.secrets:
        try:
            token_dict = dict(st.secrets["gcp_user_token"])
            return UserCredentials.from_authorized_user_info(token_dict, SCOPE)
        except Exception as e:
            logger.warning(f"Invalid gcp_user_token in secrets: {e}")

    # 2. Keyfile (Fallback)
    return get_creds() # Calls the existing Service Account loader


def upload_receipt_to_drive(file_bytes, file_name, mime_type):
    """
    Uploads a file to Google Drive and returns the webViewLink.
    Target Folder: 'Comprobantes_Rendicion' (Folder ID hardcoded or found by name)
    """
    try:
        # Use Drive-specific creds loader (User Auth priority)
        creds = get_drive_creds()
        if not creds:
             return None, None, "No Credentials (token.json or service_account.json)"

        service = build('drive', 'v3', credentials=creds)
        
        # Priority: Env Var > Const
        folder_id = os.getenv("DRIVE_FOLDER_ID", DRIVE_FOLDER_ID_CONST)
        
        file_metadata = {'name': file_name}
        if folder_id and "PASTE" not in folder_id:
            file_metadata['parents'] = [folder_id]
            
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type)
        
        file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        return file.get('webViewLink'), file.get('id'), None
    except Exception as e:
        logger.error(f"Drive Upload Error: {e}")
        return None, None, str(e)

def find_available_invoice_balance(cuit_provider, amount_needed):
    """
    Searches 'CONTROL_SALDOS' for invoices from this provider with 'Saldo Disponible' >= amount_needed.
    Returns: dict with invoice data (suc, num, tipo) or None.
    """
    try:
        client, email = get_gsheets_client()
        if not client: return None
        
        sheet_id = os.getenv("GSHEET_ID")
        sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
        try:
            if "GSHEET_ID" in st.secrets:
                sheet_id = st.secrets["GSHEET_ID"]
            if "GSHEET_NAME" in st.secrets:
                sheet_name = st.secrets["GSHEET_NAME"]
        except: pass

        if sheet_id:
            sh = client.open_by_key(sheet_id)
        else:
            sh = client.open(sheet_name)
            
        ws = sh.worksheet("CONTROL_SALDOS")
        
        rows = ws.get_all_records() # Expects headers in row 1
        
        # Candidates list
        candidates = []
        
        for r in rows:
            # Check CUIT (Handle string/int types)
            row_cuit = str(r.get("Cuit_Proveedor", "")).replace("-","").strip()
            target_cuit = str(cuit_provider).replace("-","").strip()
            
            if row_cuit == target_cuit:
                try:
                    saldo = float(str(r.get("Saldo Disponible", 0)).replace("$","").replace(",",""))
                except:
                    saldo = 0.0
                
                if saldo >= amount_needed:
                    # Found a candidate
                    return {
                        "tipo": r.get("Tipo", "C"),
                        "sucursal": str(r.get("Sucursal", "")).zfill(5),
                        "numero": str(r.get("Numero", "")).zfill(8),
                        "saldo": saldo
                    }
        return None
    except Exception as e:
        logger.warning(f"Error searching balances: {e}")
        return None

def log_rendicion_to_sheet(payload, ticket_url="", estado_override=None):
    """
    Appends a new row to RENDICIONES_LOG tab with updated columns.

    Args:
        payload: dict with rendition data.
        ticket_url: URL of uploaded receipt in Drive.
        estado_override: if set, overrides the Puchito-calculated estado
                         (e.g. "PENDIENTE REVISIÓN" for excess amounts).
    """
    client, email = get_gsheets_client()
    if not client:
        return False
        
    try:
        sheet_id = os.getenv("GSHEET_ID")
        sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
        try:
            if "GSHEET_ID" in st.secrets:
                sheet_id = st.secrets["GSHEET_ID"]
            if "GSHEET_NAME" in st.secrets:
                sheet_name = st.secrets["GSHEET_NAME"]
        except: pass

        if sheet_id:
            sh = client.open_by_key(sheet_id)
        else:
            sh = client.open(sheet_name)
            
        ws_log = sh.worksheet("RENDICIONES_LOG")
        
        # Mapping payload to columns (22 COLUMNS NOW)
        row_id = datetime.now().strftime("%Y%m%d%H%M%S")
        
        # Parse and Pad Components for Robust Keys
        suc_raw = str(payload.get("sucursal_factura", "") or "").strip()
        num_raw = str(payload.get("numero_factura", "") or "").strip()
        
        # Only pad if they loop numeric-ish. If empty, keep empty?
        # User example: 02016 (5 digits).
        suc = suc_raw.zfill(5) if suc_raw.isdigit() else suc_raw
        num = num_raw.zfill(8) if num_raw.isdigit() else num_raw
        
        n_comprobante = f"{suc}{num}"
        
        # Calculate Estado (Puchito Rule)
        monto_ticket = payload.get("monto_ticket_total", 0.0)
        monto_imputar = payload.get("monto_a_imputar", 0.0)
        
        saldo_pendiente = abs(monto_ticket - monto_imputar)
        if estado_override:
            estado_saldo = estado_override
        elif saldo_pendiente < 1000.0 and saldo_pendiente > 0:
            estado_saldo = "LISTA PARA AJUSTE"
        elif saldo_pendiente > 0:
            estado_saldo = "PENDIENTE"
        else:
            estado_saldo = "CERRADO"
             
        # Clave Maestra (Requested for Control Saldos)
        cuit = str(payload.get("proveedor_cuit", "")).strip()
        tipo = str(payload.get("tipo_factura", "")).strip().upper()
        
        # Logic: If no ticket but we found a balance match in app.py, key might be passed in payload?
        # Assuming payload has the data used to find it.
        # Key: CUIT + TIPO + SUCRUSAL (5) + NUMERO (8)
        # Ensure strict padding here too
        clave_unica = f"{cuit}{tipo}{suc}{num}"

        # Auditor Breakdown (Lines Q-Y)
        desglose = payload.get("auditor_desglose", {})
        
        # Mapping to Columns R, S, T, U, V, W, X logic
        # If Type B/C, 'monto_ticket' is total, and auditor puts it in "No Gravado" or we force it here?
        # The prompt says Auditor puts it in "No Gravado". We trust the desglose dict.
        
        col_q_neto = payload.get("monto_gravado_calculated", payload.get("monto_gravado_calculado", 0)) 
        col_r_no_grav = desglose.get("columna_R_no_gravado", 0)
        col_s_iva21 = desglose.get("columna_S_iva_21", 0)
        col_t_iva105 = desglose.get("columna_T_iva_105", 0)
        col_u_iva27 = desglose.get("columna_U_iva_27", 0)
        col_v_perc_iva = desglose.get("columna_V_perc_iva", 0)
        col_w_perc_g = desglose.get("columna_W_perc_ganancias", 0)
        col_x_perc_i = desglose.get("columna_X_perc_iibb", 0)
        col_y_juris = desglose.get("columna_Y_jurisdiccion_code", "")
        col_z_total = desglose.get("monto_total_columna_Z", desglose.get("monto_total_columna_Y", monto_ticket))

        row = [
            row_id,                                     # 1. ID Operación
            payload.get("fecha"),                       # 2. Fecha
            payload.get("usuario"),                     # 3. Usuario
            payload.get("oficina"),                     # 4. Oficina
            payload.get("numero_carpeta"),              # 5. Número de Carpeta
            payload.get("tipo_operacion"),              # 6. Tipo de Operación
            payload.get("cliente"),                     # 7. Cliente
            payload.get("concepto"),                    # 8. Concepto
            payload.get("monto_sugerido_concepto", 0),  # 9. Monto Concepto
            payload.get("tipo_factura", ""),            # 10. factura_tipo
            payload.get("codigo_afip", ""),             # 11. Código AFIP
            suc,                                        # 12. Sucursal (Padded)
            num,                                        # 13. Número_de_factura (Padded)
            n_comprobante,                              # 14. N°Comprobante
            payload.get("proveedor_validado_txt", "No"),# 15. Proveedor_Validado
            cuit,                                       # 16. Cuit_Proveedor_AI
            
            # --- NEW AUDITOR COLUMNS (Q-Z) --- 
            col_q_neto,                                 # 17 (Q). Neto Gravado
            col_r_no_grav,                              # 18 (R). No Gravado (o Total B/C)
            col_s_iva21,                                # 19 (S). IVA 21%
            col_t_iva105,                               # 20 (T). IVA 10.5%
            col_u_iva27,                                # 21 (U). IVA 27%
            col_v_perc_iva,                             # 22 (V). Perc IVA
            col_w_perc_g,                               # 23 (W). Perc Ganancias
            col_x_perc_i,                               # 24 (X). Perc IIBB
            col_y_juris,                                # 25 (Y). Jurisdicción
            col_z_total,                                # 26 (Z). Monto Total Ticket
            
            # --- SHIFTED METADATA (AA+) ---
            monto_imputar,                              # 27 (AA). Monto a Imputar (Manual)
            ticket_url,                                 # 28 (AB). Ticket URL
            estado_saldo,                               # 29 (AC). Estado (Puchito)
            clave_unica,                                # 30 (AD). Clave Maestra
            payload.get("observaciones", ""),            # 31 (AE). Observaciones

            # --- AUDIT COLUMNS (AF-AH) ---
            "",                                         # 32 (AF). Motivo_Rechazo
            "",                                         # 33 (AG). Revisado_Por
            "",                                         # 34 (AH). Fecha_Revision
        ]

        ws_log.append_row(row)
        return True
    except Exception as e:
        logger.error(f"Error logging to sheet: {e}")
        return False

def check_duplicate_comprobante(cuit, sucursal, numero):
    """
    Checks RENDICIONES_LOG for an existing row with the same CUIT (col 16) and N°Comprobante (col 14).
    Returns True if a duplicate exists, False otherwise.
    """
    if not cuit or not numero:
        return False

    client, _ = get_gsheets_client()
    if not client:
        return False

    try:
        sheet_id = os.getenv("GSHEET_ID")
        sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
        try:
            if "GSHEET_ID" in st.secrets:
                sheet_id = st.secrets["GSHEET_ID"]
            if "GSHEET_NAME" in st.secrets:
                sheet_name = st.secrets["GSHEET_NAME"]
        except:
            pass

        if sheet_id:
            sh = client.open_by_key(sheet_id)
        else:
            sh = client.open(sheet_name)

        ws_log = sh.worksheet("RENDICIONES_LOG")

        # Build the expected N°Comprobante the same way log_rendicion_to_sheet does
        suc = str(sucursal or "").strip()
        num = str(numero or "").strip()
        suc = suc.zfill(5) if suc.isdigit() else suc
        num = num.zfill(8) if num.isdigit() else num
        n_comprobante = f"{suc}{num}"
        cuit_clean = str(cuit).strip()

        # Col N (14) = N°Comprobante, Col P (16) = CUIT Proveedor
        all_rows = ws_log.get_all_values()
        for row in all_rows[1:]:  # skip header
            if len(row) >= 16:
                row_n_comp = str(row[13]).strip()   # col 14 (0-indexed: 13)
                row_cuit = str(row[15]).strip()      # col 16 (0-indexed: 15)
                if row_cuit == cuit_clean and row_n_comp == n_comprobante:
                    return True
        return False
    except Exception as e:
        logger.error(f"Error checking duplicate: {e}")
        return False


def _calcular_estado_saldo(saldo_restante):
    """Determina el estado según el saldo restante."""
    if saldo_restante == 0:
        return "CERRADO"
    elif saldo_restante < 1000 or saldo_restante < 0:
        return "AJUSTE Puchito"
    else:
        return "EN PROCESO"


def actualizar_control_saldos(payload):
    """
    Actualiza la hoja CONTROL_SALDOS tras guardar una rendición.
    Si la factura ya existe (CUIT + ID Factura), actualiza imputado/saldo/estado.
    Si no existe, crea una fila nueva.

    Returns:
        (bool, str): (éxito, mensaje)
    """
    try:
        client, email = get_gsheets_client()
        if not client:
            return False, f"No se pudieron generar credenciales. Email: {email}"

        sheet_id = os.getenv("GSHEET_ID")
        sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
        try:
            if "GSHEET_ID" in st.secrets:
                sheet_id = st.secrets["GSHEET_ID"]
            if "GSHEET_NAME" in st.secrets:
                sheet_name = st.secrets["GSHEET_NAME"]
        except:
            pass

        if sheet_id:
            sh = client.open_by_key(sheet_id)
        else:
            sh = client.open(sheet_name)

        ws = sh.worksheet("CONTROL_SALDOS")

        # Construir ID Factura
        suc_raw = str(payload.get("sucursal_factura", "") or "").strip()
        num_raw = str(payload.get("numero_factura", "") or "").strip()
        suc = suc_raw.zfill(5) if suc_raw.isdigit() else suc_raw
        num = num_raw.zfill(8) if num_raw.isdigit() else num_raw
        id_factura = f"{suc}{num}"

        cuit = str(payload.get("proveedor_cuit", "")).strip()
        monto_a_imputar = safe_float(payload.get("monto_a_imputar", 0))

        # Buscar fila existente con mismo CUIT (col A) + ID Factura (col B)
        all_values = ws.get_all_values()
        fila_encontrada = None
        fila_idx = None  # 1-based row index in sheet

        for i, row in enumerate(all_values):
            if i == 0:
                continue  # Skip header
            if len(row) >= 2:
                row_cuit = str(row[0]).strip()
                row_id = str(row[1]).strip()
                if row_cuit == cuit and row_id == id_factura:
                    fila_encontrada = row
                    fila_idx = i + 1  # 1-based
                    break

        if fila_encontrada:
            # Actualizar fila existente
            respaldo = safe_float(fila_encontrada[2]) if len(fila_encontrada) > 2 else 0.0
            total_imputado = safe_float(fila_encontrada[3]) if len(fila_encontrada) > 3 else 0.0
            total_imputado += monto_a_imputar
            saldo = respaldo - total_imputado
            estado = _calcular_estado_saldo(saldo)

            ws.batch_update([
                {'range': f'D{fila_idx}', 'values': [[round(total_imputado, 2)]]},
                {'range': f'E{fila_idx}', 'values': [[round(saldo, 2)]]},
                {'range': f'F{fila_idx}', 'values': [[estado]]},
            ])
            return True, f"Saldo actualizado: {id_factura} -> {estado} (${saldo:,.2f})"
        else:
            # Crear fila nueva
            tipo = str(payload.get("tipo_factura", "")).strip().upper()
            if tipo == "A":
                respaldo = safe_float(payload.get("monto_neto_original", 0)) + \
                           safe_float(payload.get("no_gravado_original", 0))
            else:
                respaldo = safe_float(payload.get("monto_ticket_total_original", 0))

            saldo = respaldo - monto_a_imputar
            estado = _calcular_estado_saldo(saldo)

            ws.append_row([
                cuit,
                id_factura,
                round(respaldo, 2),
                round(monto_a_imputar, 2),
                round(saldo, 2),
                estado,
            ])
            return True, f"Nuevo saldo creado: {id_factura} -> {estado} (${saldo:,.2f})"

    except Exception as e:
        logger.error(f"Error actualizando CONTROL_SALDOS: {e}")
        return False, str(e)


def recalcular_control_saldos():
    """
    Recalcula CONTROL_SALDOS completo desde RENDICIONES_LOG.
    Lee todas las rendiciones, agrupa por CUIT + ID Factura,
    calcula respaldo/imputado/saldo/estado, y reescribe la hoja.

    Returns:
        (bool, str, int): (éxito, mensaje, filas actualizadas)
    """
    try:
        client, email = get_gsheets_client()
        if not client:
            return False, f"No se pudieron generar credenciales. Email: {email}", 0

        sheet_id = os.getenv("GSHEET_ID")
        sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
        try:
            if "GSHEET_ID" in st.secrets:
                sheet_id = st.secrets["GSHEET_ID"]
            if "GSHEET_NAME" in st.secrets:
                sheet_name = st.secrets["GSHEET_NAME"]
        except:
            pass

        if sheet_id:
            sh = client.open_by_key(sheet_id)
        else:
            sh = client.open(sheet_name)

        # 1. Leer RENDICIONES_LOG
        ws_log = sh.worksheet("RENDICIONES_LOG")
        all_records = ws_log.get_all_records()

        if not all_records:
            return False, "RENDICIONES_LOG está vacía.", 0

        # 2. Agrupar por CUIT + ID Factura
        grupos = {}
        for record in all_records:
            cuit = str(record.get("Cuit_Proveedor_AI", "")).strip()
            suc_raw = str(record.get("Sucursal", "") or "").strip()
            num_raw = str(record.get("Número_de_factura", "") or "").strip()
            suc = suc_raw.zfill(5) if suc_raw.isdigit() else suc_raw
            num = num_raw.zfill(8) if num_raw.isdigit() else num_raw
            id_factura = f"{suc}{num}"

            if not cuit or not id_factura or id_factura == "0000000000000":
                continue

            key = f"{cuit}|{id_factura}"
            if key not in grupos:
                grupos[key] = {
                    "cuit": cuit,
                    "id_factura": id_factura,
                    "tipo": str(record.get("factura_tipo", "")).strip().upper(),
                    "records": [],
                }
            grupos[key]["records"].append(record)

        # 3. Calcular saldos
        filas_saldos = []
        for key, grupo in grupos.items():
            primer = grupo["records"][0]
            tipo = grupo["tipo"]

            # Determinar Respaldo
            if tipo == "A":
                respaldo = safe_float(primer.get("Neto Gravado", 0)) + \
                           safe_float(primer.get("No Gravado", 0))
            else:
                respaldo = safe_float(primer.get("Monto Total Ticket", 0))

            # Sumar todos los monto_a_imputar
            total_imputado = sum(
                safe_float(r.get("Monto a Imputar", 0))
                for r in grupo["records"]
            )

            saldo = respaldo - total_imputado
            estado = _calcular_estado_saldo(saldo)

            filas_saldos.append([
                grupo["cuit"],
                grupo["id_factura"],
                round(respaldo, 2),
                round(total_imputado, 2),
                round(saldo, 2),
                estado,
            ])

        # 4. Limpiar y reescribir CONTROL_SALDOS
        ws_saldos = sh.worksheet("CONTROL_SALDOS")
        ws_saldos.clear()

        header = ["Cuit_Proveedor", "ID_Factura", "Respaldo", "Total_Imputado", "Saldo Disponible", "Estado"]
        all_data = [header] + filas_saldos

        if len(filas_saldos) > 0:
            total_rows = 1 + len(filas_saldos)
            cell_range = f"A1:F{total_rows}"
            ws_saldos.update(range_name=cell_range, values=all_data)

        msg = f"Recalculado: {len(filas_saldos)} facturas procesadas"
        logger.info(msg)
        return True, msg, len(filas_saldos)

    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        logger.error(f"Error recalculando CONTROL_SALDOS: {err_detail}")
        return False, f"Error: {str(e)}", 0


def _revalidate_log(client, sheet_key, providers_db):
    """
    Scans RENDICIONES_LOG for rows where 'Proveedor Validado' (Col O) is 'No'
    and checks if the CUIT (Col P) exists in the updated providers_db.
    If yes, updates Col O to 'Sí'.
    """
    try:
        sh = client.open_by_key(sheet_key) if sheet_key.startswith("1") else client.open(sheet_key)
        ws = sh.worksheet("RENDICIONES_LOG")
        
        # Get Columns O (15) and P (16). 1-based index.
        # Efficiently get all values
        # We assume headers are row 1.
        
        # Get O and P columns. 
        # Note: gspread get_values handles 'O:P'
        data_range = ws.get_values("O:P") 
        
        if not data_range:
            return 0
            
        updates = []
        
        # Iterate (Skip header row 0)
        for i, row in enumerate(data_range):
            if i == 0: continue # Header
            
            # Row structure: [Col O value, Col P value]
            if len(row) < 2: continue
            
            status = str(row[0]).strip()
            cuit = str(row[1]).strip()
            
            if status in ["No", "pending_approval", "Pending"]:
                # Check DB
                clean_cuit = cuit.replace("-", "").strip()
                match = False
                if cuit in providers_db: match = True
                elif clean_cuit in providers_db: match = True
                elif clean_cuit in [k.replace("-", "") for k in providers_db.keys()]: match = True
                
                if match:
                    # Log index is i + 1 (1-based)
                    # We want to update Col O (15)
                    # batch_update format: {'range': 'O5', 'values': [['Sí']]}
                    cell_ref = f"O{i+1}"
                    updates.append({
                        'range': cell_ref,
                        'values': [['Sí']]
                    })
        
        if updates:
            ws.batch_update(updates)
            return len(updates)
            
        return 0
    except Exception as e:
        logger.error(f"Error in _revalidate_log: {e}")
        return 0


# ==========================================
# 4. REVISIÓN DE EXCESOS
# ==========================================


def _get_sheet_handle():
    """Helper: returns (gspread_client, spreadsheet_handle) or raises."""
    client, email = get_gsheets_client()
    if not client:
        raise RuntimeError(f"No credentials. Email: {email}")

    sheet_id = os.getenv("GSHEET_ID")
    sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
    try:
        if "GSHEET_ID" in st.secrets:
            sheet_id = st.secrets["GSHEET_ID"]
        if "GSHEET_NAME" in st.secrets:
            sheet_name = st.secrets["GSHEET_NAME"]
    except Exception:
        pass

    if sheet_id:
        sh = client.open_by_key(sheet_id)
    else:
        sh = client.open(sheet_name)
    return client, sh


def leer_pendientes_revision():
    """Lee RENDICIONES_LOG y devuelve filas con estado PENDIENTE REVISIÓN.

    Returns:
        list[dict]: cada dict tiene keys: row_idx (1-based sheet row),
        id_operacion, fecha, usuario, oficina, numero_carpeta,
        concepto, monto_sugerido, monto_imputar, proveedor_cuit,
        proveedor_nombre, ticket_url, estado.
    """
    try:
        _, sh = _get_sheet_handle()
        ws = sh.worksheet("RENDICIONES_LOG")
        all_rows = ws.get_all_values()

        if len(all_rows) < 2:
            return []

        results = []
        for i, row in enumerate(all_rows):
            if i == 0:
                continue  # header
            if len(row) < 31:
                continue

            estado = str(row[28]).strip()  # AC = index 28
            if estado != "PENDIENTE REVISIÓN":
                continue

            results.append({
                "row_idx": i + 1,  # 1-based for gspread
                "id_operacion": row[0],
                "fecha": row[1],
                "usuario": row[2],
                "oficina": row[3],
                "numero_carpeta": row[4],
                "concepto": row[7],
                "monto_sugerido": row[8],
                "tipo_factura": row[9],
                "sucursal": row[11],
                "numero_factura": row[12],
                "proveedor_cuit": row[15],
                "proveedor_nombre": "",  # not stored directly — use CUIT lookup
                "monto_total_ticket": row[25],
                "monto_imputar": row[26],
                "ticket_url": row[27],
                "estado": estado,
            })

        return results
    except Exception as e:
        logger.error(f"Error reading pending reviews: {e}")
        return []


def aprobar_rendicion(row_idx, admin_user):
    """Aprueba una rendición en PENDIENTE REVISIÓN.

    Recalcula el estado usando la regla del Puchito y escribe las columnas
    de auditoría (Revisado_Por, Fecha_Revision).

    Args:
        row_idx: 1-based row index in RENDICIONES_LOG.
        admin_user: name of the admin who approved.

    Returns:
        (bool, str): (success, message)
    """
    try:
        _, sh = _get_sheet_handle()
        ws = sh.worksheet("RENDICIONES_LOG")

        row = ws.row_values(row_idx)
        if len(row) < 29 or str(row[28]).strip() != "PENDIENTE REVISIÓN":
            return False, "La fila no está en estado PENDIENTE REVISIÓN"

        # Recalculate estado using Puchito rule
        monto_ticket = safe_float(row[25])    # Z: Monto Total Ticket
        monto_imputar = safe_float(row[26])   # AA: Monto a Imputar
        saldo_pendiente = abs(monto_ticket - monto_imputar)

        if saldo_pendiente == 0:
            nuevo_estado = "CERRADO"
        elif saldo_pendiente < 1000:
            nuevo_estado = "LISTA PARA AJUSTE"
        else:
            nuevo_estado = "PENDIENTE"

        fecha_rev = datetime.now().isoformat()

        ws.batch_update([
            {"range": f"AC{row_idx}", "values": [[nuevo_estado]]},
            {"range": f"AG{row_idx}", "values": [[admin_user]]},
            {"range": f"AH{row_idx}", "values": [[fecha_rev]]},
        ])

        logger.info(f"Rendición {row_idx} aprobada -> {nuevo_estado} por {admin_user}")
        return True, f"Aprobada -> {nuevo_estado}"

    except Exception as e:
        logger.error(f"Error approving rendition {row_idx}: {e}")
        return False, str(e)


def rechazar_rendicion(row_idx, admin_user, motivo):
    """Rechaza una rendición en PENDIENTE REVISIÓN.

    Sets estado to RECHAZADO, writes motivo and audit columns.
    Also reverts the imputación in CONTROL_SALDOS atomically.

    Args:
        row_idx: 1-based row index in RENDICIONES_LOG.
        admin_user: name of the admin who rejected.
        motivo: mandatory rejection reason.

    Returns:
        (bool, str): (success, message)
    """
    try:
        _, sh = _get_sheet_handle()
        ws_log = sh.worksheet("RENDICIONES_LOG")

        row = ws_log.row_values(row_idx)
        if len(row) < 29 or str(row[28]).strip() != "PENDIENTE REVISIÓN":
            return False, "La fila no está en estado PENDIENTE REVISIÓN"

        # Extract data needed to revert CONTROL_SALDOS
        cuit = str(row[15]).strip()
        suc_raw = str(row[11]).strip()
        num_raw = str(row[12]).strip()
        suc = suc_raw.zfill(5) if suc_raw.isdigit() else suc_raw
        num = num_raw.zfill(8) if num_raw.isdigit() else num_raw
        id_factura = f"{suc}{num}"
        monto_a_revertir = safe_float(row[26])  # AA: Monto a Imputar

        # Step 1: Revert CONTROL_SALDOS
        revert_ok = _revertir_imputacion_saldos(sh, cuit, id_factura, monto_a_revertir)
        if not revert_ok:
            return False, "No se pudo revertir la imputación en CONTROL_SALDOS. El estado no fue modificado."

        # Step 2: Update RENDICIONES_LOG (only if revert succeeded)
        fecha_rev = datetime.now().isoformat()
        try:
            ws_log.batch_update([
                {"range": f"AC{row_idx}", "values": [["RECHAZADO"]]},
                {"range": f"AF{row_idx}", "values": [[motivo]]},
                {"range": f"AG{row_idx}", "values": [[admin_user]]},
                {"range": f"AH{row_idx}", "values": [[fecha_rev]]},
            ])
        except Exception as e:
            # Revert already happened — log the inconsistency
            logger.error(f"CRITICAL: CONTROL_SALDOS reverted but RENDICIONES_LOG update failed for row {row_idx}: {e}")
            return False, f"Se revirtió el saldo pero falló actualizar el log: {e}"

        logger.info(f"Rendición {row_idx} rechazada por {admin_user}: {motivo}")
        return True, "Rechazada y saldo revertido"

    except Exception as e:
        logger.error(f"Error rejecting rendition {row_idx}: {e}")
        return False, str(e)


def _revertir_imputacion_saldos(sh, cuit, id_factura, monto_a_revertir):
    """Reverts an imputación in CONTROL_SALDOS by subtracting the amount.

    Returns True on success, False on failure.
    """
    try:
        ws = sh.worksheet("CONTROL_SALDOS")
        all_values = ws.get_all_values()

        for i, row in enumerate(all_values):
            if i == 0:
                continue
            if len(row) < 2:
                continue
            row_cuit = str(row[0]).strip()
            row_id = str(row[1]).strip()
            if row_cuit == cuit and row_id == id_factura:
                fila_idx = i + 1
                respaldo = safe_float(row[2]) if len(row) > 2 else 0.0
                total_imputado = safe_float(row[3]) if len(row) > 3 else 0.0

                total_imputado = max(0, total_imputado - monto_a_revertir)
                saldo = respaldo - total_imputado
                estado = _calcular_estado_saldo(saldo)

                ws.batch_update([
                    {"range": f"D{fila_idx}", "values": [[round(total_imputado, 2)]]},
                    {"range": f"E{fila_idx}", "values": [[round(saldo, 2)]]},
                    {"range": f"F{fila_idx}", "values": [[estado]]},
                ])
                logger.info(f"CONTROL_SALDOS reverted: {id_factura} imputado={total_imputado} saldo={saldo}")
                return True

        # No matching row found — might be a manual entry without CONTROL_SALDOS
        logger.warning(f"No CONTROL_SALDOS row for CUIT={cuit} ID={id_factura} — skipping revert")
        return True  # Not an error, just no balance row to revert

    except Exception as e:
        logger.error(f"Error reverting CONTROL_SALDOS: {e}")
        return False


# ==========================================
# 5. EXPORTACIÓN DUX
# ==========================================

from dux_export import agrupar_por_comprobante, generar_filas_dux, DUX_HEADERS, safe_float


def escribir_export_dux_en_sheet(fecha_desde=None, fecha_hasta=None, estado=None):
    """
    Lee RENDICIONES_LOG, filtra, genera filas ENC/DET y las escribe
    en la hoja EXPORT_DUX del mismo spreadsheet.

    Args:
        fecha_desde (date|None): filtrar rendiciones >= esta fecha.
        fecha_hasta (date|None): filtrar rendiciones <= esta fecha.
        estado (str|None): filtrar por estado (CERRADO, PENDIENTE, etc.).
                           None o "Todos" = sin filtro.

    Returns:
        (bool, str, int): (éxito, mensaje, cantidad de filas escritas).
    """
    client, email = get_gsheets_client()
    if not client:
        return False, f"No se pudieron generar credenciales. Email: {email}", 0

    try:
        sheet_id = os.getenv("GSHEET_ID")
        sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
        try:
            if "GSHEET_ID" in st.secrets:
                sheet_id = st.secrets["GSHEET_ID"]
            if "GSHEET_NAME" in st.secrets:
                sheet_name = st.secrets["GSHEET_NAME"]
        except Exception:
            pass

        if sheet_id:
            sh = client.open_by_key(sheet_id)
        else:
            sh = client.open(sheet_name)

        logger.info(f"Dux export: Spreadsheet abierto OK (gspread {gspread.__version__})")

        # 0. Limpieza: borrar hoja fantasma "Hoja 6" si existe
        try:
            hoja_basura = sh.worksheet("Hoja 6")
            sh.del_worksheet(hoja_basura)
            logger.info("Dux export: Hoja 'Hoja 6' eliminada")
        except Exception:
            pass

        # 1. Leer RENDICIONES_LOG como lista de dicts
        ws_log = sh.worksheet("RENDICIONES_LOG")
        all_records = ws_log.get_all_records()

        if not all_records:
            return False, "RENDICIONES_LOG está vacía.", 0

        logger.info(f"Dux export: {len(all_records)} filas leídas de RENDICIONES_LOG")

        # 2. Mapear headers de Sheets a claves internas de dux_export
        SHEET_KEY_MAP = {
            "ID Operación": "id_operacion",
            "Fecha": "fecha",
            "Usuario": "usuario",
            "Oficina": "oficina",
            "Número de Carpeta": "numero_carpeta",
            "Tipo de Operación": "tipo_operacion",
            "Cliente": "cliente",
            "Concepto": "concepto",
            "Monto Concepto": "monto_concepto",
            "factura_tipo": "factura_tipo",
            "Código AFIP": "codigo_afip",
            "Sucursal": "sucursal",
            "Número_de_factura": "numero_factura",
            "N°Comprobante": "n_comprobante",
            "Proveedor_Validado": "proveedor_validado",
            "Cuit_Proveedor_AI": "cuit_proveedor",
            "Neto Gravado": "neto_gravado",
            "No Gravado": "no_gravado",
            "IVA 21%": "iva_21",
            "IVA 10.5%": "iva_105",
            "IVA 27%": "iva_27",
            "Perc IVA": "perc_iva",
            "Perc Ganancias": "perc_ganancias",
            "Perc IIBB": "perc_iibb",
            "Jurisdicción": "jurisdiccion",
            "Monto Total Ticket": "monto_total_ticket",
            "Monto a Imputar": "monto_a_imputar",
            "Ticket URL": "ticket_url",
            "Estado": "estado",
            "Clave Maestra": "clave_maestra",
            "Observaciones": "observaciones",
            "Motivo_Rechazo": "motivo_rechazo",
            "Revisado_Por": "revisado_por",
            "Fecha_Revision": "fecha_revision",
        }

        rendiciones = []
        for record in all_records:
            mapped = {}
            for sheet_key, internal_key in SHEET_KEY_MAP.items():
                mapped[internal_key] = record.get(sheet_key, "")
            rendiciones.append(mapped)

        logger.info(f"Dux export: {len(rendiciones)} rendiciones mapeadas a claves internas")

        # 3. Aplicar filtros
        if fecha_desde:
            fecha_desde_str = fecha_desde.isoformat()
            rendiciones = [r for r in rendiciones
                           if str(r.get("fecha", ""))[:10] >= fecha_desde_str]

        if fecha_hasta:
            fecha_hasta_str = fecha_hasta.isoformat()
            rendiciones = [r for r in rendiciones
                           if str(r.get("fecha", ""))[:10] <= fecha_hasta_str]

        if estado and estado != "Todos":
            rendiciones = [r for r in rendiciones
                           if str(r.get("estado", "")).strip().upper() == estado.upper()]

        if not rendiciones:
            return False, "No hay rendiciones que coincidan con los filtros.", 0

        logger.info(f"Dux export: {len(rendiciones)} filas después de filtros")

        # 4. Generar filas ENC/DET
        grupos = agrupar_por_comprobante(rendiciones)
        filas_dux = generar_filas_dux(grupos)

        if not filas_dux:
            return False, "No se generaron filas ENC/DET (sin comprobantes válidos).", 0

        logger.info(f"Dux export: {len(filas_dux)} filas ENC/DET generadas ({len(grupos)} comprobantes)")

        # 5. Crear o limpiar hoja EXPORT_DUX
        hoja_nombres = [ws.title for ws in sh.worksheets()]
        logger.info(f"Dux export: Hojas existentes: {hoja_nombres}")

        if "EXPORT_DUX" in hoja_nombres:
            ws_export = sh.worksheet("EXPORT_DUX")
            ws_export.clear()
            logger.info("Dux export: Hoja EXPORT_DUX encontrada y limpiada")
        else:
            ws_export = sh.add_worksheet(title="EXPORT_DUX", rows=1000, cols=28)
            logger.info("Dux export: Hoja EXPORT_DUX creada (1000 filas x 28 cols)")

        # 6. Asegurar que la hoja tenga suficientes filas
        total_rows = 1 + len(filas_dux)  # 1 header + N data rows
        if ws_export.row_count < total_rows:
            ws_export.resize(rows=total_rows, cols=28)
            logger.info(f"Dux export: Hoja redimensionada a {total_rows} filas")

        # 7. Sanitizar None → "" (gspread no acepta None en update)
        all_data = [DUX_HEADERS] + filas_dux
        sanitized = []
        for row in all_data:
            sanitized.append([v if v is not None else "" for v in row])
        all_data = sanitized

        cell_range = f"A1:AB{total_rows}"

        logger.info(f"Dux export: Escribiendo {len(all_data)} filas en rango {cell_range}")
        logger.info(f"Dux export: Header (fila 1) = {all_data[0][:5]}... ({len(all_data[0])} cols)")
        logger.info(f"Dux export: Primera fila datos = {all_data[1][:5]}...")

        # Usar kwargs para compatibilidad gspread v5/v6 (v6 invirtió el orden de args)
        ws_export.update(range_name=cell_range, values=all_data)

        logger.info(f"Dux export: update() completado OK")

        msg = f"Exportación completada: {len(filas_dux)} filas ENC/DET en EXPORT_DUX"
        logger.info(msg)
        return True, msg, len(filas_dux)

    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        logger.error(f"Dux export error: {err_detail}")
        return False, f"Error: {str(e)}\n\nDetalle:\n{err_detail}", 0
