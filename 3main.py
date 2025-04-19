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
    InlineKeyboardMarkup,
    ChatMember
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

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Firebase config
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
TINYURL_API_KEY = "PzJOqDQMIXuTGshO8VCpscW3jzqHsCKtsBQ16MYdKJfhcP7IbRNOEkqa3mME"
BOT_USERNAME = os.getenv("BOT_USERNAME")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL")

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
    user_id = str(user_id)
    bonus = db.child("referrals").child(user_id).get().val()
    bonus_count = len(bonus or [])
    custom = db.child("custom_slots").child(user_id).get().val()
    return DEFAULT_UPLOAD_LIMIT + BONUS_PER_REFERRAL * bonus_count + int(custom or 0)

def get_referrer(user_id):
    return db.child("ref_by").child(user_id).get().val()

async def check_force_join(user_id, bot):
    try:
        member = await bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        if member.status in [ChatMember.LEFT, ChatMember.KICKED]:
            return False
        return True
    except:
        return False

async def send_force_join_message(update):
    keyboard = [
        [InlineKeyboardButton("✅ Join Channel", url=f"https://t.me/{FORCE_JOIN_CHANNEL}")],
        [InlineKeyboardButton("🔄 I've Joined", callback_data='check_force')]
    ]
    message = update.message or update.callback_query.message
    await message.reply_text(
        "🚫 To use this bot, you must join our channel.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)

    if not await check_force_join(user.id, context.bot):
        await send_force_join_message(update)
        return

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

    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    keyboard = [
        [InlineKeyboardButton("📤 Upload File", callback_data='upload')],
        [InlineKeyboardButton("📁 My Files", callback_data='files')],
        [InlineKeyboardButton("❌ Delete File", callback_data='delete')],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data='leaderboard')],
        [InlineKeyboardButton("📊 Referral Stats", callback_data='stats')],
        [InlineKeyboardButton("ℹ️ Help", callback_data='help')],
        [InlineKeyboardButton("👤 Contact", url="https://t.me/ViperROX")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message = update.message or update.callback_query.message
    await message.reply_text(
        f"👋 Welcome to the HTML Hosting Bot!\n\n"
        f"Host static websites with instant public links.\n"
        f"Refer friends and get +3 slots per referral!\n\n"
        f"Your referral link: `{link}`",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Upload file
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    file = update.message.document

    if not await check_force_join(update.message.from_user.id, context.bot):
        await send_force_join_message(update)
        return

    if not file.file_name.lower().endswith(ALLOWED_EXTENSIONS):
        await update.message.reply_text("⚠️ Only .html or .zip files allowed.")
        return

    if file.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("⚠️ Max 5MB file size.")
        return

    files = db.child("users").child(user_id).get().val() or []
    if len(files) >= get_upload_limit(user_id):
        await update.message.reply_text("⚠️ Upload limit reached. Delete some files.")
        return

    try:
        temp_path = tempfile.mktemp()
        telegram_file = await file.get_file()
        await telegram_file.download_to_drive(temp_path)

        if file.file_name.endswith('.zip'):
            extract_path = tempfile.mkdtemp()
            with zipfile.ZipFile(temp_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
            html_files = [f for f in os.listdir(extract_path) if f.endswith('.html')]
            if not html_files:
                raise ValueError("No HTML files in ZIP")
            temp_path = os.path.join(extract_path, html_files[0])
            file_name = html_files[0]
        else:
            file_name = file.file_name

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        cloud_path = f"uploads/{user_id}/{timestamp}_{file_name}"
        storage.child(cloud_path).put(temp_path)
        url = storage.child(cloud_path).get_url(None)
        short_url = shorten_url(url)

        files.append({
            "name": file_name,
            "path": cloud_path,
            "url": short_url,
            "timestamp": timestamp,
            "size": file.file_size
        })
        db.child("users").child(user_id).set(files)

        await update.message.reply_text(
            f"✅ *Upload Successful!*\n\n"
            f"📄 File: `{file_name}`\n"
            f"🌐 [View Online]({short_url})\n"
            f"🔗 `{short_url}`",
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await update.message.reply_text("❌ Upload failed.")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# Callback buttons
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(query.from_user.id)

    if data == 'check_force':
        if await check_force_join(user_id, context.bot):
            await start(update, context)
        else:
            await send_force_join_message(update)

    elif data == 'upload':
        await query.edit_message_text(
            "📤 Send an HTML or ZIP file (max 5MB).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'files':
        files = db.child("users").child(user_id).get().val() or []
        limit = get_upload_limit(user_id)
        if not files:
            await query.edit_message_text("📁 No files uploaded.")
            return
        file_list = "\n".join(
            [f"• [{f['name']}]({f['url']}) ({f['size']//1024}KB)" for f in files]
        )
        await query.edit_message_text(
            f"📂 *Your Files ({len(files)}/{limit}):*\n\n{file_list}",
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
        buttons = [[InlineKeyboardButton(f"🗑 {f['name']}", callback_data=f"delete_{i}")]
                   for i, f in enumerate(files)]
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

        await query.edit_message_text(
            f"🏆 *Referral Leaderboard*\n\n" + "\n".join(leaderboard),
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'stats':
        referrals = db.child("referrals").child(user_id).get().val() or []
        total_slots = get_upload_limit(user_id)
        await query.edit_message_text(
            f"📊 *Referral Stats*\n\n"
            f"👥 Referrals: {len(referrals)}\n"
            f"📦 Upload Limit: {total_slots} files",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'help':
        await query.edit_message_text(
            "ℹ️ *Help*\n\n"
            "• Upload HTML/ZIP files\n"
            "• Share links\n"
            "• +3 uploads per referral\n"
            "• ZIP must include `index.html`",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='start')]
            ])
        )

    elif data == 'start':
        await start(update, context)

# Admin commands
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return

    msg = ' '.join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /broadcast message")
        return

    users = db.child("users").get().val() or {}
    success, fail = 0, 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=msg)
            success += 1
            await asyncio.sleep(0.1)
        except:
            fail += 1
    await update.message.reply_text(f"✅ Sent: {success}, ❌ Failed: {fail}")

async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    users = db.child("users").get().val() or {}
    await update.message.reply_text("User UIDs:\n" + "\n".join(users.keys()))

async def addslots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /addslots user_id count")
        return
    uid, count = context.args
    try:
        current = int(db.child("custom_slots").child(uid).get().val() or 0)
        db.child("custom_slots").child(uid).set(current + int(count))
        await update.message.reply_text(f"✅ Added {count} slots to {uid}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# Main
if __name__ == '__main__':
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("users", users))
    app.add_handler(CommandHandler("addslots", addslots))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(CallbackQueryHandler(button_handler))
    logger.info("Bot running...")
    app.run_polling()
