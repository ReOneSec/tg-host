import os
import logging
import tempfile
import zipfile
import re
from datetime import datetime
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from dotenv import load_dotenv
import pyrebase

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Firebase configuration from .env
firebase_config = {
    "apiKey": os.getenv("FIREBASE_API_KEY"),
    "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
    "projectId": os.getenv("FIREBASE_PROJECT_ID"),
    "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
    "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID"),
    "appId": os.getenv("FIREBASE_APP_ID"),
    "measurementId": os.getenv("FIREBASE_MEASUREMENT_ID"),
    "databaseURL": os.getenv("FIREBASE_DATABASE_URL")
}

# Initialize Firebase
firebase = pyrebase.initialize_app(firebase_config)
storage = firebase.storage()

# Constants
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_EXTENSIONS = ('.html', '.zip')
USER_STORAGE_LIMIT = 10

# In-memory user file storage
user_files = defaultdict(list)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📤 Upload File", callback_data='upload')],
        [InlineKeyboardButton("📁 My Files", callback_data='files')],
        [InlineKeyboardButton("❌ Delete File", callback_data='delete')],
        [
            InlineKeyboardButton("ℹ️ Help", callback_data='help'),
            InlineKeyboardButton("📬 Contact", url="https://t.me/ViperROX")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 *Welcome to the HTML Hosting Bot!*\n\n"
        "Host static websites with instant public links. Supported formats: HTML/ZIP",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    file = update.message.document

    if not file.file_name.lower().endswith(ALLOWED_EXTENSIONS):
        await update.message.reply_text("⚠️ Only .html or .zip files are supported.")
        return

    if file.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("⚠️ File exceeds 5MB limit.")
        return

    if len(user_files[user_id]) >= USER_STORAGE_LIMIT:
        await update.message.reply_text("⚠️ You've reached your storage limit.")
        return

    try:
        file_path = tempfile.mktemp()
        telegram_file = await file.get_file()
        await telegram_file.download_to_drive(file_path)

        if file.file_name.lower().endswith('.zip'):
            extract_path = tempfile.mkdtemp()
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
            html_files = [f for f in os.listdir(extract_path) if f.lower().endswith('.html')]
            if not html_files:
                raise ValueError("No HTML files found in ZIP archive.")
            file_path = os.path.join(extract_path, html_files[0])
            file_name = html_files[0]
        else:
            file_name = file.file_name

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        firebase_path = f"uploads/{user_id}/{timestamp}_{file_name}"
        storage.child(firebase_path).put(file_path)
        url = storage.child(firebase_path).get_url(None)

        user_files[user_id].append({
            "name": file_name,
            "path": firebase_path,
            "url": url,
            "timestamp": timestamp,
            "size": file.file_size
        })

        await update.message.reply_text(
            f"✅ *Upload Successful!*\n\n"
            f"📄 File: `{file_name}`\n"
            f"🔗 [View File]({url})",
            parse_mode='Markdown',
            disable_web_page_preview=False
        )

    except Exception as e:
        logger.error(f"Upload error for {user_id}: {str(e)}")
        await update.message.reply_text(f"❌ Upload failed: {str(e)}")
    finally:
        if 'file_path' in locals() and os.path.exists(file_path):
            os.remove(file_path)
        if 'extract_path' in locals() and os.path.exists(extract_path):
            os.rmdir(extract_path)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(query.from_user.id)

    try:
        if data == 'upload':
            await query.message.reply_text("📤 Please send an HTML/ZIP file (max 5MB).")

        elif data == 'files':
            files = user_files.get(user_id, [])
            if not files:
                await query.edit_message_text("📁 You don't have any files yet.")
                return

            file_list = "\n".join(
                [f"• [{f['name']}]({f['url']}) ({f['size']//1024}KB)"
                 for f in files]
            )
            await query.edit_message_text(
                f"📂 *Your Files ({len(files)}/{USER_STORAGE_LIMIT}):*\n\n{file_list}",
                parse_mode='Markdown',
                disable_web_page_preview=True
            )

        elif data == 'delete':
            files = user_files.get(user_id, [])
            if not files:
                await query.edit_message_text("❌ No files to delete.")
                return

            buttons = [
                [InlineKeyboardButton(f"🗑 {f['name']}", callback_data=f"delete_{i}")]
                for i, f in enumerate(files)
            ]
            buttons.append([InlineKeyboardButton("🔙 Back", callback_data='start')])
            await query.edit_message_text("Select a file to delete:", reply_markup=InlineKeyboardMarkup(buttons))

        elif data.startswith("delete_"):
            index = int(data.split("_")[1])
            files = user_files.get(user_id, [])
            if 0 <= index < len(files):
                file_info = files.pop(index)
                storage.delete(file_info['path'], None)
                await query.edit_message_text(f"✅ `{file_info['name']}` deleted.")
            else:
                await query.edit_message_text("⚠️ Invalid selection.")

        elif data == 'help':
            help_text = (
                "ℹ️ *How to Use the Bot:*\n\n"
                "1. Send an HTML or ZIP file (with index.html).\n"
                "2. Get a public link to view it online.\n"
                "3. Manage your files with the menu.\n\n"
                "⚠️ ZIP files must include `index.html` at root."
            )
            await query.edit_message_text(help_text, parse_mode='Markdown')

        elif data == 'start':
            await start(update, context)

    except Exception as e:
        logger.error(f"Button handler error: {str(e)}")
        await query.edit_message_text("⚠️ An error occurred. Please try again.")


if __name__ == '__main__':
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot is running...")
    app.run_polling()
