import streamlit as st
import datetime
import requests
import json
import os
import google.generativeai as genai
from dotenv import load_dotenv

# Import our data module
import data
import notificaciones

# Jurisdictions for perception selectboxes
JURISDICCIONES_ARG = [
    "", "BUENOS AIRES", "CABA", "CATAMARCA", "CHACO", "CHUBUT", "CORDOBA",
    "CORRIENTES", "ENTRE RIOS", "FORMOSA", "JUJUY", "LA PAMPA", "LA RIOJA",
    "MENDOZA", "MISIONES", "NEUQUEN", "RIO NEGRO", "SALTA", "SAN JUAN",
    "SAN LUIS", "SANTA CRUZ", "SANTA FE", "SANTIAGO DEL ESTERO",
    "TIERRA DEL FUEGO", "TUCUMAN", "OTRA",
]

# Load environment variables
load_dotenv()

# Configure page
st.set_page_config(page_title="Gestión y Compensación", page_icon="🧾", layout="centered") 

# ==========================================
# 0. SETUP & HELPER FUNCTIONS
# ==========================================

# Initialize / Sync Data on Startup
if "data_synced" not in st.session_state:
    with st.spinner("Sincronizando parámetros..."):
        success, msg = data.sync_data_from_sheets()
        if success:
            st.toast("✅ Parámetros actualizados desde Google Sheets")
        else:
            sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
            st.error(f"⚠️ Error de Sincronización (Modo Offline): No se pudo abrir '{sheet_name}'. Detalles: {msg}")
            st.info("💡 Verifique que la planilla esté compartida con el email de la Service Account y que los Secretos en Streamlit Cloud sean correctos.")
            st.info("💡 Verifique que la planilla esté compartida con el email de la Service Account y que los Secretos en Streamlit Cloud sean correctos.")
    st.session_state.data_synced = True

# Initialize Session keys for Form Reset if not present
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

if st.session_state.get("needs_reset"):
    # Clear specific widget-bound keys safely at the START of the run
    keys_to_reset = [
        "folder_input", "concept_input", "obs_input", "scanned_data", "desglose_data",
        "manual_cuit", "manual_provider", "manual_tipo", "manual_suc", "manual_num", "manual_total", "manual_neto", "manual_afip",
        "scan_suc_input", "scan_num_input", "scan_tipo_input", "scan_cuit_input", "scan_cuit_cliente_input", "scan_provider_input",
        "manual_cuit_cliente"
    ]
    for k in keys_to_reset:
        if k in st.session_state:
            del st.session_state[k]
    
    # Also clear dynamic monto_imputar keys
    for k in list(st.session_state.keys()):
        if k.startswith("monto_imputar_"):
            del st.session_state[k]
            
    st.session_state.uploader_key += 1
    st.session_state.needs_reset = False # Reset the flag

def configure_genai():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        try:
            if "GOOGLE_API_KEY" in st.secrets:
                api_key = st.secrets["GOOGLE_API_KEY"]
        except Exception:
            pass
    
    if api_key:
        try:
            genai.configure(api_key=api_key)
            return True
        except Exception as e:
            st.error(f"Error config API: {e}")
            return False
    return False

