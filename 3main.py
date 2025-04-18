import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import asyncio
import logging
import tempfile
import zipfile
import re
from datetime import datetime
from collections import defaultdict

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
import pyrebase
import requests


# Run health check HTTP server (for uptime services)
def run_fake_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')

    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
    server.serve_forever()


threading.Thread(target=run_fake_server, daemon=True).start()


# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Firebase configuration
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

firebase = pyrebase.initialize_app(firebase_config)
storage = firebase.storage()
db = firebase.database()

# Constants
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_EXTENSIONS = ('.html', '.zip')
DEFAULT_UPLOAD_LIMIT = 10
BONUS_PER_REFERRAL = 3
TINYURL_API_KEY = "PzJOqDQMIXuTGshO8VCpscW3jzqHsCKtsBQ16MYdKJfhcP7IbRNOEkqa3mME"
BOT_USERNAME = os.getenv("BOT_USERNAME")


# Utility functions
def shorten_url(long_url):
    try:
        response = requests.post(
            'https://api.tinyurl.com/create',
            headers={'Authorization': f'Bearer {TINYURL_API_KEY}'},
            json={"url": long_url}
        )
        response.raise_for_status()
        return response.json().get('data', {}).get('tiny_url', long_url)
    except Exception as e:
        logger.warning(f"Shorten URL failed: {e}")
        return long_url


def get_upload_limit(user_id):
    referrals = db.child("referrals").child(user_id).get().val() or []
    return DEFAULT_UPLOAD_LIMIT + BONUS_PER_REFERRAL * len(referrals)


def get_referrer(user_id):
    return db.child("ref_by").child(user_id).get().val()


# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = str(user.id)

    # Process referral
    args = context.args
    if args:
        referrer_id = args[0]
        if referrer_id != user_id and not get_referrer(user_id):
            db.child("ref_by").child(user_id).set(referrer_id)
            db.child("referrals").child(referrer_id).push(user_id)

            try:
                await context.bot.send_message(
                    chat_id=int(referrer_id),
                    text=f"🎉 {user.first_name} joined via your referral link!"
                )
            except Exception:
                pass

    keyboard = [
        [InlineKeyboardButton("📤 Upload File", callback_data='upload')],
        [InlineKeyboardButton("📁 My Files", callback_data='files')],
        [InlineKeyboardButton("❌ Delete File", callback_data='delete')],
        [InlineKeyboardButton("🏆 Referral Leaderboard", callback_data='leaderboard')],
        [
            InlineKeyboardButton("ℹ️ Help", callback_data='help'),
            InlineKeyboardButton("📩 Contact Owner", url="https://t.me/ViperROX")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    await update.message.reply_text(
        f"👋 Welcome to the HTML Hosting Bot!\n\n"
        f"Host static websites with instant public links.\n"
        f"You can refer friends and get +3 extra upload slots for each!\n"
        f"Your referral link: `{link}`",
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
        await update.message.reply_text("⚠️ File size exceeds 5MB limit.")
        return

    user_data = db.child("users").child(user_id).get().val() or []
    limit = get_upload_limit(user_id)

    if len(user_data) >= limit:
        await update.message.reply_text("⚠️ Storage limit reached. Delete some files first.")
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
                raise ValueError("No HTML files found in ZIP archive")
            file_path = os.path.join(extract_path, html_files[0])
            file_name = html_files[0]
        else:
            file_name = file.file_name

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        firebase_path = f"uploads/{user_id}/{timestamp}_{file_name}"
        storage.child(firebase_path).put(file_path)
        url = storage.child(firebase_path).get_url(None)
        short_url = shorten_url(url)

        file_record = {
            "name": file_name,
            "path": firebase_path,
            "url": short_url,
            "timestamp": timestamp,
            "size": file.file_size
        }

        user_data.append(file_record)
        db.child("users").child(user_id).set(user_data)

        await update.message.reply_text(
            f"✅ *Upload Successful!*\n\n"
            f"📄 File: `{file_name}`\n"
            f"🌐 [View File]({short_url})\n"
            f"🔗 Tap to copy:- `{short_url}`",
            parse_mode='Markdown',
            disable_web_page_preview=False
        )

    except Exception as e:
        logger.error(f"Upload failed for {user_id}: {str(e)}")
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

    if data == 'upload':
        await query.edit_message_text(
            "📤 Please send an HTML/ZIP file (max 5MB)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'files':
        files = db.child("users").child(user_id).get().val() or []
        limit = get_upload_limit(user_id)
        if not files:
            await query.edit_message_text("📁 Your storage is empty")
            return
        file_list = "\n".join(
            [f"• [{f['name']}]({f['url']}) ({f['size']//1024}KB)" for f in files]
        )
        await query.edit_message_text(
            f"📂 *Your Files ({len(files)}/{limit}):*\n{file_list}",
            parse_mode='Markdown',
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'delete':
        files = db.child("users").child(user_id).get().val() or []
        if not files:
            await query.edit_message_text("❌ No files to delete")
            return
        buttons = [
            [InlineKeyboardButton(f"🗑 {f['name']}", callback_data=f"delete_{i}")]
            for i, f in enumerate(files)
        ]
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data='start')])
        await query.edit_message_text("Select file to delete:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("delete_"):
        index = int(data.split("_")[1])
        files = db.child("users").child(user_id).get().val() or []
        if 0 <= index < len(files):
            file_info = files.pop(index)
            storage.delete(file_info['path'], None)
            db.child("users").child(user_id).set(files)
            await query.edit_message_text(f"✅ `{file_info['name']}` deleted", parse_mode='Markdown')
        else:
            await query.edit_message_text("⚠️ Invalid selection")

    elif data == 'leaderboard':
        referral_data = db.child("referrals").get().val() or {}
        counts = [(uid, len(refs)) for uid, refs in referral_data.items()]
        counts.sort(key=lambda x: x[1], reverse=True)

        leaderboard = []
        for i, (uid, count) in enumerate(counts[:10], 1):
            try:
                user = await context.bot.get_chat(int(uid))
                name = user.username or user.first_name or uid
            except Exception:
                name = uid
            leaderboard.append(f"{i}. {name}: {count} referrals")

        leaderboard_text = "\n".join(leaderboard) or "No referrals yet."
        await query.edit_message_text(
            f"🏆 *Referral Leaderboard*\n\n{leaderboard_text}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'help':
        help_text = (
            "ℹ️ *Bot Guide*\n\n"
            "1. Upload HTML/ZIP files\n"
            "2. Share generated short links\n"
            "3. Invite others using your referral link to increase file limit (+3 per referral)\n\n"
            "⚠️ ZIP files must contain `index.html`"
        )
        await query.edit_message_text(
            help_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'start':
        await start(update, context)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = int(os.getenv("ADMIN_ID"))
    if update.message.from_user.id != admin_id:
        await update.message.reply_text("❌ You're not authorized to use this command.")
        return

    message = ' '.join(context.args)
    if not message:
        await update.message.reply_text("⚠️ Provide a message to broadcast.")
        return

    all_users = db.child("users").get().val() or {}
    success_count = 0
    failure_count = 0

    for uid in all_users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=message)
            success_count += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning(f"Failed to send to {uid}: {e}")
            failure_count += 1

    await update.message.reply_text(
        f"✅ Broadcast sent to {success_count} users. ❌ Failed: {failure_count}"
    )


# Main
if __name__ == '__main__':
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot is running...")
    app.run_polling()
