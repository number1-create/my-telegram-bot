import os
import logging
import openai
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    PersistenceInput,
    BasePersistence,
    PicklePersistence,
    JobQueue,
)
from fastapi import FastAPI, Request
import uvicorn
from dotenv import load_dotenv

# --- CONFIGURAZIONE e VARIABILI D'AMBIENTE ---
load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Credenziali e configurazioni fondamentali ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # L'ID della chat dove inviare le notifiche

# --- Configurazione OpenAI ---
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
client = openai.AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_KEY,
    api_version="2023-12-01-preview"
)

# --- DATI SPECIFICI DEL BOT (Personalizza qui!) ---
# Lista di link di test. Il bot li assegnerà a rotazione.
TEST_LINKS = [
    "https://www.amazon.com/review/create-review/ref=...&asin=LINK1",
    "https://www.amazon.com/review/create-review/ref=...&asin=LINK2",
    "https://www.amazon.com/review/create-review/ref=...&asin=LINK3",
]
# Path del file PDF della guida ufficiale
GUIDE_PDF_PATH = "Official_Guide.pdf" # Assicurati che questo file sia nella stessa cartella del bot

# --- FUNZIONI DI LOGICA PRINCIPALE ---

async def get_next_test_link(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Recupera il prossimo link di test a rotazione."""
    if 'link_index' not in context.bot_data:
        context.bot_data['link_index'] = 0
    
    index = context.bot_data['link_index']
    link = TEST_LINKS[index]
    
    # Aggiorna l'indice per la prossima volta, tornando a 0 se si supera la lunghezza della lista
    context.bot_data['link_index'] = (index + 1) % len(TEST_LINKS)
    return link

async def get_ai_response(user_id: int, user_message: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Funzione principale che interroga Azure OpenAI con il contesto corretto.
    Nota: questa funzione ora riceve anche lo stato dell'utente.
    """
    user_state = context.user_data.get('state', 'new_user')
    user_first_name = context.user_data.get('first_name', 'there')
    active_link = context.user_data.get('assigned_link', 'ERROR: No link assigned')

    # Costruiamo il System Prompt dinamicamente
    system_prompt = f"""
    # IDENTITY AND ROLE
You are Luciano, the Review Manager for the ARC Team. You are a human, not a bot. Your communication style is professional, friendly, encouraging, and clear. You must communicate exclusively in American English. Your primary goal is to guide new applicants through the initial eligibility test.

# KNOWLEDGE BASE
- **Active Test Link for this user:** {active_link}
- **Official Guide PDF:** A PDF guide explaining the main ARC program. You send this when a user asks about earnings, payments, or the program's general workflow.
- **How to leave a review and take a screenshot:** You know the steps: 1. Click the link. 2. Write the review on the Amazon page. 3. Use their phone/computer's built-in screenshot function (e.g., Power + Volume Down on Android, Side Button + Volume Up on iPhone) to capture an image of the submitted review. 4. Send that image back here.

# CORE PROCEDURE: ONBOARDING A NEW REVIEWER
You keep track of each user's state.

## STATE: AWAITING_SCREENSHOT
- **Your Primary Goal:** Gently guide the user to submit the screenshot.
- **Your Secondary Goal:** Be genuinely helpful. If the user is confused or asks for help, DO NOT just repeat the primary goal. Instead, break down the task for them and address their specific question.

- **Handling User Questions:**
    - **If the user asks "how does this work?" or "what do I do?":** Don't just say "send the screenshot". Explain the steps simply. Example: "Of course! Here’s a simple breakdown: 1. First, click the link I sent you to go to the Amazon review page. 2. Write a short, positive review there. 3. Once it's submitted, just take a screenshot of it and send it back to me in this chat. Let me know which step you're stuck on!"
    - **If the user asks about payment/earnings/program:** Your response MUST contain the special string `[SEND_GUIDE_PDF]`. Example: "Great question! This guide explains everything about how payments and the main program work: [SEND_GUIDE_PDF]. After you've completed this first test step, you'll be on your way to that!"
    - **If the user says they don't know HOW to take a screenshot:** Briefly explain the common methods for their likely device (phone). Example: "No problem! On most phones, you can take a screenshot by pressing the Power and Volume Down buttons at the same time. Once you have the image, just attach it here."
    - **If the user asks any other relevant question:** Answer it helpfully. Always try to end your helpful answer with a gentle nudge back to the main task. Example: "...and that's why we do this test. So, whenever you're ready, just send over that screenshot!"
    
    messages_to_send = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT_NAME,
            messages=messages_to_send
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Errore nella chiamata ad Azure OpenAI: {e}")
        return "I'm having a little trouble connecting right now. Let me get back to you in a moment."

# --- JOB PER LA CODA (SOLLECITI E SCADENZE) ---

async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Invia un sollecito dopo 23 ore."""
    job = context.job
    await context.bot.send_message(
        chat_id=job.chat_id,
        text=f"Hi {job.data['first_name']}, just a friendly reminder that you have about 1 hour left to submit your review screenshot to secure your spot in the ARC program. You've got this! 👍"
    )

async def expiration_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Imposta lo stato dell'utente a 'expired' dopo 24 ore."""
    job = context.job
    # Accediamo ai dati dell'utente tramite il persistence layer
    user_data = await context.application.persistence.get_user_data()
    if job.user_id in user_data and user_data[job.user_id].get('state') == 'awaiting_screenshot':
        user_data[job.user_id]['state'] = 'expired'
        await context.application.persistence.update_user_data(job.user_id, user_data[job.user_id])
        logger.info(f"User {job.user_id} has expired.")

# --- GESTORI DI MESSAGGI (HANDLERS) ---

async def handle_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestisce il primo contatto di un nuovo utente."""
    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    
    logger.info(f"New user contact: {user.full_name} (ID: {user_id})")
    
    # Assegna il link e salva lo stato iniziale
    assigned_link = await get_next_test_link(context)
    context.user_data['state'] = 'awaiting_screenshot'
    context.user_data['first_name'] = user.first_name
    context.user_data['assigned_link'] = assigned_link

    # Invia il messaggio di benvenuto e le istruzioni
    welcome_message = f"""Hi {user.first_name}, I'm Luciano, Review Manager for the ARC Team. Welcome!

Great news, you've passed the initial screening to join our ARC team! To be added to our private Telegram channel (where you'll receive early copies of upcoming titles), we first need to confirm that your Amazon account can leave reviews.

