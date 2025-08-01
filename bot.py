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
import gspread
from google.oauth2.service_account import Credentials
from gspread_asyncio import AsyncioGspreadClientManager
import json


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
    "https://www.amazon.com/review/create-review/ref=cm_cr_dp_d_wr_but_top?ie=UTF8&channel=glance-detail&asin=B0DST3L9WP",
    "https://www.amazon.com/review/create-review/ref=cm_cr_dp_d_wr_but_top?ie=UTF8&channel=glance-detail&asin=B0DRG93HJN",
    "https://www.amazon.com/review/create-review/ref=cm_cr_dp_d_wr_but_top?ie=UTF8&channel=glance-detail&asin=B0DRSKDRXP",
    "https://www.amazon.com/review/create-review/ref=cm_cr_dp_d_wr_but_top?ie=UTF8&channel=glance-detail&asin=B0DYWRSSF6",
    "https://www.amazon.com/review/create-review/ref=cm_cr_dp_d_wr_but_top?ie=UTF8&channel=glance-detail&asin=B0DP7K9D4T",
]
# Path del file PDF della guida ufficiale
GUIDE_PDF_PATH = "Arc Team Guide & Policy (1).pdf" # Assicurati che questo file sia nella stessa cartella del bot

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
    You are Luciano, the Review Manager for the ARC Team. You are a human, never a bot or AI. Your communication style is professional, encouraging, and crystal clear. You use American English exclusively. Your mission is to guide new applicants through a one-time eligibility test.

# USER CONTEXT
- **User's First Name:** {user_first_name}
- **User's Current State:** {user_state}
- **User's Assigned Test Link:** {active_link}

# KNOWLEDGE BASE & FAQ
You must answer questions based on this exact knowledge.

- **"Where do I send the screenshot?"**: The user should send it directly to you in this chat.
- **"Where is the link?" / "Link not working?"**: The test link was in your first message. Re-paste it for them by referencing the `{active_link}` variable. Tell them it's a unique link to the Amazon test page.
- **"How/when do I get paid?"**: Explain that this first test review is an *unpaid eligibility check*. Payments for the main program happen after reviewing a batch of 3 books. You can offer to send the official guide for more details by including the special string `[SEND_GUIDE_PDF]` in your response.
- **"Amazon won't let me leave a review"**: This is usually due to Amazon's minimum spend requirement. The user's best option is to use an account from a friend or family member that is eligible. Advise against making purchases just to qualify.
- **"Can I refer people?"**: Yes, for $2 per successful referral. A referral is successful only after they pass the same test (screenshot + valid TG username). Mention that more details will be provided after they join.
- **"Where is the submit button?"**: Explain that the `/submit` command is a feature of the *main program* inside the private channel. Gently refocus them on the current task: sending the screenshot for this test.

# KNOWLEDGE BASE
- **Active Test Link for this user:** {active_link}
- **Official Guide PDF:** A PDF guide explaining the main ARC program. You send this when a user asks about earnings, payments, or the program's general workflow.
- **How to leave a review and take a screenshot:** You know the steps: 1. Click the link. 2. Write the review on the Amazon page. 3. Use their phone/computer's built-in screenshot function (e.g., Power + Volume Down on Android, Side Button + Volume Up on iPhone) to capture an image of the submitted review. 4. Send that image back here.

# CORE PROCEDURE: ONBOARDING A NEW REVIEWER
You keep track of each user's state.

## STATE: AWAITING_SCREENSHOT
- **Your Primary Goal:** Gently guide the user to submit the screenshot.
- **Your Secondary Goal:** Be genuinely helpful. If the user is confused or asks for help, DO NOT just repeat the primary goal. Instead, break down the task for them and address their specific question.
- After answering, always nudge them back to the main task. Example: "...so for now, let's just focus on getting that screenshot sent over."

## STATE: awaiting_username
- The user has already sent a screenshot. Do not talk about the screenshot anymore.
- Your only goal is to get their public, all-lowercase Telegram username.
- If they ask why, explain it's so the manager can find them and add them to the private channel.

## STATE: awaiting_verification
- The user has completed all steps.
- Your only response should be a polite message confirming that everything has been received and is under review. Example: "I've got everything I need! Your application is now with our team for final review. We'll get back to you here shortly. Thanks for your patience!"

## STATE: expired
- The user took more than 24 hours.
- Politely but firmly inform them that the window has closed and the spot was given to someone else. Do not offer another chance.