def scan_receipt(image_bytes, mime_type="image/jpeg"):
    import re
    try:
        # Based on check, 2.0-flash is available and supports generateContent
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        prompt = """
        # ROL
        Actúas como un **Auditor Contable Senior experto en normativa AFIP (Argentina)**. Tu objetivo es extraer datos estructurados de comprobantes de gastos para un sistema de rendición automatizado. Tu prioridad es la precisión matemática y la correcta categorización impositiva según el TIPO de comprobante.

        # REGLAS DE NEGOCIO (ESTRICTAS)

        ## 1. DETECCIÓN DE CUITs
        - Las facturas Tipo A y C tienen dos CUITs (Emisor y Receptor).
        - El **CUIT del PROVEEDOR (Emisor)** siempre está en el ENCABEZADO (parte superior). Es el PRIMERO que aparece. → campo `cuit_proveedor`.
        - El **CUIT del Cliente (Receptor)** está más abajo en el comprobante. → campo `cuit_cliente`.
        - Factura B: suele tener solo el CUIT del emisor. `cuit_cliente` = null.
        - Factura C: aplica la misma regla que la A.

        ## 2. DATOS BÁSICOS DEL COMPROBANTE
        - **TIPO:** Identifica la LETRA (A, B, C, M) o "TICKET".
        - **CÓDIGO AFIP:** Busca "COD. XX" (ej: 001, 006, 011). Normalízalo a 3 dígitos.
        - **PUNTO DE VENTA (SUCURSAL):** 4 o 5 dígitos. Si ves `XXXXX-YYYYYYYY`, el `XXXXX` es la sucursal.
        - **Opesa/Combustibles:** NO confundas "Nro. Estación" con el PV.

        ## 3. LÓGICA PARA FACTURA TIPO "A" (Discriminación Obligatoria)
        Debes desglosar cada centavo del ticket en los campos individuales.
        - **neto_gravado:** base imponible.
        - **no_gravado:** montos exentos, impuestos internos (combustibles líquidos, fondo hídrico), cargos que NO son IVA ni percepciones.
        - **exento:** bienes/servicios exentos de IVA si se discriminan.
        - **IVA:** separar por alícuota (21%, 10.5%, 27%).
        - **IMPORTANTE:** NO mezclar Percepción Municipal con No Gravado. La Percepción Municipal va en `perc_municipal`.

        ## 4. CLASIFICACIÓN DE LÍNEAS DE PERCEPCIONES (CRÍTICO)
        Identificá cada línea individualmente. **NO sumes líneas distintas.**

        - Líneas que contengan "PERC IVA" o "PER IVA" o "RG 2408" o "R.G. 2408" → `perc_iva` (un solo valor numérico).
        - Líneas que contengan "PER IB" o "PERC IIBB" o "Per IIBB" o "ING BRUTOS" → entrada en `perc_iibb_lista`. Cada línea es UNA entrada distinta con su jurisdicción.
          Para la jurisdicción, usá esta tabla de códigos:
            CBAD, CBA, CORDOBA → "CORDOBA"
            CABA, CAPFED, CFED → "CABA"
            BSAS, BSA, BUENOSAIRES → "BUENOS AIRES"
            MZA, MENDOZA → "MENDOZA"
            SFE, SANTAFE → "SANTA FE"
            NQN, NEUQUEN → "NEUQUEN"
          Si el código no coincide con ninguno conocido, usá el texto literal en mayúsculas.
        - Líneas que contengan "Per Mun" o "PERC MUN" o "MUNICIPAL" → `perc_municipal` (objeto único, NO array). Si hay más de una, sumalas en el monto pero registrá la jurisdicción de la primera.
        - Líneas que contengan "PERC GCIAS" o "PER GAN" o "RG 830" → `perc_ganancias`.
        - Si el ticket muestra "TOTAL DESCUENTOS" o líneas con valor negativo, ignorarlas.
        - Si una línea tiene formato "PERC X.XX% [BASE] MONTO", el MONTO es el último número de la línea.

        ## 5. LÓGICA PARA FACTURA TIPO "B" o "C" (Agrupación Total)
        - **NUNCA DISCRIMINES IMPUESTOS EN B O C.**
        - Todo el valor del ticket (100%) va al campo `no_gravado`. Todos los demás campos impositivos en 0.

        ## 6. VALIDACIÓN INTERNA ANTES DE DEVOLVER
        Calculá:
        `suma = neto_gravado + no_gravado + exento + iva_21 + iva_10_5 + iva_27 + perc_iva + perc_ganancias + sum(perc_iibb_lista[].monto) + (perc_municipal.monto if perc_municipal else 0)`
        Si `abs(suma - monto_total) > 1`, revisá tu extracción. Si la diferencia persiste, devolvé el JSON con `"warning_total_no_cuadra": true`.

        # FORMATO DE SALIDA (JSON)
        Devuelve ÚNICAMENTE un objeto JSON con esta estructura exacta:

        {
          "tipo_factura": "String (A, B, C, M, TICKET)",
          "codigo_afip": "String (001, 006, etc) o null",
          "fecha": "DD/MM/AAAA",
          "proveedor": "String (Nombre o Razón Social)",
          "cuit_proveedor": "String (11 dígitos sin guiones)",
          "cuit_cliente": "String (11 dígitos sin guiones) o null",
          "sucursal": "String (5 dígitos)",
          "numero_comprobante": "String (8 dígitos)",
          "neto_gravado": Number,
          "no_gravado": Number,
          "exento": Number,
          "iva_21": Number,
          "iva_10_5": Number,
          "iva_27": Number,
          "perc_iva": Number,
          "perc_ganancias": Number,
          "perc_iibb_lista": [
            {"jurisdiccion": "String", "monto": Number}
          ],
          "perc_municipal": {"jurisdiccion": "String", "monto": Number} o null,
          "monto_total": Number,
          "warning_total_no_cuadra": Boolean (true si la suma no cuadra)
        }

        ## REGLAS DE SEGURIDAD (LEGIBILIDAD)
        - **SI EL TICKET ES ILEGIBLE, ESTÁ BORROSO O CORTADO:** No intentes adivinar datos.
        - Devuelve `null` en los campos que no puedas leer con certeza absoluta (especialmente CUITs y Montos).
        - El sistema detectará los `null` y pedirá carga manual al usuario.
        - Prioriza siempre la precisión sobre la inferencia.
        """
        
        image_parts = [{"mime_type": mime_type, "data": image_bytes}]
        
        # ERROR HANDLING 429: Retry Logic with Exponential Backoff
        import time
        max_retries = 3
        retry_delay = 2 # Starting delay in seconds
        
        for attempt in range(max_retries):
            try:
                response = model.generate_content([prompt, image_parts[0]])
                break # Success!
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "ResourceExhausted" in error_str:
                    if attempt < max_retries - 1:
                        st.toast(f"⏳ Límite de carga excedido. Reintentando en {retry_delay}s... (Intento {attempt+1}/{max_retries})")
                        time.sleep(retry_delay)
                        retry_delay *= 2 # Double the delay for next time
                        continue
                raise e # Re-raise if not 429 or max retries reached
        
        if not response or not response.text:
             # Check for safety blocks if text is empty
             if response and response.candidates and response.candidates[0].finish_reason:
                  return f"Error: La IA bloqueó la respuesta (Razón: {response.candidates[0].finish_reason})"
             return "Error: La IA devolvió una respuesta vacía."
             
        text = response.text
        
        # Robust JSON extraction using Regex (Simple block finder compatible with Python 're')
        json_match = re.search(r'(\{.*\})', text, re.DOTALL)
        if json_match:
            try:
                json_text = json_match.group(1)
                return json.loads(json_text)
            except:
                pass
        
        # Fallback to manual stripping if regex fails or JSON is malformed
        text = text.replace("```json", "").replace("```", "").strip()
        if not text:
             return "Error: No se encontró JSON en la respuesta de la IA."
             
        return json.loads(text)
    except Exception as e:
        return f"Error details: {str(e)}"

# ==========================================
# MAIN LAYOUT - SINGLE COLUMN LINEAR FLOW
# ==========================================

st.title("Sistema de Gestión y Compensación de Gastos")

# --- CARD 1: DATOS DEL OPERADOR ---
with st.container(border=True):
    st.subheader("👤 Datos del Operador")
    
    col_op1, col_op2 = st.columns(2)
    with col_op1:
        expense_date = st.date_input("Fecha", datetime.date.today())
    
    with col_op2:
        users_list = sorted(list(data.USUARIOS_DB.keys()))
        selected_user = st.selectbox("Usuario", users_list, index=None, placeholder="Seleccionar...")
    
    # Office logic
    office = ""
    if selected_user:
        office = data.USUARIOS_DB.get(selected_user, "---")
    
    st.text_input("Oficina", value=office, disabled=True)


# --- CARD 2: DETALLES DE OPERACIÓN & IMPUTACIÓN ---
with st.container(border=True):
    st.subheader("📝 Imputación de Gastos")
    
    # Folder Number is strictly required now. Supports multiple (comma separated)
    folder_number = st.text_input("📂 Número de Carpeta (Obligatorio)", placeholder="Ej: IMP-2024-001, EXP-2024-050 (Separar con coma para prorrateo)", key="folder_input")
    
    c1, c2 = st.columns(2)
    with c1:
        # Use fixed operations from data module
        op_type = st.selectbox("Tipo de Operación", data.OPERACIONES_DB)
    with c2:
        client = st.selectbox("Cliente", data.CLIENTES_DB, index=None, placeholder="Buscar Cliente...")
    
    st.markdown("### Concepto")
    # Filter concepts by Office (uses composite key mapping)
    if office:
        concepts_list = data.get_conceptos_para_oficina(office)
        st.caption(f"🔍 Mostrando {len(concepts_list)} conceptos para oficina: **{office}**")
    else:
        concepts_list = sorted(list(data.CONCEPTOS_DB.keys()))
        st.caption("🔍 Mostrando todos los conceptos (Sin filtro de oficina)")

    selected_concept = st.selectbox("Seleccionar Concepto", concepts_list, index=None, label_visibility="collapsed", placeholder="Escribe para buscar...", key="concept_input")

    # Auto-fill logic (uses office-aware lookup)
    suggested_amount_concept = 0.0
    if selected_concept:
        suggested_amount_concept = data.get_monto_sugerido(selected_concept, office)
    
    # User Input for IMPUTATION (Monto a Imputar)
    monto_imputar = st.number_input("💵 Monto a Imputar (Usuario)",
                                  value=suggested_amount_concept if suggested_amount_concept > 0 else 0.0,
                                  step=100.0, format="%.2f",
                                  help="El monto que desea asignar a esta carpeta. Puede diferir del ticket.",
                                  key=f"monto_imputar_{selected_concept}")

    # --- EXCESS DETECTION ---
    excede_sugerido = False
    confirma_exceso = False
    if suggested_amount_concept > 0 and monto_imputar > suggested_amount_concept:
        excede_sugerido = True
        diff_exceso = monto_imputar - suggested_amount_concept
        pct_exceso = (diff_exceso / suggested_amount_concept) * 100
        st.warning(
            f"El monto ingresado (${monto_imputar:,.2f}) supera el monto sugerido "
            f"(${suggested_amount_concept:,.2f}) en **${diff_exceso:,.2f}** ({pct_exceso:.1f}%). "
            f"La rendición quedará sujeta a revisión."
        )
        confirma_exceso = st.checkbox(
            "Confirmo que esta rendición excede el monto sugerido y quedará sujeta a revisión",
            key="confirma_exceso"
        )

    # New Field: Observations (Column AD)
    observaciones = st.text_area("📝 Observaciones (Opcional)", placeholder="Detalles adicionales, número de guía, etc...", height=80, key="obs_input")