Here's what to do:
1. **Click this link:** {assigned_link}
2. Leave a 5-star, positive, empowering review on the page. It doesn't have to be long, just a few positive sentences.
3. Take a screenshot showing your submitted review.
4. **Reply to this message with your screenshot.**

You have 24 hours to complete this test. After that, your application spot may be given to another candidate to ensure fairness for everyone.

I'm here to help if you have any questions. Looking forward to having you on board!"""
    await update.message.reply_text(welcome_message)
    
    # Programma i job di sollecito e scadenza
    context.job_queue.run_once(reminder_job, 23 * 3600, chat_id=chat_id, user_id=user_id, name=f"reminder_{user_id}", data={'first_name': user.first_name})
    context.job_queue.run_once(expiration_job, 24 * 3600, chat_id=chat_id, user_id=user_id, name=f"expire_{user_id}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestisce la ricezione di uno screenshot."""
    user = update.effective_user
    user_id = user.id
    
    logger.info(f"Photo received from user {user_id}")
    
    # Cambia stato
    context.user_data['state'] = 'awaiting_verification'
    
    # Rimuovi i job di sollecito/scadenza perché non più necessari
    current_jobs = context.job_queue.get_jobs_by_name(f"reminder_{user_id}")
    for job in current_jobs:
        job.schedule_removal()
    current_jobs = context.job_queue.get_jobs_by_name(f"expire_{user_id}")
    for job in current_jobs:
        job.schedule_removal()
        
    await update.message.reply_text("Thanks! I've received your screenshot. I will personally review it shortly. I'll get back to you right here as soon as it's verified. This usually takes just a few hours.")
    
    # Invia notifica all'admin
    if ADMIN_CHAT_ID:
        try:
            admin_notification = f"📸 Screenshot received from user {user.full_name} (@{user.username}, ID: {user_id}). Ready for verification."
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_notification)
            # Inoltra anche la foto per una verifica più rapida
            await context.bot.forward_message(chat_id=ADMIN_CHAT_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        except Exception as e:
            logger.error(f"Failed to send notification to admin: {e}")

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestisce i messaggi di testo degli utenti in stato 'awaiting_screenshot'."""
    user = update.effective_user
    user_id = user.id
    
    if context.user_data.get('state') == 'expired':
        await update.message.reply_text(f"Hi {user.first_name}, unfortunately, the 24-hour window for the eligibility test has expired, and your spot has been allocated to another applicant. Thank you for your interest in the ARC Team.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    ai_response = await get_ai_response(user_id, update.message.text, context)
    
    # Controlla se l'AI vuole inviare il PDF
    if "[SEND_GUIDE_PDF]" in ai_response:
        # Rimuovi il placeholder dalla risposta prima di inviarla
        clean_response = ai_response.replace("[SEND_GUIDE_PDF]", "").strip()
        await update.message.reply_text(clean_response)
        try:
            with open(GUIDE_PDF_PATH, 'rb') as pdf_file:
                await context.bot.send_document(chat_id=update.effective_chat.id, document=pdf_file)
        except FileNotFoundError:
            logger.error(f"File PDF non trovato: {GUIDE_PDF_PATH}")
            await update.message.reply_text("I'm sorry, I can't seem to find the guide document right now. Please ask my colleague for it in the main group later.")
    else:
        await update.message.reply_text(ai_response)
        
async def dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Funzione principale che smista i messaggi in base allo stato."""
    user_state = context.user_data.get('state', 'new_user')
    
    # Se è un nuovo utente, avvia la procedura
    if user_state == 'new_user':
        await handle_new_user(update, context)
        return

    # Se l'utente è in attesa di inviare lo screenshot
    if user_state == 'awaiting_screenshot':
        if update.message.photo:
            await handle_photo(update, context)
        elif update.message.text:
            await handle_text_message(update, context)
        else:
            await update.message.reply_text("Please send me your screenshot to continue, or ask a question if you're stuck!")
            
    # Se l'utente è in attesa di verifica o scaduto, l'AI darà una risposta generica
    elif user_state in ['awaiting_verification', 'expired']:
        if update.message.text:
            await handle_text_message(update, context)
        else:
             await update.message.reply_text("I've received your submission and it's in the queue for review. I'll get back to you here. Thanks for your patience!")

# --- CONFIGURAZIONE E AVVIO (FastAPI & Uvicorn) ---
# MODIFICATO: Inizializzazione separata per un controllo migliore
persistence = PicklePersistence(filepath="./bot_persistence")
# MODIFICATO: Creiamo il builder ma non l'applicazione ancora
app_builder = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence)

