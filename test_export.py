from dotenv import load_dotenv
load_dotenv()

import data
import logging
logging.basicConfig(level=logging.DEBUG)

print("=== TEST EXPORT DUX ===")
success, msg, count = data.escribir_export_dux_en_sheet()
print(f"Success: {success}")
print(f"Message: {msg}")
print(f"Count: {count}")