# --- CARD 3: COMPROBANTE & IA ---
with st.container(border=True):
    st.subheader("📸 Comprobante (Opcional)")
    
    tab_cam, tab_upload = st.tabs(["📷 Cámara", "📁 Subir"])
    
    final_image_bytes = None
    final_mime_type = "image/jpeg" # Default
    
    with tab_cam:
        cam_input = st.camera_input("Tomar foto")
        if cam_input: 
            final_image_bytes = cam_input.getvalue()
            final_mime_type = "image/jpeg"
            
    with tab_upload:
        file_input = st.file_uploader("Seleccionar archivo", type=["jpg", "png", "jpeg", "pdf"], key=f"uploader_{st.session_state.uploader_key}")
        if file_input: 
            final_image_bytes = file_input.getvalue()
            final_mime_type = file_input.type # Dynamically get mime type (e.g. application/pdf)

    if final_image_bytes:
        if st.button("✨ Escanear con IA", type="primary", use_container_width=True):
            if configure_genai():
                with st.status("🤖 Procesando comprobante...", expanded=True) as status:
                    st.write(f"Conectando con Gemini A ({final_mime_type})...")
                    scan_result = scan_receipt(final_image_bytes, final_mime_type)
                    
                    if isinstance(scan_result, dict):
                        st.write("Analizando datos extraídos...")
                        st.session_state.scanned_data = scan_result
                        # Initialize correction keys so they "stick"
                        st.session_state.scan_suc_input = str(scan_result.get("sucursal") or "").replace("-","")
                        st.session_state.scan_num_input = str(scan_result.get("numero_comprobante") or "").replace("-","")
                        st.session_state.scan_tipo_input = str(scan_result.get("tipo_factura") or "C").upper().strip()
                        if st.session_state.scan_tipo_input not in ["A", "B", "C", "M", "Ticket"]:
                            st.session_state.scan_tipo_input = "C"
                        
                        st.session_state.scan_cuit_input = str(scan_result.get("cuit_proveedor") or scan_result.get("cuit") or "")
                        st.session_state.scan_cuit_cliente_input = str(scan_result.get("cuit_cliente") or "")
                        st.session_state.scan_provider_input = str(scan_result.get("proveedor") or "")
                        
                        status.update(label="✅ Escaneo completado!", state="complete", expanded=False)
                    else:
                        st.error(f"Error técnico: {scan_result}")
                        status.update(label="❌ Error en el escaneo", state="error")
            else:
                st.error("Error de configuración API Key")
    
    # --- MANUAL MODE (Discreet) ---
    st.markdown("---")
    modo_manual = st.checkbox("⌨️ Cargar sin comprobante / Corregir", value=False, help="Habilita la carga manual si no tienes un comprobante para escanear.")

# --- VALIDATION RESULT SECTION ---

# Defaults
default_cuit = ""
default_provider = ""
default_afip = ""
monto_ticket_total = 0.0  # What AI sees on the paper
monto_neto = 0.0

monto_neto = 0.0

