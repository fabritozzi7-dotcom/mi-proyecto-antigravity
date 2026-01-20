import json
import os

print("\n=== COPIAR DESDE AQUÍ ABAJO PARA ST.SECRETS ===\n")

# 1. Environment Variables
print(f'GOOGLE_API_KEY = "{os.getenv("GOOGLE_API_KEY", "")}"')
print(f'GSHEET_NAME = "{os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")}"')
# Note: DRIVE_FOLDER_ID might be in data.py as const, but if env exists:
folder_id = os.getenv("DRIVE_FOLDER_ID", "1y5...PASTE_ID_HERE")
print(f'DRIVE_FOLDER_ID = "{folder_id}"')

print("\n")

# 2. Service Account
if os.path.exists("service_account.json"):
    with open("service_account.json") as f:
        sa_data = json.load(f)
        print("[gcp_service_account]")
        for k, v in sa_data.items():
            print(f'{k} = "{v}"')
else:
    print("# [gcp_service_account] -> service_account.json NO ENCONTRADO")

print("\n")

# 3. User Token (OAuth)
if os.path.exists("token.json"):
    with open("token.json") as f:
        token_data = json.load(f)
        print("[gcp_user_token]")
        for k, v in token_data.items():
            # Boolean/Numbers handling for TOML
            if isinstance(v, bool):
                val = str(v).lower()
            elif isinstance(v, (int, float)):
                val = v
            else:
                val = f'"{v}"'
            print(f'{k} = {val}')
else:
    print("# [gcp_user_token] -> token.json NO ENCONTRADO (Ejecuta setup_auth.py primero)")

print("\n=== FIN DE COPIA ===\n")
