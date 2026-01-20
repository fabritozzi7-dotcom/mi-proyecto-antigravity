import data
import os
from dotenv import load_dotenv

load_dotenv()

try:
    print("Conectando a Google Sheets...")
    client = data.get_gsheets_client()
    if not client:
        print("Error: No se pudo crear el cliente de GSheets (revisa service_account.json)")
    else:
        sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
        print(f"Buscando planilla: {sheet_name}")
        sh = client.open(sheet_name)
        print("\n=== LINK DE TU PLANILLA ===")
        print(sh.url)
        print("===========================\n")
except Exception as e:
    print(f"Error: {e}")