# Logic: Show AI section if scanned AND (Successful OR Manual Mode is ON for correction)
if "scanned_data" in st.session_state and final_image_bytes:
    with st.container(border=True):
        st.subheader("🔍 Datos del Ticket")
        
        data_ia = st.session_state.scanned_data
        
        # --- PARSING AND RESTORING DEFAULTS ---
        default_cuit = str(data_ia.get("cuit") or "")
        default_provider = str(data_ia.get("proveedor") or "")
        default_tipo = str(data_ia.get("tipo_factura") or "C").upper().strip()
        default_suc = str(data_ia.get("sucursal") or "").replace("-","")
        default_num = str(data_ia.get("numero_comprobante") or "").replace("-","")
        default_afip = str(data_ia.get("codigo_afip") or "")

        # New Auditor Fields — parse from flat JSON (new format) or nested desglose (legacy)
        try:
            monto_ticket_total = float(data_ia.get("monto_total") or data_ia.get("monto_total_columna_Z") or 0.0)
            monto_neto = float(data_ia.get("neto_gravado") or 0.0)

            # Build desglose from flat fields (new Gemini format)
            if "neto_gravado" in data_ia and "desglose" not in data_ia:
                # New format — build compatible desglose for backward compat
                iibb_lista = data_ia.get("perc_iibb_lista") or []
                perc_muni = data_ia.get("perc_municipal") or {}
                desglose = {
                    "neto_gravado_aux": monto_neto,
                    "columna_R_no_gravado": float(data_ia.get("no_gravado") or 0),
                    "columna_S_iva_21": float(data_ia.get("iva_21") or 0),
                    "columna_T_iva_105": float(data_ia.get("iva_10_5") or 0),
                    "columna_U_iva_27": float(data_ia.get("iva_27") or 0),
                    "columna_V_perc_iva": float(data_ia.get("perc_iva") or 0),
                    "columna_W_perc_ganancias": float(data_ia.get("perc_ganancias") or 0),
                    "columna_X_perc_iibb": float(iibb_lista[0]["monto"]) if iibb_lista else 0,
                    "columna_Y_jurisdiccion_code": iibb_lista[0].get("jurisdiccion", "") if iibb_lista else "",
                    "monto_total_columna_Z": monto_ticket_total,
                }
                # Store extended perception data in session for the form
                st.session_state.perc_iibb_lista = iibb_lista
                st.session_state.perc_municipal = perc_muni
            else:
                # Legacy format
                desglose = data_ia.get("desglose", {})
                monto_neto = float(desglose.get("neto_gravado_aux") or 0.0)
                st.session_state.perc_iibb_lista = []
                st.session_state.perc_municipal = {}

            st.session_state.desglose_data = desglose

            # Validation Check
            if data_ia.get("warning_total_no_cuadra"):
                st.warning("⚠️ Alerta Auditoría: Los importes extraídos no suman el total del ticket.")
            else:
                val_check = data_ia.get("validacion_check", "N/A")
                if val_check == "OK":
                    st.info("✅ Auditoría: Suma de control OK")
                elif val_check != "N/A":
                    st.warning(f"⚠️ Alerta Auditoría: {val_check}")

        except Exception as e:
            st.error(f"Error parsing AI data: {e}")
            monto_ticket_total = 0.0
            monto_neto = 0.0
            
        # Factura A Rule: Use Net Amount for Imputation Base
        if default_tipo == "A" and monto_neto > 0:
            base_imputacion = monto_neto
            st.info(f"ℹ️ Factura A detectada: Base de imputación sugerida ${monto_neto:,.2f} (Neto)")
        else:
            base_imputacion = monto_ticket_total

        # Ensure session state keys exist (in case of browser refresh)
        if "scan_tipo_input" not in st.session_state: st.session_state.scan_tipo_input = default_tipo
        if "scan_suc_input" not in st.session_state: st.session_state.scan_suc_input = default_suc
        if "scan_num_input" not in st.session_state: st.session_state.scan_num_input = default_num
        if "scan_cuit_input" not in st.session_state: st.session_state.scan_cuit_input = default_cuit
        if "scan_provider_input" not in st.session_state: st.session_state.scan_provider_input = default_provider

        # --- KEY METRICS (Always visible if scanned) ---
        c1, c2 = st.columns(2)
        c1.metric("CUIT Detectado", default_cuit if default_cuit else "???")
        c2.metric("Monto Ticket", f"${monto_ticket_total:,.2f}")
        
        # Determine if we should show manual correction fields
        scan_incomplete = not default_cuit or monto_ticket_total <= 0
        
        if scan_incomplete:
             st.warning("⚠️ **Escaneo Incompleto o Ilegible.** Por favor complete o corrija los datos manualmente.")

        # --- CUIT VALIDATION ENGINE (AI + Manual) ---
        st.markdown("### Validación de Proveedor")
        # Use a container to group validation UI
        v_col1, v_col2 = st.columns([1, 2])
        
        with v_col1:
            # The manual input defaults to what AI found, but allows correction
            cuit_input = st.text_input("CUIT del Proveedor", key="scan_cuit_input", placeholder="Ej: 30123456789")
        
        # Real-time search in DB based on manual OR ai input
        is_validated = False
        validated_name = ""
        
        if cuit_input:
            clean_input = cuit_input.replace("-", "").replace(" ", "")
            for db_cuit, db_name in data.PROVEEDORES_DB.items():
                if db_cuit == cuit_input or db_cuit.replace("-", "") == clean_input:
                    is_validated = True
                    validated_name = db_name
                    # Standardize format
                    cuit_input = db_cuit
                    break
        
        provider_status = "none"
        with v_col2:
            if is_validated:
                st.success(f"✅ **Validado:** {validated_name}")
                provider_input = validated_name
                provider_status = "valid"
            elif cuit_input:
                st.warning("🔍 Proveedor no encontrado (Pendiente de Alta)")
                provider_input = st.text_input("Razón Social (Manual)", key="scan_provider_input")
                provider_status = "pending_approval"
            else:
                provider_input = ""
                st.info("Ingrese CUIT para validar")

        # Expanded Invoice Details (Fabian's Rules)
        st.markdown("---")
        
        # Ensure session state keys exist (in case of browser refresh)
        if "scan_tipo_input" not in st.session_state: st.session_state.scan_tipo_input = default_tipo
        if "scan_suc_input" not in st.session_state: st.session_state.scan_suc_input = default_suc
        if "scan_num_input" not in st.session_state: st.session_state.scan_num_input = default_num

        c1, c2, c3 = st.columns(3)
        with c1:
            tipo_fact_input = st.selectbox("Tipo", ["A", "B", "C", "M", "Ticket"], key="scan_tipo_input")
        with c2:
            pto_vta_input = st.text_input("Sucursal (5)", key="scan_suc_input", max_chars=5, help="Debe ser de 5 dígitos (ej: 00001)")
            if pto_vta_input and not pto_vta_input.isdigit():
                st.caption("⚠️ Debe ser solo números")
            elif pto_vta_input and len(pto_vta_input) < 5:
                st.caption("ℹ️ Se completará con ceros a la izquierda (relleno a 5)")
        with c3:
            num_comp_input = st.text_input("Número (8)", key="scan_num_input", max_chars=8, help="Número de la factura (ej: 00012345)")
            
        afip_code_input = st.text_input("Código AFIP", value=default_afip)

        # CUIT del Cliente
        if "scan_cuit_cliente_input" not in st.session_state:
            st.session_state.scan_cuit_cliente_input = ""
        cuit_cliente_input = st.text_input(
            "CUIT del Cliente (Receptor)", key="scan_cuit_cliente_input",
            placeholder="11 dígitos, solo si aparece en el comprobante",
            help="Si es Expoconsult (30570717630), la factura se exporta como PROPIA en DUX."
        )

        # --- DETALLE IMPOSITIVO (editable, with real-time validation) ---
        st.markdown("### Detalle impositivo (revisá los valores antes de guardar)")

        # Get defaults from Gemini parse or zeros
        _des = st.session_state.get("desglose_data", {})
        _iibb_list = st.session_state.get("perc_iibb_lista", [])
        _muni = st.session_state.get("perc_municipal", {})
        _is_a = tipo_fact_input == "A"

        # Row 1: Neto + No Gravado + Exento
        dc1, dc2, dc3 = st.columns(3)
        with dc1:
            inp_neto = st.number_input("Neto Gravado", value=float(monto_neto if _is_a else 0), step=100.0, format="%.2f", key="imp_neto")
        with dc2:
            inp_no_grav = st.number_input("No Gravado", value=float(_des.get("columna_R_no_gravado", monto_ticket_total if not _is_a else 0) or 0), step=100.0, format="%.2f", key="imp_no_grav")
        with dc3:
            inp_exento = st.number_input("Exento", value=0.0, step=100.0, format="%.2f", key="imp_exento")

        # Row 2: IVA rates
        dc4, dc5, dc6 = st.columns(3)
        with dc4:
            inp_iva21 = st.number_input("IVA 21%", value=float(_des.get("columna_S_iva_21", 0) or 0), step=100.0, format="%.2f", key="imp_iva21")
        with dc5:
            inp_iva105 = st.number_input("IVA 10.5%", value=float(_des.get("columna_T_iva_105", 0) or 0), step=100.0, format="%.2f", key="imp_iva105")
        with dc6:
            inp_iva27 = st.number_input("IVA 27%", value=float(_des.get("columna_U_iva_27", 0) or 0), step=100.0, format="%.2f", key="imp_iva27")

        # Row 3: Perc IVA + Ganancias
        dc7, dc8 = st.columns(2)
        with dc7:
            inp_perc_iva = st.number_input("Perc IVA", value=float(_des.get("columna_V_perc_iva", 0) or 0), step=100.0, format="%.2f", key="imp_perc_iva")
        with dc8:
            inp_perc_gcias = st.number_input("Perc Ganancias", value=float(_des.get("columna_W_perc_ganancias", 0) or 0), step=100.0, format="%.2f", key="imp_perc_gcias")

        # Row 4: IIBB 1
        st.markdown("**Percepción IIBB 1**")
        ic1, ic2 = st.columns([1, 1])
        _iibb1_default = float(_iibb_list[0]["monto"]) if _iibb_list else float(_des.get("columna_X_perc_iibb", 0) or 0)
        _jur1_default = (_iibb_list[0].get("jurisdiccion", "") if _iibb_list else str(_des.get("columna_Y_jurisdiccion_code", "") or "")).upper()
        with ic1:
            inp_iibb1 = st.number_input("Monto IIBB 1", value=_iibb1_default, step=100.0, format="%.2f", key="imp_iibb1")
        with ic2:
            _jur1_idx = JURISDICCIONES_ARG.index(_jur1_default) if _jur1_default in JURISDICCIONES_ARG else 0
            inp_jur1 = st.selectbox("Jurisdicción IIBB 1", JURISDICCIONES_ARG, index=_jur1_idx, key="imp_jur1")
            if inp_jur1 == "OTRA":
                inp_jur1 = st.text_input("Jurisdicción (manual)", key="imp_jur1_otra")

        # Row 5: IIBB 2 (show if there's data or IIBB1 > 0)
        _iibb2_default = float(_iibb_list[1]["monto"]) if len(_iibb_list) > 1 else 0.0
        _jur2_default = (_iibb_list[1].get("jurisdiccion", "") if len(_iibb_list) > 1 else "").upper()
        if _iibb2_default > 0 or inp_iibb1 > 0:
            st.markdown("**Percepción IIBB 2**")
            ic3, ic4 = st.columns([1, 1])
            with ic3:
                inp_iibb2 = st.number_input("Monto IIBB 2", value=_iibb2_default, step=100.0, format="%.2f", key="imp_iibb2")
            with ic4:
                _jur2_idx = JURISDICCIONES_ARG.index(_jur2_default) if _jur2_default in JURISDICCIONES_ARG else 0
                inp_jur2 = st.selectbox("Jurisdicción IIBB 2", JURISDICCIONES_ARG, index=_jur2_idx, key="imp_jur2")
                if inp_jur2 == "OTRA":
                    inp_jur2 = st.text_input("Jurisdicción 2 (manual)", key="imp_jur2_otra")
        else:
            inp_iibb2 = 0.0
            inp_jur2 = ""

        # 3+ IIBB warning
        if len(_iibb_list) > 2:
            extra_total = sum(e["monto"] for e in _iibb_list[2:])
            st.warning(
                f"La factura tiene {len(_iibb_list)} jurisdicciones IIBB. "
                f"Solo se cargarán las dos de mayor monto. Las restantes suman ${extra_total:,.2f}. "
                f"Revisá manualmente o cargá las demás como rendición separada."
            )

        # Row 6: Perc Municipal (show if there's data)
        _muni_monto = float(_muni.get("monto", 0) or 0) if _muni else 0.0
        _muni_jur = str(_muni.get("jurisdiccion", "") or "").upper() if _muni else ""
        if _muni_monto > 0:
            st.markdown("**Percepción Municipal**")
            mc1, mc2 = st.columns([1, 1])
            with mc1:
                inp_perc_muni = st.number_input("Monto Municipal", value=_muni_monto, step=100.0, format="%.2f", key="imp_perc_muni")
            with mc2:
                _muni_idx = JURISDICCIONES_ARG.index(_muni_jur) if _muni_jur in JURISDICCIONES_ARG else 0
                inp_jur_muni = st.selectbox("Jurisdicción Municipal", JURISDICCIONES_ARG, index=_muni_idx, key="imp_jur_muni")
                if inp_jur_muni == "OTRA":
                    inp_jur_muni = st.text_input("Jurisdicción municipal (manual)", key="imp_jur_muni_otra")
        else:
            inp_perc_muni = st.number_input("Perc Municipal", value=0.0, step=100.0, format="%.2f", key="imp_perc_muni")
            inp_jur_muni = ""

        # Real-time sum validation
        suma_componentes = (inp_neto + inp_no_grav + inp_exento + inp_iva21 + inp_iva105 + inp_iva27
                           + inp_perc_iva + inp_perc_gcias + inp_iibb1 + inp_iibb2 + inp_perc_muni)
        diff_check = abs(monto_ticket_total - suma_componentes)

        st.markdown(f"**Suma de componentes:** ${suma_componentes:,.2f}  |  **Monto total ticket:** ${monto_ticket_total:,.2f}  |  **Diferencia:** ${diff_check:,.2f}")
        if diff_check <= 1:
            st.success("Los importes cuadran")
        else:
            st.warning(f"Los importes desglosados no suman el total. Diferencia: ${diff_check:,.2f}. Revisá los valores.")

        # Store form values for payload (override desglose from Gemini)
        monto_neto_input = inp_neto

