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

# Dict of Users -> Office (HARDCODED SINGLE SOURCE OF TRUTH)
USUARIOS_DB = {
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

        if sheet_id:
             try:
                 sh = client.open_by_key(sheet_id)
                 logger.info(f"Opened sheet by ID: {sheet_id}")
             except Exception as e:
                 return False, f"Error abriendo por ID '{sheet_id}': {e}"
        else:
             sh = client.open(sheet_name)
             logger.info(f"Opened sheet by name: {sheet_name}")
        
        # 1. DB_PARAMETROS -> Update CONCEPTOS_DB
        try:
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
            
            global CONCEPTOS_DB, CONCEPTOS_OFICINA_DB
            CONCEPTOS_DB.clear()
            CONCEPTOS_DB.update(new_conceptos)
            
            CONCEPTOS_OFICINA_DB.clear()
            CONCEPTOS_OFICINA_DB.update(new_oficinas)
                            
            logger.info("Synced CONCEPTOS_DB from Sheets")
        except Exception as e:
            logger.warning(f"Could not sync DB_PARAMETROS: {type(e).__name__}: {e}")

        # 2. DB_PROVEEDORES -> Update PROVEEDORES_DB
        try:
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

def log_rendicion_to_sheet(payload, ticket_url=""):
    """
    Appends a new row to RENDICIONES_LOG tab with updated columns.
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
        estado_saldo = ""
        if saldo_pendiente < 1000.0 and saldo_pendiente > 0:
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
        
        col_q_neto = payload.get("monto_gravado_calculado", 0) # Q: Neto Gravado
        col_r_no_grav = desglose.get("columna_R_no_gravado", 0)
        col_s_iva21 = desglose.get("columna_S_iva_21", 0)
        col_t_iva105 = desglose.get("columna_T_iva_105", 0)
        col_u_iva27 = desglose.get("columna_U_iva_27", 0)
        col_v_perc_g = desglose.get("columna_V_perc_ganancias", 0)
        col_w_perc_i = desglose.get("columna_W_perc_iibb", 0)
        col_x_juris = desglose.get("columna_X_jurisdiccion_code", "")
        col_y_total = desglose.get("monto_total_columna_Y", monto_ticket) # Use Desglose Total or Fallback

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
            
            # --- NEW AUDITOR COLUMNS (Q-Y) ---
            col_q_neto,                                 # 17 (Q). Neto Gravado
            col_r_no_grav,                              # 18 (R). No Gravado (o Total B/C)
            col_s_iva21,                                # 19 (S). IVA 21%
            col_t_iva105,                               # 20 (T). IVA 10.5%
            col_u_iva27,                                # 21 (U). IVA 27%
            col_v_perc_g,                               # 22 (V). Perc Ganancias
            col_w_perc_i,                               # 23 (W). Perc IIBB
            col_x_juris,                                # 24 (X). Jurisdicción
            col_y_total,                                # 25 (Y). Monto Total Ticket
            
            # --- SHIFTED METADATA (Z+) ---
            monto_imputar,                              # 26. Monto a Imputar (Manual)
            ticket_url,                                 # 27. Ticket URL
            estado_saldo,                               # 28. Estado (Puchito)
            clave_unica                                 # 29. Clave Maestra
        ]
        
        ws_log.append_row(row)
        return True
    except Exception as e:
        logger.error(f"Error logging to sheet: {e}")
        return False