- **Handling User Questions:**
    - **If the user asks "how does this work?" or "what do I do?":** Don't just say "send the screenshot". Explain the steps simply. Example: "Of course! Here’s a simple breakdown: 1. First, click the link I sent you to go to the Amazon review page. 2. Write a short, positive review there. 3. Once it's submitted, just take a screenshot of it and send it back to me in this chat. Let me know which step you're stuck on!"
    - **If the user asks about payment/earnings/program:** Your response MUST contain the special string `[SEND_GUIDE_PDF]`. Example: "Great question! This guide explains everything about how payments and the main program work: [SEND_GUIDE_PDF]. After you've completed this first test step, you'll be on your way to that!"
    - **If the user says they don't know HOW to take a screenshot:** Briefly explain the common methods for their likely device (phone). Example: "No problem! On most phones, you can take a screenshot by pressing the Power and Volume Down buttons at the same time. Once you have the image, just attach it here."
    - **If the user asks any other relevant question:** Answer it helpfully. Always try to end your helpful answer with a gentle nudge back to the main task. Example: "...and that's why we do this test. So, whenever you're ready, just send over that screenshot!"
    """
    
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

# --- FUNZIONI DI INTERAZIONE CON GOOGLE SHEETS (NUOVA SEZIONE) ---

# --- FUNZIONI DI INTERAZIONE CON GOOGLE SHEETS (VERSIONE OPERATIVA ASINCRONA) ---

# Nome del nostro foglio di lavoro
SPREADSHEET_NAME = "ARC TEAM DATI"

def get_google_creds():
    """Carica le credenziali di Google dalla variabile d'ambiente."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file"
    ]
    google_creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not google_creds_json_str:
        logger.error("SHEETS_HELPER: La variabile d'ambiente 'GOOGLE_CREDENTIALS_JSON' è vuota.")
        return None
    
    try:
        google_creds_dict = json.loads(google_creds_json_str)
        creds = Credentials.from_service_account_info(google_creds_dict, scopes=scopes)
        return creds
    except json.JSONDecodeError:
        logger.error("SHEETS_HELPER: Errore nel decodificare il JSON delle credenziali. Controlla il valore incollato su Render.")
        return None

# Creiamo un gestore del client asincrono che useremo in tutte le nostre funzioni

agc_manager = AsyncioGspreadClientManager(get_google_creds)

async def find_user_by_email(email: str):
    """
    Cerca un utente nel foglio tramite email.
    Restituisce un oggetto 'Cell' di gspread se trovato, altrimenti None.
    """
    logger.info(f"SHEETS: Inizio ricerca per email: {email}")
    try:
        agc = await agc_manager.authorize()
        spreadsheet = await agc.open(SPREADSHEET_NAME)
        worksheet = await spreadsheet.get_worksheet(0) # 0 per il primo foglio
        
        # ATTENZIONE: Assumiamo che l'email sia nella colonna D (la quarta colonna).
        # Cambia il valore di 'in_column=4' se la tua colonna email è diversa (A=1, B=2, C=3, ecc.)
        cell = await worksheet.find(email, in_column=4)
        logger.info(f"SHEETS: Trovato utente per email '{email}' nella riga {cell.row}.")
        return cell
    except gspread.SpreadsheetNotFound:
        logger.error(f"SHEETS: CRITICO! Il foglio con nome '{SPREADSHEET_NAME}' non è stato trovato. Controlla il nome esatto e la condivisione.")
        return None 
    except gspread.CellNotFound:
        logger.info(f"SHEETS: Nessun utente trovato con l'email '{email}'. Procedura normale per un nuovo utente.")
        return None
    except Exception as e:
        logger.error(f"SHEETS: Errore generico durante la ricerca dell'utente: {type(e).__name__} - {e}")
        return None
        
async def create_new_user(email: str, telegram_username: str, telegram_id: int):
    """
    Aggiunge una nuova riga per un nuovo utente al foglio.
    """
    logger.info(f"SHEETS: Inizio creazione nuovo utente per email: {email}")
    try:
        agc = await agc_manager.authorize()
        spreadsheet = await agc.open(SPREADSHEET_NAME)
        worksheet = await spreadsheet.get_worksheet(0)
        
        # PERSONALIZZA QUESTA LISTA! L'ordine deve corrispondere alle tue colonne.