# --- MANUAL ENTRY FALLBACK (Only if NO scan AND Toggle is ON) ---
elif modo_manual:
    # Manual mode defaults (When NO scan exists)
    cuit_input = ""
    provider_input = ""
    afip_code_input = ""
    monto_ticket_total = 0.0
    
    st.subheader("⌨️ Carga Manual (Sin Comprobante)")
    
    col_m1, col_m2 = st.columns([1, 2])
    with col_m1:
        cuit_input = st.text_input("CUIT del Proveedor", placeholder="Ej: 30123456789", key="manual_cuit")
    
    # Real-time search
    is_validated = False
    validated_name = ""
    if cuit_input:
        clean_input = cuit_input.replace("-", "").replace(" ", "")
        for db_cuit, db_name in data.PROVEEDORES_DB.items():
            if db_cuit == cuit_input or db_cuit.replace("-", "") == clean_input:
                is_validated = True
                validated_name = db_name
                cuit_input = db_cuit
                break

    provider_status = "none"
    with col_m2:
        if is_validated:
            st.success(f"✅ **Validado:** {validated_name}")
            provider_input = validated_name
            provider_status = "valid"
        elif cuit_input:
            st.warning("🔍 Proveedor no encontrado")
            provider_input = st.text_input("Razón Social", placeholder="Nombre del proveedor", key="manual_provider")
            provider_status = "pending_approval"
        else:
            provider_input = ""
            st.info("Ingrese CUIT para validar")

    st.markdown("---")
    c1, m_c2, m_c3 = st.columns(3)
    with c1:
        tipo_fact_input = st.selectbox("Tipo", ["A", "B", "C", "M", "Ticket"], index=2, key="manual_tipo")
    with m_c2:
        pto_vta_input = st.text_input("Sucursal (5)", max_chars=5, key="manual_suc")
    with m_c3:
        num_comp_input = st.text_input("Número (8)", max_chars=8, key="manual_num")
    
    # Add Monto Total for manual mode
    monto_ticket_total = st.number_input("Monto Total del Ticket", value=0.0, step=100.0, format="%.2f", key="manual_total")
    
    monto_neto_input = 0.0
    if tipo_fact_input == "A":
        monto_neto_input = st.number_input("Monto Neto Gravado", value=0.0, key="manual_neto")
        
    afip_code_input = st.text_input("Código AFIP", key="manual_afip")
    cuit_cliente_input = st.text_input(
        "CUIT del Cliente (Receptor)",
        key="manual_cuit_cliente",
        placeholder="11 dígitos (solo para facturas emitidas a Expoconsult)"
    )
    # Initialize perception vars for manual mode
    inp_neto = monto_neto_input
    inp_no_grav = monto_ticket_total if tipo_fact_input in ["B", "C", "Ticket"] else 0.0
    inp_exento = 0.0
    inp_iva21 = 0.0; inp_iva105 = 0.0; inp_iva27 = 0.0
    inp_perc_iva = 0.0; inp_perc_gcias = 0.0
    inp_iibb1 = 0.0; inp_jur1 = ""
    inp_iibb2 = 0.0; inp_jur2 = ""
    inp_perc_muni = 0.0; inp_jur_muni = ""
