import json
import os
from dotenv import load_dotenv

load_dotenv()

print("\n=== COPIAR DESDE AQUÍ ABAJO PARA ST.SECRETS ===\n")

# 1. Environment Variables
print(f'GOOGLE_API_KEY = "{os.getenv("GOOGLE_API_KEY", "")}"')
print(f'GSHEET_NAME = "{os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")}"')

# Drive Logic: If placeholder, ensure we output empty string (Root) or the detected value if real
raw_fid = os.getenv("DRIVE_FOLDER_ID", "1y5W...PASTE_ID_HERE")
if "PASTE" in raw_fid:
    final_fid = ""
else:
    final_fid = raw_fid

print(f'DRIVE_FOLDER_ID = "{final_fid}"')

print("\n")

# 2. Service Account
if os.path.exists("service_account.json"):
    with open("service_account.json") as f:
        sa_data = json.load(f)
        print("[gcp_service_account]")
        for k, v in sa_data.items():
            if k == "private_key":
                # Escape newlines for valid TOML single-line string
                val = v.replace("\n", "\\n")
                print(f'{k} = "{val}"')
            else:
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