# Versione aggiornata basata sullo screenshot del foglio.
        new_row = [
            # Colonna A: Data RICHIESTA INGRESSO ARC Team (la lasciamo vuota, può essere riempita manualmente o con uno script del foglio)
            "", 
            
            # Colonna B: Mail Pay Pal (non la conosciamo ancora, la chiediamo dopo)
            "",
            
            # Colonna C: Recensori (Nome completo, non lo conosciamo ancora)
            "", 
            
            # Colonna D: Mail Personale (questa la conosciamo, la passiamo alla funzione)
            email, 
            
            # Colonna E: @username (la conosciamo, la passiamo alla funzione)
            telegram_username, 
            
            # Colonne da F a L (lasciamo vuote)
            "", # F
            "", # G
            "", # H
            "", # I
            "", # J
            "", # K
            "", # L
            
            # Colonna M: ABILITATO AL BOT (Lasciamo vuoto per ora)
            "",
            
            # Colonna N: ONBOARDING (Impostiamo lo stato iniziale)
            "IN ATTESA DI TEST", 
        ]
        
        await worksheet.append_row(new_row)
        logger.info(f"SHEETS: Nuovo utente creato con successo per l'email '{email}'.")
        return True
    except Exception as e:
        logger.error(f"SHEETS: Errore durante la creazione del nuovo utente: {type(e).__name__} - {e}")
        return False

async def update_user_status(row_number: int, new_status: str):
    """
    Aggiorna lo stato di un utente in una specifica riga.
    """
    logger.info(f"SHEETS: Inizio aggiornamento stato a '{new_status}' per riga {row_number}")
    try:
        agc = await agc_manager.authorize()
        spreadsheet = await agc.open(SPREADSHEET_NAME)
        worksheet = await spreadsheet.get_worksheet(0)
        
        # ATTENZIONE: Assumiamo che lo stato sia nella colonna N (la 14esima colonna).
        # Cambia il valore '14' se la tua colonna stato ("ONBOARDING") è diversa.
        await worksheet.update_cell(row_number, 14, new_status)
        logger.info(f"SHEETS: Stato aggiornato a '{new_status}' per la riga {row_number}.")
        return True
    except Exception as e:
        logger.error(f"SHEETS: Errore durante l'aggiornamento dello stato per riga {row_number}: {type(e).__name__} - {e}")
        return False
        
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
    """
    Gestisce il primo contatto di un nuovo utente.
    NUOVA LOGICA: Chiede l'email, controlla il foglio Google, e poi procede.
    """
    user = update.effective_user
    logger.info(f"Nuovo contatto: {user.full_name} (ID: {user.id}). Inizio procedura di onboarding.")
    
    # 1. Impostiamo uno stato intermedio per sapere che stiamo aspettando l'email
    context.user_data['state'] = 'awaiting_email'
    context.user_data['telegram_username'] = f"@{user.username}" if user.username else ""
    context.user_data['telegram_id'] = user.id

    # 2. Invia il messaggio di richiesta dell'email
    await update.message.reply_text(
        "Hi! I'm Luciano, Review Manager for the ARC Team. Welcome!\n\n"
        "To get started, please send me the email address you used to sign up."
    )

# --- NUOVA FUNZIONE PER GESTIRE L'EMAIL ---

async def handle_email_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gestisce la ricezione dell'email. Cerca l'utente: se lo trova, procede;
    se non lo trova, lo crea e poi procede.
    """
    user = update.effective_user
    email_text = update.message.text.strip().lower()

    if '@' not in email_text or '.' not in email_text.split('@')[1]:
        await update.message.reply_text("That doesn't look like a valid email address. Please try again.")
        return

    await update.message.reply_text("Perfect, thank you. One moment while I check our records...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    logger.info(f"SHEETS: Inizio la ricerca dell'utente con email '{email_text}'")
    user_cell = await find_user_by_email(email_text)
    
    sheet_row_number = None

    if user_cell:
        # --- UTENTE ESISTENTE ---
        sheet_row_number = user_cell.row
        logger.info(f"SHEETS: Utente con email '{email_text}' trovato alla riga {sheet_row_number}. Aggiorno lo stato.")
        await update_user_status(sheet_row_number, "TEST INVIATO")
    else:
        # --- NUOVO UTENTE ---
        logger.info(f"SHEETS: Utente con email '{email_text}' non trovato. Procedo con la creazione.")
        success = await create_new_user(
            email=email_text,
            telegram_username=context.user_data.get('telegram_username', f"@{user.username}"),
            telegram_id=user.id
        )
        if success:
            logger.info(f"SHEETS: Creazione riuscita. Ora cerco di nuovo l'utente per ottenere la sua riga.")
            # Dobbiamo cercarlo di nuovo per sapere su quale riga è stato appena aggiunto
            new_user_cell = await find_user_by_email(email_text)
            if new_user_cell:
                sheet_row_number = new_user_cell.row
                logger.info(f"SHEETS: Nuovo utente trovato alla riga {sheet_row_number}.")
            else:
                logger.error(f"SHEETS: CRITICO! Ho creato l'utente con email '{email_text}' ma non riesco a ritrovarlo.")
        else:
            logger.error(f"SHEETS: CRITICO! La funzione create_new_user ha fallito per l'email '{email_text}'.")

    # --- SE ABBIAMO UNA RIGA, PROCEDIAMO ---
    if sheet_row_number:
        context.user_data['sheet_row'] = sheet_row_number # Fondamentale per gli aggiornamenti futuri!

        assigned_link = await get_next_test_link(context)
        context.user_data['state'] = 'awaiting_screenshot'
        context.user_data['first_name'] = user.first_name
        context.user_data['assigned_link'] = assigned_link

        welcome_message = f"""Great news, {user.first_name}! You are now registered.

