import os
import asyncio
import logging
import tempfile
import zipfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
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
MAX_FILE_SIZE = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = ('.html', '.zip')
DEFAULT_UPLOAD_LIMIT = 10
BONUS_PER_REFERRAL = 3
TINYURL_API_KEY = os.getenv("TINYURL_API_KEY")
BOT_USERNAME = os.getenv("BOT_USERNAME")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# Health check server
def run_health_check_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')
    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_check_server, daemon=True).start()

# Helper: shorten URLs
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
        logger.warning(f"URL Shortening Failed: {e}")
        return long_url

# Helper: get upload limit
def get_upload_limit(user_id):
    referrals = db.child("referrals").child(user_id).get().val() or []
    custom_bonus = db.child("custom_slots").child(user_id).get().val() or 0
    return DEFAULT_UPLOAD_LIMIT + BONUS_PER_REFERRAL * len(referrals) + int(custom_bonus)

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    args = context.args if hasattr(context, "args") else []

    if args:
        referrer_id = args[0]
        if referrer_id != user_id and not db.child("ref_by").child(user_id).get().val():
            db.child("ref_by").child(user_id).set(referrer_id)
            db.child("referrals").child(referrer_id).push(user_id)
            try:
                await context.bot.send_message(chat_id=int(referrer_id), text=f"🎉 {user.first_name} joined using your referral link!")
            except Exception:
                pass

    referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload File", callback_data="upload")],
        [InlineKeyboardButton("👤 Profile", callback_data="profile"), InlineKeyboardButton("❌ Delete File", callback_data="delete")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help"), InlineKeyboardButton("👤 Contact", url="https://t.me/ViperROX")]
    ])

    message = update.message or update.callback_query.message
    await message.reply_text(
        f"👋 Welcome to the HTML Hosting Bot!\n\n"
        f"Host static websites easily and share public links.\n"
        f"Refer friends and get +3 upload slots per referral.\n\n"
        f"🔗 Your referral link: `{referral_link}`",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# Add slots command
async def add_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("⚠️ Usage: /addslots <user_id> <slots>")
        return

    user_id, slots = context.args
    try:
        slots = int(slots)
        db.child("custom_slots").child(user_id).set(slots)
        await update.message.reply_text(f"✅ User {user_id} now has +{slots} extra upload slots.")
    except Exception as e:
        logger.error(f"Failed to add slots: {e}")
        await update.message.reply_text("❌ Failed to add slots.")

# Handle file upload
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = str(user.id)
    file = update.message.document

    if not file.file_name.lower().endswith(ALLOWED_EXTENSIONS):
        await update.message.reply_text("⚠️ Only .html or .zip files are supported.")
        return

    if file.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("⚠️ File exceeds 5MB limit.")
        return

    user_files = db.child("users").child(user_id).get().val() or []
    if len(user_files) >= get_upload_limit(user_id):
        await update.message.reply_text("⚠️ Upload limit reached.")
        return

    temp_file_path = tempfile.mktemp()
    try:
        telegram_file = await file.get_file()
        await telegram_file.download_to_drive(temp_file_path)

        if file.file_name.lower().endswith(".zip"):
            extract_path = tempfile.mkdtemp()
            with zipfile.ZipFile(temp_file_path, "r") as zip_ref:
                zip_ref.extractall(extract_path)
            html_files = [f for f in os.listdir(extract_path) if f.endswith(".html")]
            if not html_files:
                raise ValueError("No HTML file found in ZIP.")
            temp_file_path = os.path.join(extract_path, html_files[0])
            file_name = html_files[0]
        else:
            file_name = file.file_name

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        firebase_path = f"uploads/{user_id}/{timestamp}_{file_name}"
        storage.child(firebase_path).put(temp_file_path)
        file_url = storage.child(firebase_path).get_url(None)
        short_url = shorten_url(file_url)

        record = {
            "name": file_name,
            "path": firebase_path,
            "url": short_url,
            "timestamp": timestamp,
            "size": file.file_size
        }

        user_files.append(record)
        db.child("users").child(user_id).set(user_files)

        await update.message.reply_text(
            f"✅ *Upload Successful!*\n\n"
            f"📄 File: `{file_name}`\n"
            f"🌐 [Tap To View]({short_url})\n"
            f"`{short_url}`",
            parse_mode="Markdown",
            disable_web_page_preview=False
        )

    except Exception as e:
        logger.error(f"Upload error: {e}")
        await update.message.reply_text("❌ Upload failed.")
    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

# Callback button handler
# Callback button handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    if query.data == "profile":
        user_files = db.child("users").child(user_id).get().val() or []
        limit = get_upload_limit(user_id)
        usage = len(user_files)
        referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
        referrals = db.child("referrals").child(user_id).get().val() or []

        msg = (
            f"👤 *Your Profile*\n\n"
            f"📦 Uploads: {usage}/{limit}\n"
            f"🎯 Referrals: {len(referrals)}\n"
            f"🔗 Referral Link: `{referral_link}`"
        )
        await query.edit_message_text(msg, parse_mode="Markdown")

    elif query.data == "delete":
        user_files = db.child("users").child(user_id).get().val() or []
        if not user_files:
            await query.edit_message_text("⚠️ No uploaded files found.")
            return

        keyboard = [
            [InlineKeyboardButton(file['name'], callback_data=f"del:{i}")]
            for i, file in enumerate(user_files)
        ]
        await query.edit_message_text("🗑️ Select a file to delete:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("del:"):
        index = int(query.data.split(":")[1])
        user_files = db.child("users").child(user_id).get().val() or []
        if index >= len(user_files):
            await query.edit_message_text("⚠️ Invalid file selection.")
            return

        file = user_files.pop(index)
        try:
            storage.delete(file["path"])
        except Exception as e:
            logger.warning(f"Failed to delete file in storage: {e}")

        db.child("users").child(user_id).set(user_files)
        await query.edit_message_text(f"✅ Deleted `{file['name']}`.", parse_mode="Markdown")

    elif query.data == "leaderboard":
        referrals = db.child("referrals").get().val() or {}
        top_users = sorted(referrals.items(), key=lambda x: len(x[1]), reverse=True)[:10]
        msg = "*🏆 Top Referrers:*\n\n"
        for rank, (uid, refs) in enumerate(top_users, 1):
            msg += f"{rank}. `{uid}` - {len(refs)} referrals\n"
        await query.edit_message_text(msg, parse_mode="Markdown")

    elif query.data == "help":
        await query.edit_message_text(
            "ℹ️ *Bot Help*\n\n"
            "1. Send a .html or .zip file (max 5MB) to host.\n"
            "2. ZIP must include at least one .html file.\n"
            "3. You get 10 upload slots by default.\n"
            "4. Earn +3 slots per referral.\n\n"
            "Need help? Contact @ViperROX",
            parse_mode="Markdown"
        )

    elif query.data == "upload":
        await query.edit_message_text("📤 Please send a .html or .zip file now.")

    else:
        await query.edit_message_text("⚠️ Unknown action.")

# Broadcast command (admin only)
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this.")
        return
    message = ' '.join(context.args)
    if not message:
        await update.message.reply_text("⚠️ Usage: /broadcast <message>")
        return

    users = db.child("users").get().val() or {}
    success, failed = 0, 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=message)
            success += 1
        except Exception as e:
            logger.warning(f"Failed to message {uid}: {e}")
            failed += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"✅ Sent: {success}, ❌ Failed: {failed}")

# Start the bot
if __name__ == '__main__':
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("addslots", add_slots))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(CallbackQueryHandler(button_handler))
    logger.info("Bot started successfully.")
    app.run_polling()
