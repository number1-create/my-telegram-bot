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

@fastapi_app.post(f"/{TELEGRAM_TOKEN}")
async def telegram_webhook(request: Request):
    """Endpoint che riceve gli aggiornamenti da Telegram."""
    update_data = await request.json()
    update = Update.de_json(data=update_data, bot=telegram_app.bot)
    await telegram_app.process_update(update)
    return {"status": "ok"}

@fastapi_app.get("/")
async def index():
    """Endpoint di controllo per vedere se il server è attivo."""
    return "Ciao! Sono il server del bot, sono attivo e funzionante."

# La parte principale ora imposta il webhook e avvia il server web
async def main():
    # Imposta il webhook quando il server si avvia
    await telegram_app.bot.set_webhook(url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")
    logger.info(f"Webhook impostato su {WEBHOOK_URL}")

    # Configura e avvia il server web Uvicorn
    # Render imposterà automaticamente la porta e l'host
    port = int(os.environ.get('PORT', 8000))
    config = uvicorn.Config(
        "bot:fastapi_app",  # "nome_file:nome_app_fastapi"
        host="0.0.0.0",
        port=port,
        loop="asyncio"
    )
    server = uvicorn.Server(config)
    
    # Esegui il setup del webhook prima di avviare il server
    async with telegram_app:
        await telegram_app.initialize()
        await telegram_app.bot.set_webhook(url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")
        await server.serve()
        await telegram_app.bot.delete_webhook() # Pulisce il webhook quando il server si ferma
        await telegram_app.shutdown()


if __name__ == "__main__":
    # Questa parte non viene eseguita direttamente su Render,
    # ma è utile per capire come avviare il tutto.
    # Render userà il "Start Command" che definiremo.
    # Per avviare in locale:
    # 1. Crea un file .env con le tue chiavi
    # 2. Esegui: uvicorn bot:fastapi_app --reload
    pass