else:
    # No scan and Toggle OFF -> Initialize variables to avoid NameError
    cuit_input = ""
    provider_input = ""
    tipo_fact_input = "C"
    pto_vta_input = ""
    num_comp_input = ""
    monto_ticket_total = 0.0
    monto_neto_input = 0.0
    afip_code_input = ""
    cuit_cliente_input = ""
    provider_status = "none"
    inp_neto = 0.0; inp_no_grav = 0.0; inp_exento = 0.0
    inp_iva21 = 0.0; inp_iva105 = 0.0; inp_iva27 = 0.0
    inp_perc_iva = 0.0; inp_perc_gcias = 0.0
    inp_iibb1 = 0.0; inp_jur1 = ""
    inp_iibb2 = 0.0; inp_jur2 = ""
    inp_perc_muni = 0.0; inp_jur_muni = ""


# --- LOGIC: BALANCES & FLAGS ---
# Logic: If no receipt amount detected but user imputes amount -> affect balance
afectar_a_saldo = False
if monto_ticket_total == 0 and monto_imputar > 0:
    afectar_a_saldo = True


# --- FOOTER: ACCIÓN FINAL ---
st.markdown("<br>", unsafe_allow_html=True)

# --- DUPLICATE CHECK ---
is_duplicate = False
if cuit_input and num_comp_input:
    is_duplicate = data.check_duplicate_comprobante(cuit_input, pto_vta_input, num_comp_input)
    if is_duplicate:
        st.warning("⚠️ Ya existe un comprobante cargado con el mismo CUIT y Número de Comprobante. No se permite duplicar.")

# Disable save if: duplicate, or excess not confirmed
save_disabled = is_duplicate or (excede_sugerido and not confirma_exceso)

if st.button("💾 Guardar Rendición", type="primary", use_container_width=True, disabled=save_disabled):
    # Validation
    if not selected_user or not folder_number or not selected_concept:
        st.error("⚠️ Faltan datos obligatorios: Usuario, Carpeta o Concepto.")
    elif monto_imputar <= 0 and monto_ticket_total <= 0:
         st.error("⚠️ Debe haber un monto a imputar o un ticket válido.")
    else:
        # $1000 Closing Rule (Puchito)
        diff = abs(monto_ticket_total - monto_imputar)
        if 0 < diff < 1000.0:
            st.info("💰 Diferencia menor a $1000: Se marcará como 'LISTA PARA AJUSTE'")

        # Logic for 'Gravado' (Column 16)
        # Rule: If A -> Net. If B -> 0. Else -> 0.
        monto_gravado_total_base = 0.0
        if tipo_fact_input == "A":
            monto_gravado_total_base = monto_neto_input
        
        # Logic for Provider Validation (Column 15)
        prov_valid_txt = "Sí" if provider_status == "valid" else "No"
        
        # Auditor Breakdown — ALWAYS from form values (operator corrections override Gemini)
        desglose_base = {
            "neto_gravado_aux": inp_neto,
            "columna_R_no_gravado": inp_no_grav,
            "columna_S_iva_21": inp_iva21,
            "columna_T_iva_105": inp_iva105,
            "columna_U_iva_27": inp_iva27,
            "columna_V_perc_iva": inp_perc_iva,
            "columna_W_perc_ganancias": inp_perc_gcias,
            "columna_X_perc_iibb": inp_iibb1,
            "columna_Y_jurisdiccion_code": inp_jur1,
            "monto_total_columna_Z": monto_ticket_total,
        }

        # 1. Upload to Drive (ONCE)
        ticket_link = ""
        uploaded_once = False
        if final_image_bytes:
            with st.spinner("Subiendo Ticket a Drive..."):
                ext = "pdf" if "pdf" in final_mime_type else "jpg"
                fname = f"TICKET_{cuit_input}_{num_comp_input}.{ext}"
                link, file_id, error_msg = data.upload_receipt_to_drive(final_image_bytes, fname, final_mime_type)
                if link:
                    ticket_link = link
                    uploaded_once = True
                    st.toast("✅ Archivo subido a Drive")
                else:
                    st.error(f"Error subiendo archivo a Drive: {error_msg}")

        # Original amounts (before proration) for CONTROL_SALDOS
        monto_ticket_total_original = monto_ticket_total
        monto_neto_original = monto_gravado_total_base
        no_gravado_original = desglose_base.get("columna_R_no_gravado", 0.0)

        # 2. PRORATION LOGIC & SAVE LOOP
        folders = [f.strip() for f in folder_number.split(",") if f.strip()]
        import math
        N = len(folders)
        
        success_count = 0
        
        progress_bar = st.progress(0)
        
        for idx, folder_code in enumerate(folders):
             # Calculate Prorated Amounts
             # We use simple float division.
             p_monto_ticket = monto_ticket_total / N
             p_monto_imputar = monto_imputar / N
             p_monto_sugerido = suggested_amount_concept / N
             p_monto_gravado = monto_gravado_total_base / N
             
             # Prorate Breakdown (Desglose)
             p_desglose = {}
             for k, v in desglose_base.items():
                 if isinstance(v, (int, float)):
                     p_desglose[k] = v / N
                 else:
                     p_desglose[k] = v # Keep strings as is (though desglose usually only has numbers/nulls)

             payload = {
                "fecha": expense_date.isoformat(),
                "usuario": selected_user,
                "oficina": office,
                "numero_carpeta": folder_code, # Unique per row
                "tipo_operacion": op_type,
                "cliente": client or "Sin Cliente",
                "concepto": selected_concept,
                "monto_sugerido_concepto": p_monto_sugerido, # Prorated
                
                "tipo_factura": tipo_fact_input,
                "codigo_afip": afip_code_input,
                "sucursal_factura": pto_vta_input,
                "numero_factura": num_comp_input,
                
                "proveedor_validado_txt": prov_valid_txt,
                "proveedor_cuit": cuit_input,
                "proveedor_nombre": provider_input,
                "cuit_cliente": cuit_cliente_input,
                
                "monto_gravado_calculado": p_monto_gravado, # Prorated
                "monto_ticket_total": p_monto_ticket,       # Prorated
                "monto_a_imputar": p_monto_imputar,         # Prorated
                
                "auditor_desglose": p_desglose,             # Prorated
                "observaciones": observaciones,

                # Perception fields (prorated) for new columns AJ-AM
                "perc_iibb_1": p_desglose.get("columna_X_perc_iibb", 0),
                "jurisdiccion_iibb_1": p_desglose.get("columna_Y_jurisdiccion_code", ""),
                "perc_iibb_2": inp_iibb2 / N,
                "jurisdiccion_iibb_2": inp_jur2,
                "perc_municipal": inp_perc_muni / N,
                "jurisdiccion_municipal": inp_jur_muni,

                # Original amounts (sin prorratear) for CONTROL_SALDOS
                "monto_ticket_total_original": monto_ticket_total_original,
                "monto_neto_original": monto_neto_original,
                "no_gravado_original": no_gravado_original
            }
             
             # Determine estado override for excess
             estado_ov = "PENDIENTE REVISIÓN" if excede_sugerido else None

             # Log to GSheets
             try:
                 if data.log_rendicion_to_sheet(payload, ticket_link, estado_override=estado_ov):
                     success_count += 1
                     try:
                         data.actualizar_control_saldos(payload)
                     except Exception as e:
                         st.toast(f"⚠️ Error actualizando saldos: {e}")
             except Exception as e:
                 st.error(f"Error guardando carpeta {folder_code}: {e}")

             progress_bar.progress((idx + 1) / N)

        if success_count == N:
            if excede_sugerido:
                st.success(
                    f"Rendición guardada en {N} carpeta(s) con estado **PENDIENTE REVISIÓN**. "
                    f"Los autorizantes serán notificados."
                )
            else:
                st.toast(f"Rendición guardada exitosamente en {N} carpetas.")

            # Send email notification if excess (idempotent via session flag)
            # Use folder+concept+amount as stable key so F5 doesn't re-send
            mail_key = f"_mail_sent_{folder_number}_{selected_concept}_{monto_imputar}"
            if excede_sugerido and mail_key not in st.session_state:
                st.session_state[mail_key] = True
                try:
                    gsheets_client, _ = data.get_gsheets_client()
                    sheet_id = os.getenv("GSHEET_ID", "")
                    try:
                        if "GSHEET_ID" in st.secrets:
                            sheet_id = st.secrets["GSHEET_ID"]
                    except Exception:
                        pass
                    if gsheets_client and sheet_id:
                        # Use last payload (representative for the mail)
                        mail_ok, mail_msg = notificaciones.enviar_alerta_exceso(
                            payload, ticket_link, gsheets_client, sheet_id
                        )
                        if not mail_ok:
                            st.warning(
                                "La rendición se guardó. No se pudo enviar la notificación "
                                f"automática, contactá a administración. ({mail_msg})"
                            )
                except Exception as e:
                    st.warning(
                        "La rendición se guardó. No se pudo enviar la notificación "
                        f"automática, contactá a administración. ({e})"
                    )

            # Set reset flag for NEXT run
            st.session_state.needs_reset = True

            import time
            time.sleep(1.5 if excede_sugerido else 1.0)
            st.rerun()
            
        else:
            st.warning(f"⚠️ Se guardaron {success_count} de {N} carpetas. Revise la consola.")


