import os
import logging
import openai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from fastapi import FastAPI, Request
import uvicorn

# --- CONFIGURAZIONE e VARIABILI D'AMBIENTE ---
# Carica le variabili da .env se presenti (per test locali)
from dotenv import load_dotenv
load_dotenv()

# Configurazione del logger
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Recupera le credenziali dalle variabili d'ambiente (il modo corretto per Render)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") # Il nome del tuo deployment
WEBHOOK_URL = os.getenv("WEBHOOK_URL") # L'URL che Render ti fornirà

if not all([TELEGRAM_TOKEN, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT_NAME, WEBHOOK_URL]):
    logger.critical("ERRORE: Una o più variabili d'ambiente non sono state impostate. Il bot non può avviarsi.")
    exit()
    
# --- Configurazione del Client OpenAI per Azure ---
# Nota le differenze rispetto alla configurazione standard!
client = openai.AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_KEY,
    api_version="2023-12-01-preview"  # Usa una versione API stabile
)

# --- FUNZIONI DEL BOT (logica di business) ---
# Queste funzioni rimangono molto simili

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ciao! Sono un assistente basato su Azure OpenAI. Come posso aiutarti?")

async def get_ai_response(user_message: str) -> str:
    """Funzione che interroga Azure OpenAI e restituisce la risposta."""
    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT_NAME,  # IMPORTANTE: usa il nome del deployment
            messages=[
                {"role": "system", "content": "Sei un assistente virtuale professionale ospitato su Microsoft Azure. Fornisci risposte accurate e sicure."},
                {"role": "user", "content": user_message},
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Errore nella chiamata ad Azure OpenAI: {e}")
        return "Mi dispiace, si è verificato un problema interno. Il team tecnico è stato notificato."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce il messaggio dell'utente e risponde."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    ai_response = await get_ai_response(update.message.text)
    await update.message.reply_text(ai_response)

# --- CONFIGURAZIONE WEBHOOK (con FastAPI e Uvicorn) ---

# Inizializza l'applicazione Telegram
telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Inizializza l'applicazione web FastAPI
fastapi_app = FastAPI()

@fastapi_app.on_event("startup")
async def startup_event():
    await telegram_app.initialize() # Inizializza l'app di telegram
    webhook_info = await telegram_app.bot.get_webhook_info()
    
    # Imposta il webhook solo se non è già impostato correttamente
    webhook_full_url = f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
    if webhook_info.url != webhook_full_url:
        await telegram_app.bot.set_webhook(url=webhook_full_url)
        logger.info(f"Webhook impostato su {webhook_full_url}")
    else:
        logger.info(f"Webhook già impostato correttamente su {webhook_info.url}")

# AGGIUNTO: Evento di shutdown di FastAPI
# Questo codice viene eseguito quando il server si spegne in modo pulito.
@fastapi_app.on_event("shutdown")
async def shutdown_event():
    logger.info("Spegnimento del server, pulizia del webhook...")
    await telegram_app.bot.delete_webhook()
    await telegram_app.shutdown() # Chiude correttamente l'app di telegram

@fastapi_app.post(f"/{TELEGRAM_TOKEN}")
async def telegram_webhook(request: Request):
    update_data = await request.json()
    # MODIFICATO: Usiamo il context manager per processare l'update in modo sicuro
    async with telegram_app:
        update = Update.de_json(data=update_data, bot=telegram_app.bot)
        await telegram_app.process_update(update)
    return {"status": "ok"}

@fastapi_app.get("/")
async def index():
    return "Ciao! Sono il server del bot, sono attivo e funzionante."

# RIMOSSO: La funzione main() non è più necessaria in questa forma.
# L'avvio è gestito da Uvicorn e gli eventi di startup/shutdown.

# RIMOSSO/MODIFICATO: Il blocco if __name__ == "__main__"
# Ora serve solo per il testing locale, in un modo più standard.
if __name__ == "__main__":
    # Per avviare in locale:
    # 1. Crea un file .env con le tue chiavi.
    # 2. Assicurati che WEBHOOK_URL nel .env punti a un tunnel (es. ngrok).
    # 3. Esegui questo file: python bot.py
    # uvicorn si occuperà di avviare l'app e gli eventi di startup/shutdown.
    uvicorn.run(
        "bot:fastapi_app",
        host="0.0.0.0",
        port=8000,
        reload=True # `reload=True` è ottimo per lo sviluppo locale
    )
