import os
import logging
import openai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from fastapi import FastAPI, Request
import uvicorn

# ... (tutta la parte di configurazione iniziale rimane identica) ...
# --- CONFIGURAZIONE e VARIABILI D'AMBIENTE ---
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not all([TELEGRAM_TOKEN, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT_NAME, WEBHOOK_URL]):
    logger.critical("ERRORE: Una o più variabili d'ambiente non sono state impostate. Il bot non può avviarsi.")
    exit(1)

client = openai.AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_KEY,
    api_version="2023-12-01-preview"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ciao! Sono un assistente basato su Azure OpenAI. Come posso aiutarti?")

async def get_ai_response(user_message: str) -> str:
    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT_NAME,
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
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    ai_response = await get_ai_response(update.message.text)
    await update.message.reply_text(ai_response)

telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

fastapi_app = FastAPI()

# --- MODIFICATO: Startup Event ---
@fastapi_app.on_event("startup")
async def startup_event():
    """
    MODIFICATO: Rimuoviamo il controllo e impostiamo il webhook OGNI VOLTA che il server parte.
    Questo "pulisce" qualsiasi configurazione vecchia o errata sui server di Telegram.
    """
    await telegram_app.initialize()
    webhook_full_url = f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
    
    logger.info(f"Forzando l'impostazione del webhook su: {webhook_full_url}")
    await telegram_app.bot.set_webhook(url=webhook_full_url, allowed_updates=Update.ALL_TYPES)
    # Controlliamo che sia stato impostato correttamente dopo la nostra chiamata
    webhook_info = await telegram_app.bot.get_webhook_info()
    logger.info(f"Webhook ora impostato su: {webhook_info.url}")


@fastapi_app.on_event("shutdown")
async def shutdown_event():
    logger.info("Spegnimento del server, pulizia del webhook...")
    await telegram_app.bot.delete_webhook()
    await telegram_app.shutdown()

# --- MODIFICATO: Endpoint Webhook ---
@fastapi_app.post(f"/{TELEGRAM_TOKEN}")
async def telegram_webhook(request: Request):
    """Endpoint che riceve gli aggiornamenti da Telegram."""
    # AGGIUNTO: Log di diagnostica per essere sicuri che la richiesta arrivi
    logger.info("Webhook ricevuto! Inizio elaborazione...")
    try:
        update_data = await request.json()
        logger.info(f"Dati ricevuti: {update_data}") # Log opzionale ma utile
        
        async with telegram_app:
            update = Update.de_json(data=update_data, bot=telegram_app.bot)
            await telegram_app.process_update(update)
        
        logger.info("Elaborazione completata con successo.")
        return {"status": "ok"}
    except Exception as e:
        # Se qualcosa va storto qui, lo vedremo nei log
        logger.error(f"Errore durante l'elaborazione del webhook: {e}")
        return {"status": "error"}

@fastapi_app.get("/")
async def index():
    """Endpoint di controllo per vedere se il server è attivo."""
    return "Ciao! Sono il server del bot, sono attivo e funzionante."

# ... (il blocco if __name__ == "__main__" rimane uguale)
if __name__ == "__main__":
    uvicorn.run(
        "bot:fastapi_app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
