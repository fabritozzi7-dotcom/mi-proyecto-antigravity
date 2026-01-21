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
    st.session_state.data_synced = True

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

        ## 1. DETECCIÓN DE TIPO DE COMPROBANTE
        Lo primero que debes hacer es identificar la LETRA del comprobante (A, B, C, M).
        - **Si es "A" o "M":** Ejecuta la lógica de "Responsable Inscripto".
        - **Si es "B" o "C":** Ejecuta la lógica de "Consumidor Final / Monotributo".
        
        ## IMPORTANTE: CODIGO AFIP
        Busca cerca de la letra o arriba a la derecha el "COD. XX" (ej: 001, 006). Normalízalo a 3 dígitos si es necesario.

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

        ## IMPORTANTE: JURISDICCIÓN (GLOBAL)
        Independientemente del tipo de factura (A, B, C), busca SIEMPRE la provincia en el domicilio del emisor (ej: "Mendoza", "CABA", "Córdoba") o en las percepciones de IIBB.
        - Asigna el código correspondiente en `columna_X_jurisdiccion_code`:
          - CABA/Capital Federal -> "CF"
          - Buenos Aires -> "BA"
          - Córdoba -> "CD"
          - Santa Fe -> "SF"
          - Mendoza -> "MZ"
          - (Usa el código de 2 letras estándar si detectas otra provincia)

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
    
    # Folder Number is strictly required now
    folder_number = st.text_input("📂 Número de Carpeta (Obligatorio)", placeholder="Ej: IMP-2024-001")
    
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
        
    selected_concept = st.selectbox("Seleccionar Concepto", concepts_list, index=None, label_visibility="collapsed", placeholder="Escribe para buscar...")
    
    # Auto-fill logic
    suggested_amount_concept = 0.0
    if selected_concept:
        suggested_amount_concept = data.CONCEPTOS_DB.get(selected_concept, 0.0)
    
    # User Input for IMPUTATION (Monto a Imputar)
    # This is what will be charged to the folder, separate from the receipt total
    # We use a dynamic key to force update when concept changes
    monto_imputar = st.number_input("💵 Monto a Imputar (Usuario)", 
                                  value=suggested_amount_concept if suggested_amount_concept > 0 else 0.0, 
                                  step=100.0, format="%.2f",
                                  help="El monto que desea asignar a esta carpeta. Puede diferir del ticket.",
                                  key=f"monto_imputar_{selected_concept}")


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
        file_input = st.file_uploader("Seleccionar archivo", type=["jpg", "png", "jpeg", "pdf"])
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
                        status.update(label="✅ Escaneo completado!", state="complete", expanded=False)
                    else:
                        st.error(f"Error técnico: {scan_result}")
                        status.update(label="❌ Error en el escaneo", state="error")
            else:
                st.error("Error de configuración API Key")

# --- VALIDATION RESULT SECTION ---

# Defaults
default_cuit = ""
default_provider = ""
default_afip = ""
monto_ticket_total = 0.0  # What AI sees on the paper
monto_neto = 0.0

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
        
        # Validation Logic (Gold Rule)
        is_validated = False
        validated_name = ""
        
        if default_cuit:
            clean_input = default_cuit.replace("-", "").replace(" ", "")
            for db_cuit, db_name in data.PROVEEDORES_DB.items():
                if db_cuit == default_cuit or db_cuit.replace("-", "") == clean_input:
                    is_validated = True
                    validated_name = db_name
                    default_cuit = db_cuit
                    break
        
        # Display logic
        if is_validated:
            st.success("✅ Proveedor Validado")
            
            c1, c2 = st.columns(2)
            c1.metric("CUIT", default_cuit)
            c2.metric("Monto Ticket", f"${monto_ticket_total:,.2f}")
            st.info(f"**Razón Social:** {validated_name}")
            
            cuit_input = default_cuit
            provider_input = validated_name
            provider_status = "valid"
        else:
            st.warning("⚠️ Proveedor Pendiente de Alta")
            cuit_input = st.text_input("CUIT", value=default_cuit)
            provider_input = st.text_input("Razón Social", value=default_provider)
            provider_status = "pending_approval"
            
            provider_status = "pending_approval"

        # Expanded Invoice Details (Fabian's Rules)
        st.markdown("---")
        c1, c2, c3 = st.columns(3)
        with c1:
            tipo_fact_input = st.selectbox("Tipo", ["A", "B", "C", "M", "Ticket"], index=["A", "B", "C", "M", "Ticket"].index(default_tipo) if default_tipo in ["A","B","C","M","Ticket"] else 2)
        with c2:
            pto_vta_input = st.text_input("Sucursal (5)", value=default_suc, max_chars=5)
        with c3:
            num_comp_input = st.text_input("Número (8)", value=default_num, max_chars=8)
            
        # Conditional Input for Net Amount
        monto_neto_input = 0.0
        if tipo_fact_input == "A":
            monto_neto_input = st.number_input("Monto Neto Gravado", value=monto_neto if monto_neto > 0 else 0.0)
            
        afip_code_input = st.text_input("Código AFIP", value=default_afip)