To be added to our private Telegram channel, we first need to confirm that your Amazon account can leave reviews.

Here's what to do:
1. **Click this link:** {assigned_link}
2. Leave a 5-star, positive review.
3. Take a screenshot showing your submitted review.
4. **Reply to this message with your screenshot.**

You have 24 hours to complete this test. I'm here to help if you have any questions!
"""
        await update.message.reply_text(welcome_message)

        context.job_queue.run_once(reminder_job, 23 * 3600, chat_id=update.effective_chat.id, user_id=user.id, name=f"reminder_{user.id}", data={'first_name': user.first_name})
        context.job_queue.run_once(expiration_job, 24 * 3600, chat_id=update.effective_chat.id, user_id=user.id, name=f"expire_{user.id}")
    else:
        # Se siamo qui, qualcosa è andato storto nella comunicazione con Google Sheets
        await update.message.reply_text(
            "I'm having some trouble accessing my records at the moment. Please try again in a little while."
        )

# --- NUOVA VERSIONE DI handle_photo ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gestisce la ricezione dello screenshot.
    NUOVO FLUSSO: Chiede l'username di Telegram invece di notificare l'admin.
    """
    user = update.effective_user
    context.user_data['photo_message_id'] = update.message.message_id
    logger.info(f"Photo received from user {user.id}. Now asking for username.")
    
    # 1. Cambia lo stato per aspettare l'username
    context.user_data['state'] = 'awaiting_username'
    
    # 2. Rimuovi i job di sollecito/scadenza
    current_jobs = context.job_queue.get_jobs_by_name(f"reminder_{user.id}")
    for job in current_jobs:
        job.schedule_removal()
    current_jobs = context.job_queue.get_jobs_by_name(f"expire_{user.id}")
    for job in current_jobs:
        job.schedule_removal()

    # 3. Invia il messaggio di richiesta dell'username
    username_request_message = """Great, I've received your screenshot!

    Just one final step: please send me your Telegram username.
    
    **IMPORTANT:**
    1.  Your username must be **public**.
    2.  It must be written in **all lowercase letters** (e.g., @johndoe, not @JohnDoe).
    
    This is crucial so our manager can find you and add you to the private channel.
    
    **--> How to set up your public username:**
    Go to Telegram Settings > Edit Profile > Username. If it's empty, create one. Make sure it's all lowercase!
    
    Please type and send your username below."""
        
    await update.message.reply_text(username_request_message)

    
    # Invia notifica all'admin
    if ADMIN_CHAT_ID:
        try:
            admin_notification = f"📸 Screenshot received from user {user.full_name} (@{user.username}, ID: {user.id}). Ready for verification."
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_notification)
            # Inoltra anche la foto per una verifica più rapida
            await context.bot.forward_message(chat_id=ADMIN_CHAT_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        except Exception as e:
            logger.error(f"Failed to send notification to admin: {e}")

# --- NUOVA FUNZIONE: handle_username ---
async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gestisce la ricezione e la validazione dell'username di Telegram.
    Se valido, invia la notifica completa all'admin.
    """
    user = update.effective_user
    username_text = update.message.text.strip() # Rimuove spazi extra
    
    
    # --- Validazione dell'Username ---
    # 1. Deve iniziare con '@'
    # 2. Deve essere tutto minuscolo
    # 3. Deve essere lungo almeno 6 caratteri (@ + 5)
    if (not username_text.startswith('@') or
        not username_text[1:].islower() or
        len(username_text) < 6):
        
        validation_error_message = """It seems there's a small issue with the username. Please double-check:

- It must start with **@**
- It must be **all lowercase**
- It must be at least 5 characters long (plus the @)

For example: `@johndoe`

Please try sending it again."""
        await update.message.reply_text(validation_error_message)
        return # Esce dalla funzione, aspettando un nuovo tentativo dall'utente

    # --- Se la validazione passa ---
    logger.info(f"Username {username_text} received and validated for user {user.id}.")
    
    # 1. Salva l'username e cambia lo stato finale
    context.user_data['telegram_username'] = username_text
    context.user_data['state'] = 'awaiting_verification'

    # 2. Messaggio di conferma all'utente
    await update.message.reply_text("Perfect, thank you! I've got everything I need. Your application is now with our team for final review. We'll get back to you here shortly. Thanks for your patience!")

    # 3. Invia la notifica completa all'admin
    if ADMIN_CHAT_ID:
        try:
            # Recupera l'ID del messaggio della foto per inoltrarlo
            # Nota: questo è un approccio semplificato. Funziona se la foto è stata l'ultimo messaggio.
            # Per renderlo robusto, dovremmo salvare il message_id della foto in user_data.
            # Per ora, manteniamolo semplice.
            admin_notification = f"""✅ New Applicant Ready for Verification ✅

**User:** {user.full_name}
**User ID:** `{user.id}`
**Provided TG Username:** `{username_text}`

Screenshot is attached below.
"""
            # Invia la notifica di testo
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_notification, parse_mode='Markdown')
            
            # Ora inoltra la foto (assumiamo che possiamo trovarla, potremmo doverla salvare)
            # Per renderlo affidabile, modifichiamo un attimo handle_photo
            photo_message_id = context.user_data.get('photo_message_id')
            if photo_message_id:
                await context.bot.forward_message(chat_id=ADMIN_CHAT_ID, from_chat_id=update.effective_chat.id, message_id=photo_message_id)
            else:
                 await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="Error: Could not retrieve screenshot message ID to forward.")

        except Exception as e:
            logger.error(f"Failed to send final notification to admin: {e}")

# Per far funzionare l'inoltro della foto, facciamo una piccola aggiunta a handle_photo
# Torna a handle_photo e aggiungi questa riga dopo aver definito l'utente:
# context.user_data['photo_message_id'] = update.message.message_id

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
        
# --- NUOVA VERSIONE DEL dispatcher ---
async def dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Funzione principale che smista i messaggi in base allo stato."""
    user = update.effective_user
    user_state = context.user_data.get('state', 'new_user')

    message_type = "text" if update.message.text else "photo" if update.message.photo else "other"
    logger.info(f"[INPUT] User: {user.id} ({user.full_name}) | State: {user_state} | Type: {message_type}")
    
    # Gestisce qualsiasi messaggio di un nuovo utente
    if user_state == 'new_user' and update.message.text:
        await handle_new_user(update, context)
        return
        
  # --- NUOVO BLOCCO PER GESTIRE LA SOTTOMISSIONE DELL'EMAIL ---
    elif user_state == 'awaiting_email':
        if update.message.text:
            await handle_email_submission(update, context)
        else:
            await update.message.reply_text("Please send me your email address to continue.")
            
    # Stato: in attesa dello screenshot
    if user_state == 'awaiting_screenshot':
        if update.message.photo:
            await handle_photo(update, context)
        elif update.message.text:
            await handle_text_message(update, context)
        else:
            await update.message.reply_text("Please send me your screenshot to continue, or ask a question if you're stuck!")

    # NUOVO STATO: in attesa dell'username
    elif user_state == 'awaiting_username':
        if update.message.text:
            await handle_username(update, context)
        else:
            await update.message.reply_text("Please send me your Telegram username as plain text to continue.")
            
    # Stato: in attesa di verifica o scaduto
    elif user_state in ['awaiting_verification', 'expired']:
        if update.message.text:
            # L'AI gestirà la risposta basandosi sul prompt aggiornato
            await handle_text_message(update, context)
        else:
             await update.message.reply_text("I've received your submission and it's in the queue for review. Thanks for your patience!")

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

# NUOVO CODICE - CORRETTO
@fastapi_app.on_event("startup")
async def startup_event():
    
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}", allowed_updates=Update.ALL_TYPES)
    # Avviamo la JobQueue. È sicuro chiamarlo direttamente.
    await telegram_app.job_queue.start()
    logger.info("Bot started and webhook set.")

# NUOVO CODICE - CORRETTO
@fastapi_app.on_event("shutdown")
async def shutdown_event():
    # Stoppiamo la JobQueue. È sicuro chiamarlo direttamente.
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
