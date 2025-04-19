import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

def run_fake_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')

    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()

import os
import asyncio
import logging
import tempfile
import zipfile
from datetime import datetime

import requests
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
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# Helpers
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

# Start handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)

    # Process referral
    args = context.args if hasattr(context, "args") else []
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
            except:
                pass

    keyboard = [
        [InlineKeyboardButton("📤 Upload File", callback_data='upload')],
        [InlineKeyboardButton("📁 My Files", callback_data='files')],
        [InlineKeyboardButton("❌ Delete File", callback_data='delete')],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data='leaderboard')],
        [InlineKeyboardButton("ℹ️ Help", callback_data='help'),
         InlineKeyboardButton("👤 Contact Owner", url="https://t.me/ViperROX")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    
    message = update.message or update.callback_query.message
    await message.reply_text(
        f"👋 Welcome to the HTML Hosting Bot!\n\n"
        f"Host static websites with instant public links.\n"
        f"You can refer friends and get +3 extra upload slots for each!\n"
        f"Your referral link: `{link}`",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Upload handler
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    file = update.message.document

    if not file.file_name.lower().endswith(ALLOWED_EXTENSIONS):
        await update.message.reply_text("⚠️ Only .html or .zip files are supported.")
        return

    if file.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("⚠️ File size exceeds 5MB.")
        return

    user_data = db.child("users").child(user_id).get().val() or []
    if len(user_data) >= get_upload_limit(user_id):
        await update.message.reply_text("⚠️ Upload limit reached. Delete some files.")
        return

    try:
        file_path = tempfile.mktemp()
        telegram_file = await file.get_file()
        await telegram_file.download_to_drive(file_path)

        if file.file_name.lower().endswith('.zip'):
            extract_path = tempfile.mkdtemp()
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
            html_files = [f for f in os.listdir(extract_path) if f.endswith('.html')]
            if not html_files:
                raise ValueError("No HTML files in ZIP")
            file_path = os.path.join(extract_path, html_files[0])
            file_name = html_files[0]
        else:
            file_name = file.file_name

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        firebase_path = f"uploads/{user_id}/{timestamp}_{file_name}"
        storage.child(firebase_path).put(file_path)
        url = storage.child(firebase_path).get_url(None)
        short_url = shorten_url(url)

        record = {
            "name": file_name,
            "path": firebase_path,
            "url": short_url,
            "timestamp": timestamp,
            "size": file.file_size
        }

        user_data.append(record)
        db.child("users").child(user_id).set(user_data)

        await update.message.reply_text(
            f"✅ *Upload Successful!*\n\n"
            f"📄 File: `{file_name}`\n"
            f"🌐 [Tap To View]({short_url})\n\n"
            f"🔗 Your Link:- `{short_url}`",
            parse_mode='Markdown',
            disable_web_page_preview=False
        )

    except Exception as e:
        logger.error(f"Upload error: {e}")
        await update.message.reply_text(f"❌ Upload failed: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# Button callback handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(query.from_user.id)

    if data == 'upload':
        await query.edit_message_text(
            "📤 Please send an HTML or ZIP file (max 5MB).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'files':
        files = db.child("users").child(user_id).get().val() or []
        limit = get_upload_limit(user_id)
        if not files:
            await query.edit_message_text("📁 No files uploaded yet.")
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
            await query.edit_message_text("❌ No files to delete.")
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
            except:
                name = uid
            leaderboard.append(f"{i}. {name}: {count} referrals")

        text = "\n".join(leaderboard) or "No referrals yet."
        await query.edit_message_text(
            f"🏆 *Referral Leaderboard*\n\n{text}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'help':
        await query.edit_message_text(
            "ℹ️ *Bot Help*\n\n"
            "• Upload HTML/ZIP files\n"
            "• Share short links\n"
            "• Invite others with your referral link for +3 uploads\n\n"
            "ZIP must contain index.html",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'start':
        await start(update, context)

# Broadcast command
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this.")
        return

    message = ' '.join(context.args)
    if not message:
        await update.message.reply_text("⚠️ Usage: /broadcast Your message here")
        return

    users = db.child("users").get().val() or {}
    success, fail = 0, 0

    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=message)
            success += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning(f"Failed to send to {uid}: {e}")
            fail += 1

    await update.message.reply_text(f"✅ Sent to {success} users. ❌ Failed: {fail}")

# Main function
if __name__ == '__main__':
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot is running...")
    app.run_polling()