# ==========================================
# ADMINISTRACIÓN — EXPORTACIÓN DUX
# ==========================================

with st.expander("⚙️ Administración (Exportación Dux)", expanded=False):
    ADMIN_KEY = "expoconsult2026"
    admin_input = st.text_input("Clave Admin", type="password", key="admin_key_input")

    if admin_input == ADMIN_KEY:
        st.success("Acceso autorizado")

        # Rango de fechas: default primer y último día del mes actual
        today = datetime.date.today()
        first_day = today.replace(day=1)
        if today.month == 12:
            last_day = today.replace(day=31)
        else:
            last_day = today.replace(year=today.year if today.month < 12 else today.year + 1,
                                     month=today.month + 1, day=1) - datetime.timedelta(days=1)

        # Date cutoff from CONFIG_EMPRESA
        fecha_inicio_dux = data.get_fecha_inicio_export_dux()
        if fecha_inicio_dux:
            st.caption(f"Cutoff DUX: solo rendiciones con fecha >= {fecha_inicio_dux}")
        else:
            st.error("Configurar FECHA_INICIO_EXPORT_DUX en CONFIG_EMPRESA antes del primer export.")
            fecha_inicio_dux = ""  # Will block export via validation

        col_f1, col_f2 = st.columns(2)
        with col_f1:
            dux_fecha_desde = st.date_input("Fecha desde", value=first_day, key="dux_fecha_desde")
        with col_f2:
            dux_fecha_hasta = st.date_input("Fecha hasta", value=last_day, key="dux_fecha_hasta")

        # Export mode toggle
        dux_modo = st.radio(
            "Modo de export",
            ["Solo facturas cerradas (recomendado)", "Permitir parciales"],
            key="dux_export_mode",
            help="Modo A filtra solo CERRADO/LISTA PARA AJUSTE. Modo B incluye PENDIENTE."
        )
        modo_parcial = "Permitir" in dux_modo

        if modo_parcial:
            st.warning(
                "Modo parcial activado: las facturas con saldo pendiente se exportarán "
                "con ENC.L = suma de imputaciones del período. Confirmá con DUX si tu "
                "instancia soporta re-importar la misma factura más adelante con saldos "
                "faltantes. Si DUX rechaza re-imports, NO uses este modo."
            )

        # Validation step
        if st.button("Validar antes de exportar", use_container_width=True, key="btn_validar_dux"):
            # Clear lookup caches so admin sees fresh data after edits
            data._leer_maestro_conceptos_dux.clear()
            data._leer_codigos_empleado_dux.clear()
            data._leer_config_empresa.clear()
            with st.spinner("Validando rendiciones..."):
                from dux_export import validar_rendiciones_para_export
                # Get renditions using the same filter logic as export
                validation_result = data.validar_rendiciones_pre_export(
                    fecha_desde=dux_fecha_desde,
                    fecha_hasta=dux_fecha_hasta,
                    modo_parcial=modo_parcial,
                    fecha_inicio_dux=fecha_inicio_dux,
                )
                if validation_result is None:
                    st.error("No se pudo leer RENDICIONES_LOG")
                elif isinstance(validation_result, str):
                    st.warning(validation_result)
                elif isinstance(validation_result, tuple):
                    errores, warn_list = validation_result
                    if errores:
                        st.session_state["dux_validation_passed"] = False
                        st.error(f"Export DUX abortado: {len(errores)} errores bloqueantes")
                        for idx, err in enumerate(errores, 1):
                            with st.expander(f"ERROR {idx} — {err['tipo']}", expanded=True):
                                st.write(err["mensaje"])
                                for f in err.get("filas_afectadas", [])[:10]:
                                    st.code(f)
                                st.caption(f"Acción: {err.get('accion', '')}")
                        if warn_list:
                            st.info(f"Además hay {len(warn_list)} advertencias (ver abajo)")
                            for w in warn_list:
                                st.caption(f"⚠️ {w['tipo']}: {w['mensaje']}")
                    elif warn_list:
                        st.session_state["dux_validation_passed"] = True
                        st.success("Validación OK — sin errores bloqueantes. Podés exportar.")
                        st.warning(f"{len(warn_list)} advertencias (no bloqueantes):")
                        for w in warn_list:
                            with st.expander(f"⚠️ {w['tipo']}"):
                                st.write(w["mensaje"])
                                for f in w.get("filas_afectadas", [])[:10]:
                                    st.code(f)
                                st.caption(f"Acción: {w.get('accion', '')}")
                    else:
                        st.success("Validación OK — sin errores. Podés exportar.")
                        st.session_state["dux_validation_passed"] = True

        # Export button — only enabled after validation passes
        export_enabled = st.session_state.get("dux_validation_passed", False)
        if st.button("Exportar a EXPORT_DUX", type="primary", use_container_width=True,
                     disabled=not export_enabled, key="btn_exportar_dux"):
            with st.spinner("Generando exportación Dux..."):
                success, msg, count = data.escribir_export_dux_en_sheet(
                    fecha_desde=dux_fecha_desde,
                    fecha_hasta=dux_fecha_hasta,
                    modo_parcial=modo_parcial,
                    fecha_inicio_dux=fecha_inicio_dux,
                )
                if success:
                    st.success(f"✅ {msg}")
                    st.session_state["dux_validation_passed"] = False
                else:
                    st.error(f"❌ Error en exportación Dux")
                    st.code(msg)

        st.markdown("---")
        if st.button("🔄 Recalcular Saldos", use_container_width=True):
            with st.spinner("Recalculando saldos..."):
                success, msg, count = data.recalcular_control_saldos()
                if success:
                    st.success(f"✅ {msg}")
                else:
                    st.error(f"❌ {msg}")

        # ==========================================
        # REVISIÓN DE EXCESOS
        # ==========================================
        st.markdown("---")
        st.subheader("Revisión de Excesos")

        admin_name = st.text_input("Nombre del revisor", placeholder="Ej: Juan Pablo Mastrangelo", key="admin_reviewer_name")

        if st.button("Cargar pendientes de revisión", use_container_width=True, key="btn_load_pendientes"):
            with st.spinner("Leyendo rendiciones pendientes..."):
                st.session_state["pendientes_revision"] = data.leer_pendientes_revision()

        pendientes = st.session_state.get("pendientes_revision", [])

        if pendientes:
            # Filters
            col_rf1, col_rf2, col_rf3 = st.columns(3)
            with col_rf1:
                fechas_disponibles = sorted(set(p["fecha"] for p in pendientes))
                rev_fecha_desde = st.date_input("Desde", value=None, key="rev_fecha_desde")
            with col_rf2:
                rev_fecha_hasta = st.date_input("Hasta", value=None, key="rev_fecha_hasta")
            with col_rf3:
                oficinas_disp = sorted(set(p["oficina"] for p in pendientes))
                rev_oficina = st.selectbox("Oficina", ["Todas"] + oficinas_disp, key="rev_oficina_filter")

            # Apply filters
            filtered = pendientes
            if rev_fecha_desde:
                filtered = [p for p in filtered if str(p["fecha"])[:10] >= rev_fecha_desde.isoformat()]
            if rev_fecha_hasta:
                filtered = [p for p in filtered if str(p["fecha"])[:10] <= rev_fecha_hasta.isoformat()]
            if rev_oficina and rev_oficina != "Todas":
                filtered = [p for p in filtered if p["oficina"] == rev_oficina]

            st.caption(f"Mostrando {len(filtered)} de {len(pendientes)} pendientes")

            for i, pend in enumerate(filtered):
                with st.container(border=True):
                    st.markdown(
                        f"**{pend['usuario']}** — {pend['oficina']} — {pend['fecha']}"
                    )
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Concepto", pend["concepto"])
                    c2.metric("Monto Imputado", f"${float(pend.get('monto_imputar', 0) or 0):,.2f}")
                    c3.metric("Monto Sugerido", f"${float(pend.get('monto_sugerido', 0) or 0):,.2f}")

                    st.caption(f"Carpeta: {pend['numero_carpeta']} | CUIT: {pend['proveedor_cuit']}")

                    if pend.get("ticket_url"):
                        st.markdown(f"[Ver comprobante]({pend['ticket_url']})")

                    col_a, col_r = st.columns(2)
                    with col_a:
                        if st.button("Aprobar", key=f"aprobar_{pend['row_idx']}_{i}", type="primary"):
                            if not admin_name:
                                st.error("Ingresá tu nombre de revisor")
                            else:
                                ok, msg = data.aprobar_rendicion(pend["row_idx"], admin_name)
                                if ok:
                                    st.success(f"Aprobada: {msg}")
                                    # Remove from local list
                                    st.session_state["pendientes_revision"] = [
                                        p for p in st.session_state["pendientes_revision"]
                                        if p["row_idx"] != pend["row_idx"]
                                    ]
                                    st.rerun()
                                else:
                                    st.error(f"Error: {msg}")

                    with col_r:
                        motivo = st.text_area(
                            "Motivo de rechazo",
                            placeholder="Obligatorio para rechazar",
                            key=f"motivo_{pend['row_idx']}_{i}",
                            height=68
                        )
                        if st.button("Rechazar", key=f"rechazar_{pend['row_idx']}_{i}", type="secondary"):
                            if not admin_name:
                                st.error("Ingresá tu nombre de revisor")
                            elif not motivo or not motivo.strip():
                                st.error("El motivo de rechazo es obligatorio")
                            else:
                                ok, msg = data.rechazar_rendicion(pend["row_idx"], admin_name, motivo.strip())
                                if ok:
                                    st.success(f"Rechazada: {msg}")
                                    st.session_state["pendientes_revision"] = [
                                        p for p in st.session_state["pendientes_revision"]
                                        if p["row_idx"] != pend["row_idx"]
                                    ]
                                    st.rerun()
                                else:
                                    st.error(f"Error: {msg}")
        elif "pendientes_revision" in st.session_state:
            st.info("No hay rendiciones pendientes de revisión.")

    elif admin_input:
        st.error("Clave incorrecta")
