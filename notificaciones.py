"""
notificaciones.py — Envío de alertas por email cuando una rendición excede
el monto sugerido.

SMTP Gmail (Google Workspace). Credenciales en st.secrets:
    [smtp]
    user = "alertas@expoconsult.com.ar"
    password = "xxxx xxxx xxxx xxxx"   # app password de Gmail
    from_name = "Expoconsult Rendiciones"
    dry_run = false
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import streamlit as st
import gspread

logger = logging.getLogger(__name__)

# ==========================================
# LECTURA DE DESTINATARIOS
# ==========================================

SEED_DESTINATARIOS = [
    ["VENDRAMINI, CARLA SILVINA", "carlavendramini@expoconsult.com.ar", "TRUE"],
    ["VENDRAMINI, CONSTANZA ILEANA", "constanza@expoconsult.com.ar", "TRUE"],
    ["MASTRANGELO, JUAN PABLO", "juanpablomastrangelo@expoconsult.com.ar", "TRUE"],
    ["MASTRANGELO, TOMAS", "tomasmastrangelo@expoconsult.com.ar", "TRUE"],
]


def crear_hoja_config_notificaciones(sh):
    """Crea y siembra CONFIG_NOTIFICACIONES si no existe."""
    try:
        sh.worksheet("CONFIG_NOTIFICACIONES")
        return  # Already exists
    except gspread.exceptions.WorksheetNotFound:
        pass

    ws = sh.add_worksheet(title="CONFIG_NOTIFICACIONES", rows=20, cols=3)
    ws.update(range_name="A1:C1", values=[["nombre", "email", "activo"]])
    if SEED_DESTINATARIOS:
        end_row = 1 + len(SEED_DESTINATARIOS)
        ws.update(range_name=f"A2:C{end_row}", values=SEED_DESTINATARIOS)
    logger.info(f"CONFIG_NOTIFICACIONES created and seeded with {len(SEED_DESTINATARIOS)} rows")


@st.cache_data(ttl=300)
def leer_destinatarios(_client, sheet_id):
    """Lee destinatarios activos de CONFIG_NOTIFICACIONES.

    Args:
        _client: gspread client (prefixed with _ to skip Streamlit hashing).
        sheet_id: spreadsheet ID.

    Returns:
        list[dict]: [{"nombre": ..., "email": ...}, ...] (solo activos)
    """
    try:
        sh = _client.open_by_key(sheet_id)
        ws = sh.worksheet("CONFIG_NOTIFICACIONES")
        rows = ws.get_all_values()

        if len(rows) < 2:
            return []

        headers = [h.lower().strip() for h in rows[0]]
        try:
            idx_nombre = headers.index("nombre")
            idx_email = headers.index("email")
            idx_activo = headers.index("activo")
        except ValueError:
            idx_nombre, idx_email, idx_activo = 0, 1, 2

        destinatarios = []
        for row in rows[1:]:
            if len(row) > max(idx_nombre, idx_email, idx_activo):
                activo = str(row[idx_activo]).strip().upper()
                if activo in ("TRUE", "SI", "SÍ", "1", "VERDADERO"):
                    nombre = str(row[idx_nombre]).strip()
                    email = str(row[idx_email]).strip()
                    if email:
                        destinatarios.append({"nombre": nombre, "email": email})

        return destinatarios
    except Exception as e:
        logger.error(f"Error reading CONFIG_NOTIFICACIONES: {e}")
        return []


# ==========================================
# SMTP CONFIG
# ==========================================

def _get_smtp_config():
    """Lee configuración SMTP desde st.secrets con fallback a defaults."""
    try:
        smtp_section = st.secrets.get("smtp", {})
        return {
            "user": smtp_section.get("user", ""),
            "password": smtp_section.get("password", ""),
            "from_name": smtp_section.get("from_name", "Expoconsult Rendiciones"),
            "dry_run": str(smtp_section.get("dry_run", "false")).lower() in ("true", "1", "yes"),
        }
    except Exception:
        return {
            "user": "",
            "password": "",
            "from_name": "Expoconsult Rendiciones",
            "dry_run": True,
        }


# ==========================================
# TEMPLATE HTML
# ==========================================

def _generar_html_exceso(payload, ticket_url):
    """Genera el cuerpo HTML del mail de alerta de exceso."""
    operador = payload.get("usuario", "---")
    oficina = payload.get("oficina", "---")
    fecha = payload.get("fecha", "---")
    proveedor_nombre = payload.get("proveedor_nombre", "---")
    proveedor_cuit = payload.get("proveedor_cuit", "---")
    concepto = payload.get("concepto", "---")
    monto_sugerido = float(payload.get("monto_sugerido_concepto", 0) or 0)
    monto_imputado = float(payload.get("monto_a_imputar", 0) or 0)
    carpeta = payload.get("numero_carpeta", "---")
    operacion = payload.get("tipo_operacion", "---")

    diferencia = monto_imputado - monto_sugerido
    porcentaje = (diferencia / monto_sugerido * 100) if monto_sugerido > 0 else 0

    link_html = f'<a href="{ticket_url}">Ver comprobante</a>' if ticket_url else "Sin comprobante"

    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px;">
        <h2 style="color: #d32f2f;">Rendici&oacute;n excede monto sugerido</h2>
        <p>Se registr&oacute; una rendici&oacute;n que supera el monto sugerido para el concepto
        y qued&oacute; en estado <strong>PENDIENTE REVISI&Oacute;N</strong>.</p>

        <table style="border-collapse: collapse; width: 100%; margin: 16px 0;">
            <tr style="background: #f5f5f5;">
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Operador</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{operador}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Oficina</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{oficina}</td>
            </tr>
            <tr style="background: #f5f5f5;">
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Fecha</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{fecha}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Proveedor</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{proveedor_nombre} (CUIT: {proveedor_cuit})</td>
            </tr>
            <tr style="background: #f5f5f5;">
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Concepto</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{concepto}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Monto sugerido</td>
                <td style="padding: 8px; border: 1px solid #ddd;">${monto_sugerido:,.2f}</td>
            </tr>
            <tr style="background: #fff3e0;">
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Monto imputado</td>
                <td style="padding: 8px; border: 1px solid #ddd; color: #d32f2f; font-weight: bold;">
                    ${monto_imputado:,.2f}
                </td>
            </tr>
            <tr style="background: #ffebee;">
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Diferencia</td>
                <td style="padding: 8px; border: 1px solid #ddd; color: #d32f2f;">
                    +${diferencia:,.2f} ({porcentaje:,.1f}%)
                </td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Carpeta</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{carpeta}</td>
            </tr>
            <tr style="background: #f5f5f5;">
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Operaci&oacute;n</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{operacion}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Comprobante</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{link_html}</td>
            </tr>
        </table>

        <p style="font-size: 12px; color: #999;">
            Este mensaje fue generado autom&aacute;ticamente por Expoconsult Rendiciones.
        </p>
    </body>
    </html>
    """


