import gspread
import os
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
import json

# Carica le variabili d'ambiente (il tuo file .env)
load_dotenv()

# Prendi le stesse credenziali che usa il bot
google_creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
google_creds_dict = json.loads(google_creds_json_str)

# Definisci gli stessi scopes
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly"
]
creds = Credentials.from_service_account_info(google_creds_dict, scopes=scopes)

# Autenticazione sincrona
print(">>> Inizio test di connessione Sincrono...")
try:
    gc = gspread.Client(auth=creds)
    
    # Prendi lo stesso URL
    spreadsheet_url = os.getenv("SPREADSHEET_URL") # Assumendo che tu l'abbia messa nelle variabili d'ambiente
    # Se non l'hai fatto, incolla l'URL qui:
    spreadsheet_url = "https://docs.google.com/spreadsheets/d/1Wtm5UiS6Mcs-byDDDL5ZVIB_k6xGUF35QL5U33bvSLU/edit?pli=1&gid=450436997#gid=450436997"

    print(f">>> Tento di aprire l'URL: {spreadsheet_url}")
    spreadsheet = gc.open_by_url(spreadsheet_url)
    
    print(">>> SUCCESSO! Connessione stabilita e foglio aperto correttamente.")
    print(f">>> Nome del foglio: {spreadsheet.title}")

except Exception as e:
    print(f">>> FALLIMENTO! Errore durante il test:")
    print(f">>> Tipo di errore: {type(e).__name__}")
    print(f">>> Messaggio: {e}")

print(">>> Test terminato.")