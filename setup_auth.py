import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# Scopes required
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/spreadsheets']

def main():
    creds = None
    # 1. Load existing token if any
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        except Exception:
            print("Token inválido, se regenerará.")
            creds = None

    # 2. Refresh or Login
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print("Token refrescado automágicamente.")
            except Exception:
                print("No se pudo refrescar. Requiere re-login.")
                creds = None
        
        if not creds:
            if not os.path.exists('client_secret.json'):
                print("❌ ERROR: No se encontró 'client_secret.json'.")
                print("Descárgalo de Google Cloud Console (Create OAuth Client ID -> Desktop App).")
                input("Presiona ENTER para salir...")
                return

            print("Abriendo navegador para autenticación...")
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # 3. Save
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            print("✅ 'token.json' guardado exitosamente!")

if __name__ == '__main__':
    main()