# ==========================================
# ENVÍO DE MAIL
# ==========================================

def enviar_alerta_exceso(payload, ticket_url, gsheets_client, sheet_id):
    """Envía mail de alerta de exceso a los autorizantes configurados.

    Args:
        payload: dict con datos de la rendición.
        ticket_url: URL del comprobante en Drive.
        gsheets_client: gspread client para leer destinatarios.
        sheet_id: ID del spreadsheet.

    Returns:
        (bool, str): (éxito, mensaje).
    """
    config = _get_smtp_config()

    if not config["user"] or not config["password"]:
        msg = "SMTP no configurado (faltan credenciales en st.secrets[smtp])"
        logger.warning(msg)
        return False, msg

    destinatarios = leer_destinatarios(gsheets_client, sheet_id)
    if not destinatarios:
        msg = "No hay destinatarios activos en CONFIG_NOTIFICACIONES"
        logger.warning(msg)
        return False, msg

    emails_to = [d["email"] for d in destinatarios]
    operador = payload.get("usuario", "---")
    monto = float(payload.get("monto_a_imputar", 0) or 0)

    subject = f"[REVISION] Rendicion excede monto sugerido — {operador} — ${monto:,.2f}"
    html_body = _generar_html_exceso(payload, ticket_url)

    # DRY RUN: log to stdout instead of sending
    if config["dry_run"]:
        logger.info("=" * 60)
        logger.info("SMTP DRY RUN — Mail NOT sent")
        logger.info(f"  From: {config['from_name']} <{config['user']}>")
        logger.info(f"  To: {', '.join(emails_to)}")
        logger.info(f"  Subject: {subject}")
        logger.info("-" * 60)
        logger.info(html_body)
        logger.info("=" * 60)
        # Also print to stdout for Streamlit Cloud logs
        print("=" * 60)
        print("SMTP DRY RUN — Mail NOT sent")
        print(f"  From: {config['from_name']} <{config['user']}>")
        print(f"  To: {', '.join(emails_to)}")
        print(f"  Subject: {subject}")
        print("-" * 60)
        print(html_body)
        print("=" * 60)
        return True, f"DRY RUN: mail logueado a stdout ({len(emails_to)} destinatarios)"

    # REAL SEND
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{config['from_name']} <{config['user']}>"
        msg["To"] = ", ".join(emails_to)
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(config["user"], config["password"])
            server.sendmail(config["user"], emails_to, msg.as_string())

        logger.info(f"Mail enviado a {len(emails_to)} destinatarios: {emails_to}")
        return True, f"Mail enviado a {len(emails_to)} destinatarios"

    except Exception as e:
        logger.error(f"Error enviando mail SMTP: {e}")
        return False, str(e)