else:
    # Manual mode defaults
    cuit_input = ""
    provider_input = ""
    afip_code_input = ""
    # Manual mode defaults
    cuit_input = ""
    provider_input = ""
    afip_code_input = ""
    provider_status = "none"
    tipo_fact_input = "C"
    pto_vta_input = ""
    num_comp_input = ""
    monto_neto_input = 0.0
    
    # Correction for base imputation in manual mode?
    # We leave standard flow.
    pass


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
        # Modified: Now we just mark state in backend, do not auto-close in UI
        diff = abs(monto_ticket_total - monto_imputar)
        if 0 < diff < 1000.0:
            st.info("💰 Diferencia menor a $1000: Se marcará como 'LISTA PARA AJUSTE'")
            # monto_imputar = monto_ticket_total # DISABLED per new instruction to just MARK state.

        # Logic for 'Gravado' (Column 16)
        # Rule: If A -> Net. If B -> Imputed Amount. Else -> 0.
        monto_gravado_final = 0.0
        if tipo_fact_input == "A":
            monto_gravado_final = monto_neto_input
        elif tipo_fact_input == "B":
            # "Neto Gravado logic... saldo de respaldo debe ser la base imponible"
            # For B, base is usually Total or Imputed.
            monto_gravado_final = monto_imputar
        
        # Logic for Provider Validation (Column 15)
        prov_valid_txt = "Sí" if provider_status == "valid" else "No"

        # LOGIC: BALANCE SEARCH (Regla N a 1)
        # If no new ticket is uploaded, but user is imputing an amount, search for existing Provider Balance
        balance_info_msg = ""
        found_balance = False
        
        if not final_image_bytes and monto_imputar > 0 and cuit_input:
             # Search...
             with st.spinner("🔍 Buscando saldo a favor con Proveedor..."):
                 inv_data = data.find_available_invoice_balance(cuit_input, monto_imputar)
                 if inv_data:
                     found_balance = True
                     # Override components with found invoice data to link them
                     tipo_fact_input = inv_data["tipo"]
                     pto_vta_input = inv_data["sucursal"]
                     num_comp_input = inv_data["numero"]
                     balance_info_msg = f"✅ Se vinculó a Saldo Disponible: {tipo_fact_input} {pto_vta_input}-{num_comp_input} (Saldo: ${inv_data['saldo']:,.2f})"
                     st.info(balance_info_msg)
                 else:
                     st.warning("⚠️ No se encontró FACTURA con saldo suficiente en CONTROL_SALDOS. Se guardará como pendiente de conciliación.")
        
        # Construct Detailed Payload
        payload = {
            "fecha": expense_date.isoformat(),
            "usuario": selected_user,
            "oficina": office,
            "numero_carpeta": folder_number,
            "tipo_operacion": op_type,
            "cliente": client or "Sin Cliente",
            "concepto": selected_concept,
            "monto_sugerido_concepto": suggested_amount_concept,
            
            # Invoice Data (Potentially updated by Balance Search)
            "tipo_factura": tipo_fact_input,
            "codigo_afip": afip_code_input,
            "sucursal_factura": pto_vta_input,
            "numero_factura": num_comp_input,
            
            # Provider
            "proveedor_validado_txt": prov_valid_txt,
            "proveedor_cuit": cuit_input,
            "proveedor_nombre": provider_input,
            
            # Financials
            "monto_gravado_calculado": monto_gravado_final,
            "monto_ticket_total": monto_ticket_total,
            "monto_a_imputar": monto_imputar,
            
            # Auditor Breakdown (New Strict Logic)
            "auditor_desglose": st.session_state.get("desglose_data", {})
        }
        
        # 1. Upload to Drive (if file exists)
        ticket_link = ""
        if final_image_bytes:
            with st.spinner("Subiendo Ticket a Drive..."):
                # Generate Filename: TICKET_CUIT_NUMERO.ext
                ext = "pdf" if "pdf" in final_mime_type else "jpg"
                fname = f"TICKET_{cuit_input}_{num_comp_input}.{ext}"
                link, file_id, error_msg = data.upload_receipt_to_drive(final_image_bytes, fname, final_mime_type)
                if link:
                    ticket_link = link
                    st.toast("✅ Archivo subido a Drive")
                else:
                    st.error(f"Error subiendo archivo a Drive: {error_msg}")

        # 2. Log to GSheets (if credentials exist)
        try:
             # Pass ticket_link to the updated function
             gs_success = data.log_rendicion_to_sheet(payload, ticket_link)
             if gs_success:
                 st.toast("✅ Guardado en Google Sheets")
        except Exception as e:
             st.error(f"Error guardando en Sheets: {e}")

        st.success("✅ Rendición Procesada Exitosamente")
        st.expander("Ver Datos Técnicos (Payload)").json(payload)
