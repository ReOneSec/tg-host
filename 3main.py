import os
import uuid
import json
import time
import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters
)
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase
cred = credentials.Certificate(".env")  # Replace with your Firebase credentials path
firebase_admin.initialize_app(cred)
db = firestore.client()

# Bot credentials
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")

# File hosting base URL (adjust to match your domain and Firebase Hosting setup)
HOSTING_URL = "https://epplicon.net"

# Constants
FREE_UPLOAD_LIMIT = 10
BONUS_PER_REFERRAL = 3

# Track uptime
start_time = time.time()


# Utility Functions
def get_user_referral_code(user_id):
    return str(user_id)


async def update_upload_count(user_id, delta):
    user_doc = db.collection("users").document(str(user_id))
    user = user_doc.get()
    if user.exists:
        data = user.to_dict()
        data["uploads"] = data.get("uploads", 0) + delta
        user_doc.set(data)
    else:
        user_doc.set({"uploads": delta, "referrals": 0})


async def get_upload_count(user_id):
    user = db.collection("users").document(str(user_id)).get()
    return user.to_dict().get("uploads", 0) if user.exists else 0


async def get_upload_limit(user_id):
    user = db.collection("users").document(str(user_id)).get()
    if user.exists:
        referrals = user.to_dict().get("referrals", 0)
        return FREE_UPLOAD_LIMIT + referrals * BONUS_PER_REFERRAL
    return FREE_UPLOAD_LIMIT


# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    args = context.args
    referral_code = args[0] if args else None

    user_ref = db.collection("users").document(user_id)
    if not user_ref.get().exists:
        data = {
            "uploads": 0,
            "referrals": 0,
            "joined": firestore.SERVER_TIMESTAMP
        }
        user_ref.set(data)

        if referral_code and referral_code != user_id:
            ref_user_ref = db.collection("users").document(referral_code)
            ref_user = ref_user_ref.get()
            if ref_user.exists:
                ref_data = ref_user.to_dict()
                ref_data["referrals"] = ref_data.get("referrals", 0) + 1
                ref_user_ref.set(ref_data)
                await context.bot.send_message(
                    chat_id=referral_code,
                    text=f"You earned +{BONUS_PER_REFERRAL} file slots for referring {user.first_name}!"
                )

    keyboard = [
        [KeyboardButton("Upload"), KeyboardButton("Files")],
        [KeyboardButton("Delete"), KeyboardButton("Refer")],
        [KeyboardButton("Help"), KeyboardButton("Back")]
    ]
    await update.message.reply_text(
        "Welcome to the File Bot!",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "/start - Start or use referral link\n"
        "/upload - Upload a file\n"
        "/files - List your files\n"
        "/delete - Delete a file\n"
        "/refer - Get your referral link\n"
        "/stat - Bot stats\n"
        "/help - Show this message"
    )
    await update.message.reply_text(help_text)


async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    current = await get_upload_count(user_id)
    limit = await get_upload_limit(user_id)
    if current >= limit:
        await update.message.reply_text("Upload limit reached. Refer friends to earn more!")
        return
    await update.message.reply_text("Send me the file (HTML/ZIP only).")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    current = await get_upload_count(user_id)
    limit = await get_upload_limit(user_id)
    if current >= limit:
        await update.message.reply_text("Upload limit reached. Refer friends to earn more!")
        return

    file = update.message.document
    if file.mime_type not in ["application/zip", "text/html"]:
        await update.message.reply_text("Only ZIP or HTML files are allowed.")
        return

    file_id = str(uuid.uuid4())
    file_path = f"public/{file_id}_{file.file_name}"
    await file.get_file().download_to_drive(file_path)

    db.collection("files").document(file_id).set({
        "user_id": user_id,
        "file_name": file.file_name,
        "file_path": file_path,
        "url": f"{HOSTING_URL}/{file_id}_{file.file_name}",
        "timestamp": firestore.SERVER_TIMESTAMP
    })

    await update_upload_count(user_id, 1)

    await update.message.reply_text(f"File uploaded: {HOSTING_URL}/{file_id}_{file.file_name}")


async def files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    docs = db.collection("files").where("user_id", "==", user_id).stream()
    msg = "Your files:\n"
    found = False
    for doc in docs:
        found = True
        data = doc.to_dict()
        msg += f"- {data['file_name']}: {data['url']}\n"
    if not found:
        msg = "No files uploaded yet."
    await update.message.reply_text(msg)


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    docs = db.collection("files").where("user_id", "==", user_id).stream()
    buttons = []
    for doc in docs:
        data = doc.to_dict()
        buttons.append([
            InlineKeyboardButton(data["file_name"], callback_data=f"delete:{doc.id}")
        ])
    if not buttons:
        await update.message.reply_text("No files to delete.")
        return
    await update.message.reply_text("Select a file to delete:",
        reply_markup=InlineKeyboardMarkup(buttons))


async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, file_id = query.data.split(":")
    doc = db.collection("files").document(file_id).get()
    if doc.exists:
        data = doc.to_dict()
        path = data.get("file_path")
        if path and os.path.exists(path):
            os.remove(path)
        db.collection("files").document(file_id).delete()
        await update_upload_count(data["user_id"], -1)
        await query.edit_message_text("File deleted.")
    else:
        await query.edit_message_text("File not found.")


async def refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    await update.message.reply_text(f"Refer friends using this link:\n{link}")


async def stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = len(list(db.collection("users").stream()))
    total_files = len(list(db.collection("files").stream()))
    uptime_sec = int(time.time() - start_time)
    uptime_str = str(datetime.timedelta(seconds=uptime_sec))
    msg = (
        f"Bot Stats:\n"
        f"Total Users: {total_users}\n"
        f"Total Files: {total_files}\n"
        f"Uptime: {uptime_str}"
    )
    await update.message.reply_text(msg)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == "upload":
        await upload(update, context)
    elif text == "files":
        await files(update, context)
    elif text == "delete":
        await delete(update, context)
    elif text == "refer":
        await refer(update, context)
    elif text == "help":
        await help_command(update, context)
    elif text == "back":
        await start(update, context)
    else:
        await update.message.reply_text("Unrecognized command. Use /help")


# Main Application
if __name__ == "__main__":
    import asyncio
    if not os.path.exists("public"):
        os.makedirs("public")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("upload", upload))
    app.add_handler(CommandHandler("files", files))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("refer", refer))
    app.add_handler(CommandHandler("stat", stat))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(handle_delete))

    print("Bot started.")
    app.run_polling()
