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
            # Silent fail or toast warning - app continues with fallback
            if msg != "No Client":
                st.toast(f"⚠️ Modo Offline: Uso de datos locales ({msg})")
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
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        prompt = """
        Analiza este comprobante de gasto. Presta MUCHA atención a los detalles pequeños en la cabecera.

        INSTRUCCIONES ESPECÍFICAS PARA 'CODIGO AFIP':
        1. Busca la letra grande que identifica al comprobante (A, B, C, M).
        2. DENTRO de ese recuadro con la letra, o justo DEBAJO, busca un texto pequeño que diga "COD. XX" (ej: COD. 06).
        3. Si no está en el recuadro, busca arriba a la derecha frases como "Codigo N° 001" o "Cod. 011".
        4. Extrae solo el número y normalízalo a 3 dígitos (ej: 06 -> "006").

        Extrae la siguiente información en formato JSON estricto:
        {
            "cuit": "CUIT proveedor (solo números)",
            "proveedor": "Razon Social",
            "monto_total": 0.00,
            "tipo_factura": "Letra (A, B, C, M)",
            "sucursal": "Punto de venta (5 digitos)",
            "numero_comprobante": "Numero (8 digitos)",
            "monto_neto_gravado": 0.00 (Si es A, el neto. Si es B, igual al total o imputado),
            "codigo_afip": "001, 006, 011, etc. (segun instrucciones arriba)"
        }
        Si no encuentras algún dato, usa null.
        """
        
        image_parts = [{"mime_type": mime_type, "data": image_bytes}]
        response = model.generate_content([prompt, image_parts[0]])
        text = response.text
        
        if "```json" in text:
            text = text.replace("```json", "").replace("```", "")
        
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

if "scanned_data" in st.session_state and final_image_bytes:
    with st.container(border=True):
        st.subheader("🔍 Datos del Ticket")
        
        data_ia = st.session_state.scanned_data
        default_cuit = str(data_ia.get("cuit") or "")
        default_provider = str(data_ia.get("proveedor") or "")
        default_tipo = str(data_ia.get("tipo_factura") or "C").upper().strip()
        default_suc = str(data_ia.get("sucursal") or "").replace("-","")
        default_num = str(data_ia.get("numero_comprobante") or "").replace("-","")
        default_afip = str(data_ia.get("codigo_afip") or "")
        
        try:
            monto_ticket_total = float(data_ia.get("monto_total") or 0.0)
            monto_neto = float(data_ia.get("monto_neto_gravado") or 0.0)
        except:
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
            
            # Financials
            "monto_gravado_calculado": monto_gravado_final,
            "monto_ticket_total": monto_ticket_total,
            "monto_a_imputar": monto_imputar
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
