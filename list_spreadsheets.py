import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
from dotenv import load_dotenv

load_dotenv()

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def list_files():
    if not os.path.exists("service_account.json"):
        print("Error: No se encontró service_account.json")
        return

    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", SCOPE)
        client = gspread.authorize(creds)
        
        print("\n--- Planillas accesibles por la Service Account ---")
        spreadsheets = client.openall()
        if not spreadsheets:
            print("No se encontraron planillas compartidas con esta cuenta.")
        for sh in spreadsheets:
            print(f"- Nombre: '{sh.title}' | ID: {sh.id}")
        print("--------------------------------------------------\n")
        
        target = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
        print(f"Buscando específicamente: '{target}'")
        try:
            sh = client.open(target)
            print(f"✅ ¡ÉXITO! Se pudo abrir '{target}'")
        except gspread.exceptions.SpreadsheetNotFound:
            print(f"❌ ERROR: '{target}' NO ENCONTRADA.")

    except Exception as e:
        print(f"Error técnico: {e}")

if __name__ == "__main__":
    list_files()
