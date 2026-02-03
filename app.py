import streamlit as st
import datetime
import requests
import json
import os
import google.generativeai as genai
from dotenv import load_dotenv

# Import our data module
import data

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

        ## 1. DETECCIÓN DE DATOS (IMPORTANTE)
        - **IDENTIFICACIÓN DE CUIT (REGLA DE ORO):**
            - Las facturas Tipo A y C tienen dos CUITs (Emisor y Receptor).
            - El **CUIT del PROVEEDOR (Emisor)** siempre está en el ENCABEZADO (parte superior del ticket). Es el PRIMERO que aparece.
            - El CUIT del Cliente (Nosotros) está más abajo.
            - **CRÍTICO:** Debes tomar el CUIT que está en la parte superior del comprobante.
        - **Factura B:** Suele tener solo el CUIT del emisor.
        - **Factura C:** Aplica la misma regla que la A (Proveedor arriba, Cliente abajo).

        ## 2. LÓGICA POR TIPO
        - **TIPO DE COMPROBANTE:** Identifica la LETRA (A, B, C, M).
        - **CÓDIGO AFIP:** Busca "COD. XX" (ej: 001, 006, 011). Normalízalo a 3 dígitos.
        - **PUNTO DE VENTA (SUCURSAL):** 
            - El Punto de Venta (PV) es siempre de 4 o 5 dígitos.
            - **Opesa/Combustibles:** NO confundas el "Nro. Estación" (ej: Station 123) con el PV. El PV suele aparecer como `PV: 00010` o `00010-00000001`.
            - Si ves una cadena `XXXXX-YYYYYYYY`, el `XXXXX` es la sucursal/PV.

        ## 2. LÓGICA PARA FACTURA TIPO "A" (Discriminación Obligatoria)
        Debes desglosar cada centavo del ticket.
        - **Neto Gravado:** Identifica la base imponible sobre la que se calcula el IVA.
        - **IVA (Tasas):** Identifica y separa los montos por tasa.
            - Si el ticket NO explicita el % (ej. solo dice "IVA $210"), CALCULA la tasa: `(Monto IVA / Neto Gravado)`.
            - Resultado ~0.21 -> Asignar a **IVA 21%**.
            - Resultado ~0.105 -> Asignar a **IVA 10.5%**.
            - Resultado ~0.27 -> Asignar a **IVA 27%**.
        - **Percepciones:**
            - **Ganancias:** Busca "Perc. Ganancias" o similar.
            - **IIBB:** Busca "Perc. IIBB" o "Ingresos Brutos".
        - **No Gravado:** Suma aquí conceptos exentos, impuestos internos (combustibles, cigarrillos), tasas municipales o percepciones no categorizadas.

        ## IMPORTANTE: JURISDICCIÓN (CONDICIONAL)
        La jurisdicción SOLAMENTE es relevante si detectas una "Percepción de IIBB" (Ingresos Brutos) mayor a 0 en el campo `columna_W_perc_iibb`.
        - **SI hay Percepción de IIBB:** Busca la provincia asociada (ej: "Mendoza", "CABA", "Córdoba") y asigna el código en `columna_X_jurisdiccion_code`.
        - **SI NO hay Percepción de IIBB:** Asigna `null` a `columna_X_jurisdiccion_code`. No intentes adivinar la provincia por el domicilio si no hay impuesto provincial.
        - **Códigos:** CABA -> "CF", Buenos Aires -> "BA", Córdoba -> "CD", Santa Fe -> "SF", Mendoza -> "MZ" (o el de 2 letras estándar).

        ## 3. LÓGICA PARA FACTURA TIPO "B" o "C" (Agrupación Total)
        Esta es una regla de oro: **NUNCA DISCRIMINES IMPUESTOS EN FACTURAS B O C**.
        - Aunque el ticket diga "IVA Incluido: $XXX", **IGNÓRALO**.
        - Toma el **Monto Total** del comprobante.
        - Asigna el **100% del valor** al campo **"No Gravado"** (Columna R).
        - Los campos de IVA, Neto Gravado y Percepciones DEBEN ser 0.00.

        ## 4. VALIDACIÓN DE INTEGRIDAD MATEMÁTICA
        Antes de finalizar, realiza la siguiente suma de control internamente:
        `SUMA = (No Gravado + Neto Gravado + IVA 21 + IVA 10.5 + IVA 27 + Perc. Gcias + Perc. IIBB)`

        - La `SUMA` debe ser **EXACTAMENTE IGUAL** al **Monto Total**.
        - Si existe una diferencia menor a $0.05 (centavos) por redondeo, ajusta el campo "No Gravado" para que la suma cuadre perfectamente con el Total.

        # FORMATO DE SALIDA (JSON)
        Devuelve ÚNICAMENTE un objeto JSON con esta estructura exacta para mapear al Google Sheet:

        {
          "tipo_factura": "String (A, B, C, TICKET)",
          "codigo_afip": "String (001, 006, etc) o null", 
          "fecha": "DD/MM/AAAA",
          "proveedor": "String (Nombre de fantasía o Razón Social)",
          "cuit": "String (Solo números, sin guiones)",
          "sucursal": "Punto de venta (5 digitos)",
          "numero_comprobante": "Numero (8 digitos)",
          "monto_total_columna_Y": Number (Float, el total a pagar),
          "desglose": {
            "columna_R_no_gravado": Number (Float. Si es B/C aquí va el TOTAL. Si es A, van exentos/imp internos),
            "columna_S_iva_21": Number (Float),
            "columna_T_iva_105": Number (Float),
            "columna_U_iva_27": Number (Float),
            "columna_V_perc_ganancias": Number (Float),
            "columna_W_perc_iibb": Number (Float),
            "columna_X_jurisdiccion_code": "String (ej: CF, BA, CD) o null",
            "neto_gravado_aux": Number (Float, aunque no se pide explícito en columnas R-X, es necesario para cálculos (Col Q))
          },
          "validacion_check": "String (OK si la suma cuadra, ERROR si no)"
        }

        ## REGLAS DE SEGURIDAD (LEGIBILIDAD)
        - **SI EL TICKET ES ILEGIBLE, ESTÁ BORROSO O CORTADO:** No intentes adivinar datos.
        - Devuelve `null` en los campos que no puedas leer con certeza absoluta (especialmente CUIT y Montos).
        - El sistema detectará los `null` y pedirá carga manual al usuario.
        - Prioriza siempre la precisión sobre la inferencia.
        """
        
        image_parts = [{"mime_type": mime_type, "data": image_bytes}]
        response = model.generate_content([prompt, image_parts[0]])
        
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
    # Filter concepts by Office
    all_concepts = sorted(list(data.CONCEPTOS_DB.keys()))
    if office:
        # Filter: Keep if office is 'Todas', '---', or matches User's Office
        concepts_list = [c for c in all_concepts if data.CONCEPTOS_OFICINA_DB.get(c, "Todas") in ["Todas", "---", office]]
        st.caption(f"🔍 Mostrando {len(concepts_list)} conceptos para oficina: **{office}**")
    else:
        concepts_list = all_concepts
        st.caption("🔍 Mostrando todos los conceptos (Sin filtro de oficina)")
        
    selected_concept = st.selectbox("Seleccionar Concepto", concepts_list, index=None, label_visibility="collapsed", placeholder="Escribe para buscar...", key="concept_input")
    
    # Auto-fill logic
    suggested_amount_concept = 0.0
    if selected_concept:
        suggested_amount_concept = data.CONCEPTOS_DB.get(selected_concept, 0.0)
    
    # User Input for IMPUTATION (Monto a Imputar)
    monto_imputar = st.number_input("💵 Monto a Imputar (Usuario)", 
                                  value=suggested_amount_concept if suggested_amount_concept > 0 else 0.0, 
                                  step=100.0, format="%.2f",
                                  help="El monto que desea asignar a esta carpeta. Puede diferir del ticket.",
                                  key=f"monto_imputar_{selected_concept}")
    
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
                        
                        st.session_state.scan_cuit_input = str(scan_result.get("cuit") or "")
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

        # New Auditor Fields
        try:
            monto_ticket_total = float(data_ia.get("monto_total_columna_Y") or 0.0)
            
            # Extract Desglose
            desglose = data_ia.get("desglose", {})
            st.session_state.desglose_data = desglose # Store for payload
            
            # Helper for imputation base
            monto_neto = float(desglose.get("neto_gravado_aux") or 0.0)
             
            # Validation Check
            val_check = data_ia.get("validacion_check", "N/A")
            if val_check != "OK":
                st.warning(f"⚠️ Alerta Auditoría: {val_check}")
            else:
                st.info("✅ Auditoría: Suma de control OK")
                
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
            
        # Conditional Input for Net Amount
        monto_neto_input = 0.0
        if tipo_fact_input == "A":
            monto_neto_input = st.number_input("Monto Neto Gravado", value=monto_neto if monto_neto > 0 else 0.0)
            
        afip_code_input = st.text_input("Código AFIP", value=default_afip)

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
    provider_status = "none"


# --- LOGIC: BALANCES & FLAGS ---
# Logic: If no receipt amount detected but user imputes amount -> affect balance
afectar_a_saldo = False
if monto_ticket_total == 0 and monto_imputar > 0:
    afectar_a_saldo = True


# --- FOOTER: ACCIÓN FINAL ---
st.markdown("<br>", unsafe_allow_html=True)

if st.button("💾 Guardar Rendición", type="primary", use_container_width=True):
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
        
        # Auditor Breakdown Base (Total)
        desglose_base = st.session_state.get("desglose_data", {}).copy()
        if not desglose_base or modo_manual:
             # Basic manual breakdown based on type
            if tipo_fact_input in ["B", "C", "Ticket"]:
                desglose_base = {
                    "columna_R_no_gravado": monto_ticket_total,
                    "monto_total_columna_Y": monto_ticket_total
                }
            elif tipo_fact_input == "A":
                desglose_base = {
                    "neto_gravado_aux": monto_neto_input,
                    "monto_total_columna_Y": monto_ticket_total,
                    "columna_R_no_gravado": monto_ticket_total - monto_neto_input
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
                
                "monto_gravado_calculado": p_monto_gravado, # Prorated
                "monto_ticket_total": p_monto_ticket,       # Prorated
                "monto_a_imputar": p_monto_imputar,         # Prorated
                
                "auditor_desglose": p_desglose,             # Prorated
                "observaciones": observaciones
            }
             
             # Log to GSheets
             try:
                 if data.log_rendicion_to_sheet(payload, ticket_link):
                     success_count += 1
             except Exception as e:
                 st.error(f"Error guardando carpeta {folder_code}: {e}")
             
             progress_bar.progress((idx + 1) / N)

        if success_count == N:
            st.success(f"✅ Rendición guardada exitosamente en {N} carpetas (Prorrateo).")
            # RESET FORM LOGIC
            st.session_state.scanned_data = None # Clear AI
            if "desglose_data" in st.session_state: del st.session_state.desglose_data
            
            # Clear Inputs via Session State
            # Note: We don't delete the key, we just clear the value (if using key, modifying st.session_state[key] updates widget)
            # Actually, for widgets, if we change the state, it updates on rerun.
            
            # Reset Uploader logic (Increment key to force new widget)
            st.session_state.uploader_key += 1
            
            # Clear text inputs
            # To strictly 'clear' them for the next run:
            if "folder_input" in st.session_state: st.session_state.folder_input = ""
            if "concept_input" in st.session_state: st.session_state.concept_input = None
            if "obs_input" in st.session_state: st.session_state.obs_input = ""
            
            # Clear Manual Mode keys if they exist
            keys_to_clear = [
                "manual_cuit", "manual_provider", "manual_tipo", "manual_suc", "manual_num", "manual_total", "manual_neto", "manual_afip",
                "scan_suc_input", "scan_num_input", "scan_tipo_input", "scan_cuit_input", "scan_provider_input"
            ]
            for k in keys_to_clear:
                if k in st.session_state: del st.session_state[k]
            
            st.button("🔄 Cargar Nueva Rendición (Limpiar)", type="secondary", on_click=st.rerun)
            
            # Auto-rerun after short delay/toast if preferred, or just rely on button. 
            # The User asked for "Guardar y Nuevo" experience. 
            # We can force rerun immediately or let them see the success message.
            # Let's show the success details, then a button to 'refresh' or just clear it.
            # Ideally, immediate clear is better for speed.
            import time
            time.sleep(1.5) # Show success briefly
            st.rerun()
            
        else:
            st.warning(f"⚠️ Se guardaron {success_count} de {N} carpetas. Revise la consola.")