# MODIFICATO: Creiamo e associamo la JobQueue esplicitamente
job_queue = JobQueue()
app_builder.job_queue(job_queue)

# Ora costruiamo l'applicazione
telegram_app = app_builder.build()
job_queue.set_application(telegram_app) # Colleghiamo la JobQueue all'app

# Aggiungiamo l'handler
telegram_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, dispatcher))

# Inizializza l'applicazione web FastAPI
fastapi_app = FastAPI()

# MODIFICATO: Gestione del ciclo di vita per evitare l'errore 'HTTPXRequest not initialized'
@fastapi_app.on_event("startup")
async def startup_event():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}", allowed_updates=Update.ALL_TYPES)
    # Avviamo la JobQueue SOLO dopo aver inizializzato l'app
    if not telegram_app.job_queue.running:
        await telegram_app.job_queue.start()
    logger.info("Bot started and webhook set.")

@fastapi_app.on_event("shutdown")
async def shutdown_event():
    # Stoppiamo la JobQueue PRIMA di chiudere l'app
    if telegram_app.job_queue.running:
        await telegram_app.job_queue.stop()
    await telegram_app.shutdown()
    logger.info("Bot shutdown.")

@fastapi_app.post(f"/{TELEGRAM_TOKEN}")
async def telegram_webhook(request: Request):
    await telegram_app.process_update(Update.de_json(await request.json(), telegram_app.bot))
    return {"status": "ok"}

@fastapi_app.get("/")
async def index():
    return "Ciao! Sono il server del bot, sono attivo e funzionante."

# Per test locale
if __name__ == "__main__":
    uvicorn.run("bot:fastapi_app", host="0.0.0.0", port=8000, reload=True)
