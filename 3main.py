import os
import asyncio
import logging
import tempfile
import zipfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from collections import defaultdict
from functools import wraps

import requests
import bleach
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
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
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
MAX_EXTRACT_SIZE = 10 * 1024 * 1024  # 10MB for extracted content
MAX_FILES_IN_ZIP = 50
ALLOWED_EXTENSIONS = ('.html', '.zip')
DEFAULT_UPLOAD_LIMIT = 10
BONUS_PER_REFERRAL = 3
TINYURL_API_KEY = os.getenv("TINYURL_API_KEY")
BOT_USERNAME = os.getenv("BOT_USERNAME")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
FILE_EXPIRY_DAYS = 30
RATE_LIMIT_SECONDS = 3

# HTML sanitization settings
ALLOWED_TAGS = ['html', 'head', 'body', 'div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
                'span', 'br', 'hr', 'a', 'ul', 'ol', 'li', 'img', 'table', 'tr', 'td', 'th',
                'style', 'title', 'meta', 'link', 'script', 'footer', 'header', 'nav',
                'section', 'article', 'aside', 'main', 'button', 'form', 'input', 'label',
                'select', 'option', 'textarea', 'iframe', 'canvas', 'code', 'pre']
ALLOWED_ATTRIBUTES = {
    '*': ['class', 'id', 'style'],
    'a': ['href', 'target', 'rel'],
    'img': ['src', 'alt', 'width', 'height'],
    'meta': ['charset', 'name', 'content', 'http-equiv'],
    'link': ['rel', 'href', 'type'],
    'script': ['src', 'type', 'async', 'defer'],
    'iframe': ['src', 'width', 'height', 'frameborder', 'allowfullscreen'],
    'input': ['type', 'name', 'placeholder', 'value', 'checked', 'disabled'],
    'button': ['type', 'disabled'],
    'form': ['action', 'method']
}

# Rate limiting
user_last_action = defaultdict(lambda: 0)

# Health check server
def run_health_check_server():
    """Run a simple HTTP server for health checks"""
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')
            
    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_check_server, daemon=True).start()

# Rate limiter decorator
def rate_limit(func):
    """Decorator to enforce rate limiting"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        current_time = time.time()
        
        if current_time - user_last_action[user_id] < RATE_LIMIT_SECONDS:
            if update.callback_query:
                await update.callback_query.answer("Please wait before performing another action.")
                return
            else:
                await update.message.reply_text("Please wait before performing another action.")
                return
                
        user_last_action[user_id] = current_time
        return await func(update, context)
    return wrapper

# Helper: shorten URLs
def shorten_url(long_url):
    """Shorten a URL using TinyURL API"""
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
    """Calculate user's upload limit based on referrals and bonus slots"""
    referrals = db.child("referrals").child(user_id).get().val() or []
    custom_bonus = db.child("custom_slots").child(user_id).get().val() or 0
    return DEFAULT_UPLOAD_LIMIT + BONUS_PER_REFERRAL * len(referrals) + int(custom_bonus)

# Helper: sanitize HTML content
def sanitize_html_file(file_path):
    """Sanitize HTML content to prevent XSS attacks"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        sanitized = bleach.clean(content, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(sanitized)
        
        return file_path
    except Exception as e:
        logger.error(f"HTML sanitization failed: {e}")
        raise ValueError("HTML sanitization failed. The file may contain invalid content.")

# Helper: safely extract ZIP files
def safe_extract_zip(zip_path, extract_path):
    """Safely extract ZIP with size and file count limits to prevent ZIP bombs"""
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        # Check total uncompressed size
        total_size = sum(info.file_size for info in zip_ref.infolist())
        if total_size > MAX_EXTRACT_SIZE:
            raise ValueError(f"Extracted content would exceed maximum size ({MAX_EXTRACT_SIZE/1024/1024:.1f}MB)")
            
        # Check file count
        if len(zip_ref.infolist()) > MAX_FILES_IN_ZIP:
            raise ValueError(f"ZIP contains too many files (limit: {MAX_FILES_IN_ZIP})")
            
        # Extract files
        zip_ref.extractall(extract_path)
        
        # Find HTML files
        html_files = [f for f in os.listdir(extract_path) 
                     if os.path.isfile(os.path.join(extract_path, f)) and f.endswith('.html')]
        
        if not html_files:
            raise ValueError("No HTML files found in the ZIP archive.")
            
        return html_files

# Helper: main menu keyboard
def main_menu():
    """Create the main menu keyboard markup"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Upload File", callback_data="upload")],
        [InlineKeyboardButton("👤 Profile", callback_data="profile"), 
         InlineKeyboardButton("❌ Delete File", callback_data="delete")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help"), 
         InlineKeyboardButton("👤 Contact", url="https://t.me/ViperROX")]
    ])

# Scheduled task: cleanup expired files
async def cleanup_expired_files():
    """Delete files that have expired based on FILE_EXPIRY_DAYS"""
    try:
        all_users = db.child("users").get().val() or {}
        current_time = datetime.now()
        logger.info("Starting scheduled file cleanup")
        
        for user_id, files in all_users.items():
            if not files:
                continue
                
            updated_files = []
            for file in files:
                try:
                    file_time = datetime.strptime(file['timestamp'], "%Y%m%d%H%M%S")
                    if (current_time - file_time).days > FILE_EXPIRY_DAYS:
                        # File expired - delete from storage
                        storage.delete(file["path"])
                        logger.info(f"Deleted expired file: {file['path']}")
                    else:
                        updated_files.append(file)
                except Exception as e:
                    logger.error(f"Error processing file during cleanup: {e}")
                    updated_files.append(file)  # Keep file on error
            
            # Update database if files were removed
            if len(updated_files) != len(files):
                db.child("users").child(user_id).set(updated_files)
                
        logger.info("Completed scheduled file cleanup")
    except Exception as e:
        logger.error(f"File cleanup failed: {e}")

# Start command
@rate_limit
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command and process referrals"""
    user = update.effective_user
    user_id = str(user.id)
    args = context.args if hasattr(context, "args") else []

    if args:
        referrer_id = args[0]
        if referrer_id != user_id and not db.child("ref_by").child(user_id).get().val():
            db.child("ref_by").child(user_id).set(referrer_id)
            db.child("referrals").child(referrer_id).push(user_id)
            try:
                await context.bot.send_message(
                    chat_id=int(referrer_id), 
                    text=f"🎉 {user.first_name} joined using your referral link!"
                )
            except Exception as e:
                logger.error(f"Failed to notify referrer: {e}")

    referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    message = update.message or update.callback_query.message
    await message.reply_text(
        f"👋 Welcome to the HTML Hosting Bot!\n\n"
        f"Host static websites easily and share public links.\n"
        f"Refer friends and get +3 upload slots per referral.\n\n"
        f"🔗 Your referral link: `{referral_link}`",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

# Add slots command (admin only)
async def add_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to add extra upload slots to a user"""
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

# Broadcast command (admin only)
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to broadcast a message to all users"""
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this.")
        return
        
    message = ' '.join(context.args)
    if not message:
        await update.message.reply_text("⚠️ Usage: /broadcast <message>")
        return

    users = db.child("users").get().val() or {}
    success, failed = 0, 0
    
    status_msg = await update.message.reply_text("📣 Broadcasting message...")
    
    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=message)
            success += 1
            if success % 10 == 0:  # Update status every 10 successful messages
                await status_msg.edit_text(f"📣 Broadcasting: {success} sent, {failed} failed")
        except Exception as e:
            logger.warning(f"Failed to message {uid}: {e}")
            failed += 1
        await asyncio.sleep(0.05)  # Rate limiting to avoid Telegram API limits
        
    await status_msg.edit_text(f"✅ Broadcast complete: {success} sent, ❌ {failed} failed")

# Handle file upload
@rate_limit
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process file uploads (HTML or ZIP)"""
    user = update.message.from_user
    user_id = str(user.id)
    file = update.message.document

    if not file.file_name.lower().endswith(ALLOWED_EXTENSIONS):
        await update.message.reply_text("⚠️ Only .html or .zip files are supported.")
        return

    if file.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"⚠️ File exceeds {MAX_FILE_SIZE/1024/1024:.1f}MB limit.")
        return

    user_files = db.child("users").child(user_id).get().val() or []
    upload_limit = get_upload_limit(user_id)
    
    if len(user_files) >= upload_limit:
        await update.message.reply_text(
            f"⚠️ Upload limit reached ({len(user_files)}/{upload_limit}).\n"
            f"Refer friends to get more slots or delete existing files."
        )
        return

    # Start processing with progress updates
    progress_msg = await update.message.reply_text("⏳ Downloading file...")
    temp_dir = None
    temp_file_path = None
    
    try:
        temp_file_path = tempfile.mktemp()
        telegram_file = await file.get_file()
        await telegram_file.download_to_drive(temp_file_path)
        
        await progress_msg.edit_text("⏳ Processing file...")
        
        if file.file_name.lower().endswith(".zip"):
            temp_dir = tempfile.mkdtemp()
            try:
                html_files = safe_extract_zip(temp_file_path, temp_dir)
                
                if len(html_files) > 1:
                    # Store data for callback
                    context.user_data["zip_info"] = {
                        "extract_path": temp_dir,
                        "html_files": html_files,
                        "original_name": file.file_name
                    }
                    
                    # Build keyboard for file selection
                    keyboard = []
                    for html_file in html_files[:10]:  # Limit to 10 files in the menu
                        keyboard.append([InlineKeyboardButton(html_file, callback_data=f"selecthtml:{html_file}")])
                    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_upload")])
                    
                    await progress_msg.delete()
                    await update.message.reply_text(
                        f"📂 Found {len(html_files)} HTML files in the ZIP.\nPlease select the main file:",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return  # Wait for callback
                
                # If only one HTML file, use it directly
                file_name = html_files[0]
                temp_file_path = os.path.join(temp_dir, file_name)
            except ValueError as e:
                await progress_msg.delete()
                await update.message.reply_text(f"⚠️ {str(e)}")
                return
            except Exception as e:
                logger.error(f"ZIP processing error: {e}")
                await progress_msg.delete()
                await update.message.reply_text("❌ Failed to process ZIP file.")
                return
        else:
            file_name = file.file_name
            
        # Sanitize HTML content
        await progress_msg.edit_text("⏳ Validating content...")
        try:
            sanitize_html_file(temp_file_path)
        except ValueError as e:
            await progress_msg.delete()
            await update.message.reply_text(f"⚠️ {str(e)}")
            return
        except Exception as e:
            logger.error(f"Sanitization error: {e}")
            await progress_msg.delete()
            await update.message.reply_text("❌ Failed to validate HTML content.")
            return
        
        # Upload to Firebase
        await progress_msg.edit_text("⏳ Uploading to storage...")
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        firebase_path = f"uploads/{user_id}/{timestamp}_{file_name}"
        storage.child(firebase_path).put(temp_file_path)
        
        # Get public URL
        file_url = storage.child(firebase_path).get_url(None)
        short_url = shorten_url(file_url)
        
        # Save record in database
        record = {
            "name": file_name,
            "path": firebase_path,
            "url": short_url,
            "timestamp": timestamp,
            "size": file.file_size
        }
        
        user_files.append(record)
        db.child("users").child(user_id).set(user_files)
        
        await progress_msg.delete()
        await update.message.reply_text(
            f"✅ *Upload Successful!*\n\n"
            f"📄 File: `{file_name}`\n"
            f"🌐 [Tap To View]({short_url})\n"
            f"`{short_url}`\n\n"
            f"Your file will be available for {FILE_EXPIRY_DAYS} days.",
            parse_mode="Markdown",
            disable_web_page_preview=False
        )

    except zipfile.BadZipFile:
        await progress_msg.delete()
        await update.message.reply_text("⚠️ The ZIP file is corrupted or invalid.")
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await progress_msg.delete()
        await update.message.reply_text("❌ Upload failed. Please try again later.")
    finally:
        # Clean up temporary files
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except:
                pass
                
        # Don't delete the temp_dir if we're waiting for HTML file selection
        if temp_dir and os.path.exists(temp_dir) and "zip_info" not in context.user_data:
            try:
                import shutil
                shutil.rmtree(temp_dir)
            except:
                pass

# Handle profile display
async def handle_profile(query, user_id):
    """Display user profile with upload stats and files"""
    user_files = db.child("users").child(user_id).get().val() or []
    limit = get_upload_limit(user_id)
    usage = len(user_files)
    referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    referrals = db.child("referrals").child(user_id).get().val() or []

    keyboard = []
    # Show only the 5 most recent files
    for file in user_files[-5:]:
        keyboard.append([InlineKeyboardButton(f"📄 {file['name']}", url=file['url'])])

    if len(user_files) > 5:
        keyboard.append([InlineKeyboardButton("Show All Files", callback_data="show_all_files")])
        
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back")])
    
    msg = (
        f"👤 *Your Profile*\n\n"
        f"📦 Uploads: {usage}/{limit}\n"
        f"🎯 Referrals: {len(referrals)}\n"
        f"🔗 Referral Link: `{referral_link}`"
    )
    
    await query.edit_message_text(
        msg, 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode="Markdown"
    )

# Handle file deletion menu
async def handle_delete_menu(query, user_id):
    """Display menu of files that can be deleted"""
    user_files = db.child("users").child(user_id).get().val() or []
    if not user_files:
        await query.edit_message_text(
            "⚠️ No uploaded files found.", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
        return

    keyboard = []
    # Show only the 10 most recent files for deletion
    for i, file in enumerate(user_files[-10:]):
        # Use negative index to reference from the end of the list
        real_index = len(user_files) - 10 + i if len(user_files) > 10 else i
        keyboard.append([
            InlineKeyboardButton(
                f"{file['name']} ({datetime.strptime(file['timestamp'], '%Y%m%d%H%M%S').strftime('%Y-%m-%d')})", 
                callback_data=f"del:{real_index}"
            )
        ])
        
    if len(user_files) > 10:
        keyboard.append([InlineKeyboardButton("Show More Files", callback_data="show_more_delete")])
        
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back")])
    
    await query.edit_message_text(
        "🗑️ Select a file to delete:", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Handle delete confirmation
async def handle_delete_confirmation(query, user_id):
    """Ask for confirmation before deleting a file"""
    index = int(query.data.split(":")[1])
    user_files = db.child("users").child(user_id).get().val() or []
    if index >= len(user_files):
        await query.edit_message_text("⚠️ Invalid file selection.")
        return

    file = user_files[index]
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes", callback_data=f"confirmdel:{index}"), 
            InlineKeyboardButton("❌ Cancel", callback_data="delete")
        ]
    ]
    
    await query.edit_message_text(
        f"Are you sure you want to delete `{file['name']}`?", 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode="Markdown"
    )

# Handle confirmed delete
async def handle_confirm_delete(query, user_id):
    """Process file deletion after confirmation"""
    index = int(query.data.split(":")[1])
    user_files = db.child("users").child(user_id).get().val() or []
    if index >= len(user_files):
        await query.edit_message_text("⚠️ Invalid file selection.")
        return

    file = user_files.pop(index)
    try:
        storage.delete(file["path"])
        db.child("users").child(user_id).set(user_files)
        await query.edit_message_text(
            f"✅ Deleted `{file['name']}`.", 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="delete")]])
        )
    except Exception as e:
        logger.error(f"Failed to delete file in storage: {e}")
        await query.edit_message_text(
            f"⚠️ Error deleting file. Please try again.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="delete")]])
        )

# Handle profile display
async def handle_profile(query, user_id):
    """Display user profile with upload stats and files"""
    user_files = db.child("users").child(user_id).get().val() or []
    limit = get_upload_limit(user_id)
    usage = len(user_files)
    referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    referrals = db.child("referrals").child(user_id).get().val() or []

    keyboard = []
    # Show only the 5 most recent files
    for file in user_files[-5:]:
        keyboard.append([InlineKeyboardButton(f"📄 {file['name']}", url=file['url'])])

    if len(user_files) > 5:
        keyboard.append([InlineKeyboardButton("Show All Files", callback_data="show_all_files")])
        
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back")])
    
    msg = (
        f"👤 *Your Profile*\n\n"
        f"📦 Uploads: {usage}/{limit}\n"
        f"🎯 Referrals: {len(referrals)}\n"
        f"🔗 Referral Link: `{referral_link}`"
    )
    
    await query.edit_message_text(
        msg, 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode="Markdown"
    )

# Handle file deletion menu
async def handle_delete_menu(query, user_id):
    """Display menu of files that can be deleted"""
    user_files = db.child("users").child(user_id).get().val() or []
    if not user_files:
        await query.edit_message_text(
            "⚠️ No uploaded files found.", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
        return

    keyboard = []
    # Show only the 10 most recent files for deletion
    for i, file in enumerate(user_files[-10:]):
        # Use negative index to reference from the end of the list
        real_index = len(user_files) - 10 + i if len(user_files) > 10 else i
        keyboard.append([
            InlineKeyboardButton(
                f"{file['name']} ({datetime.strptime(file['timestamp'], '%Y%m%d%H%M%S').strftime('%Y-%m-%d')})", 
                callback_data=f"del:{real_index}"
            )
        ])
        
    if len(user_files) > 10:
        keyboard.append([InlineKeyboardButton("Show More Files", callback_data="show_more_delete")])
        
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back")])
    
    await query.edit_message_text(
        "🗑️ Select a file to delete:", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Handle delete confirmation
async def handle_delete_confirmation(query, user_id):
    """Ask for confirmation before deleting a file"""
    index = int(query.data.split(":")[1])
    user_files = db.child("users").child(user_id).get().val() or []
    if index >= len(user_files):
        await query.edit_message_text("⚠️ Invalid file selection.")
        return

    file = user_files[index]
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes", callback_data=f"confirmdel:{index}"), 
            InlineKeyboardButton("❌ Cancel", callback_data="delete")
        ]
    ]
    
    await query.edit_message_text(
        f"Are you sure you want to delete `{file['name']}`?", 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode="Markdown"
    )

# Handle leaderboard display
async def handle_leaderboard(query):
    """Display leaderboard of top referrers"""
    referrals = db.child("referrals").get().val() or {}
    top_users = sorted(referrals.items(), key=lambda x: len(x[1]) if x[1] else 0, reverse=True)[:10]
    
    msg = "*🏆 Top Referrers:*\n\n"
    if not top_users:
        msg += "No referrals yet. Be the first to invite friends!"
    else:
        for rank, (uid, refs) in enumerate(top_users, 1):
            try:
                # Try to get username if available
                user_info = db.child("user_info").child(uid).get().val() or {}
                username = user_info.get("username", f"User {uid[:6]}...")
                msg += f"{rank}. {username} - {len(refs) if refs else 0} referrals\n"
            except:
                # Fallback to user ID
                msg += f"{rank}. `{uid[:6]}...` - {len(refs) if refs else 0} referrals\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="back")]]
    await query.edit_message_text(
        msg, 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode="Markdown"
    )

# Handle help menu
async def handle_help(query):
    """Display help information"""
    msg = (
        "ℹ️ *Bot Help*\n\n"
        "1. Send a .html or .zip file (max 5MB) to host.\n"
        "2. ZIP must include at least one .html file.\n"
        "3. You get 10 upload slots by default.\n"
        "4. Earn +3 slots per referral.\n"
        "5. Files expire after 30 days.\n"
        "6. HTML content is sanitized for security.\n\n"
        "Need help? Contact @ViperROX"
    )
    await query.edit_message_text(
        msg, 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]]), 
        parse_mode="Markdown"
    )

# Handle HTML file selection from ZIP
async def handle_html_selection(query, context, html_file):
    """Process selected HTML file from a ZIP archive"""
    user_id = str(query.from_user.id)
    
    if "zip_info" not in context.user_data:
        await query.edit_message_text(
            "⚠️ Session expired. Please upload your file again.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
        return
        
    zip_info = context.user_data["zip_info"]
    extract_path = zip_info["extract_path"]
    original_name = zip_info["original_name"]
    
    progress_msg = await query.edit_message_text("⏳ Processing selected file...")
    
    try:
        temp_file_path = os.path.join(extract_path, html_file)
        
        # Sanitize HTML content
        sanitize_html_file(temp_file_path)
        
        # Upload to Firebase
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        firebase_path = f"uploads/{user_id}/{timestamp}_{html_file}"
        storage.child(firebase_path).put(temp_file_path)
        
        # Get public URL
        file_url = storage.child(firebase_path).get_url(None)
        short_url = shorten_url(file_url)
        
        # Save record in database
        user_files = db.child("users").child(user_id).get().val() or []
        record = {
            "name": html_file,
            "path": firebase_path,
            "url": short_url,
            "timestamp": timestamp,
            "size": os.path.getsize(temp_file_path),
            "from_zip": original_name
        }
        
        user_files.append(record)
        db.child("users").child(user_id).set(user_files)
        
        await progress_msg.edit_text(
            f"✅ *Upload Successful!*\n\n"
            f"📄 File: `{html_file}`\n"
            f"🌐 [Tap To View]({short_url})\n"
            f"`{short_url}`\n\n"
            f"Your file will be available for {FILE_EXPIRY_DAYS} days.",
            parse_mode="Markdown",
            disable_web_page_preview=False,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
        
    except Exception as e:
        logger.error(f"HTML selection error: {e}")
        await progress_msg.edit_text(
            "❌ Failed to process the selected file.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
    finally:
        # Clean up temporary files
        if "zip_info" in context.user_data:
            try:
                import shutil
                shutil.rmtree(extract_path)
            except Exception as e:
                logger.error(f"Failed to clean up temp directory: {e}")
            # Clear the stored data
            del context.user_data["zip_info"]

# Handle showing all files
async def handle_show_all_files(query, user_id):
    """Display all user files with pagination"""
    user_files = db.child("users").child(user_id).get().val() or []
    if not user_files:
        await query.edit_message_text(
            "⚠️ No uploaded files found.", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
        return
        
    # Pagination - 5 files per page
    page = 0
    if ":" in query.data:
        page = int(query.data.split(":")[1])
    
    total_pages = (len(user_files) + 4) // 5  # Ceiling division
    start_idx = page * 5
    end_idx = min(start_idx + 5, len(user_files))
    
    keyboard = []
    for file in user_files[start_idx:end_idx]:
        file_date = datetime.strptime(file['timestamp'], '%Y%m%d%H%M%S').strftime('%Y-%m-%d')
        keyboard.append([
            InlineKeyboardButton(
                f"📄 {file['name']} ({file_date})", 
                url=file['url']
            )
        ])
    
    # Pagination controls
    pagination = []
    if page > 0:
        pagination.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"files_page:{page-1}"))
    if page < total_pages - 1:
        pagination.append(InlineKeyboardButton("Next ➡️", callback_data=f"files_page:{page+1}"))
    
    if pagination:
        keyboard.append(pagination)
    
    keyboard.append([InlineKeyboardButton("⬅️ Back to Profile", callback_data="profile")])
    
    await query.edit_message_text(
        f"📂 *Your Files* (Page {page+1}/{total_pages})\n\n"
        f"Total files: {len(user_files)}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# Handle showing more files for deletion
async def handle_show_more_delete(query, user_id):
    """Display more files for deletion with pagination"""
    user_files = db.child("users").child(user_id).get().val() or []
    
    # Pagination - 10 files per page
    page = 0
    if ":" in query.data:
        page = int(query.data.split(":")[1])
    
    total_pages = (len(user_files) + 9) // 10  # Ceiling division
    start_idx = page * 10
    end_idx = min(start_idx + 10, len(user_files))
    
    keyboard = []
    for i, file in enumerate(user_files[start_idx:end_idx]):
        real_index = start_idx + i
        file_date = datetime.strptime(file['timestamp'], '%Y%m%d%H%M%S').strftime('%Y-%m-%d')
        keyboard.append([
            InlineKeyboardButton(
                f"{file['name']} ({file_date})", 
                callback_data=f"del:{real_index}"
            )
        ])
    
    # Pagination controls
    pagination = []
    if page > 0:
        pagination.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"delete_page:{page-1}"))
    if page < total_pages - 1:
        pagination.append(InlineKeyboardButton("Next ➡️", callback_data=f"delete_page:{page+1}"))
    
    if pagination:
        keyboard.append(pagination)
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="delete")])
    
    await query.edit_message_text(
        f"🗑️ Select a file to delete (Page {page+1}/{total_pages}):", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Handle cancel upload
async def handle_cancel_upload(query, context):
    """Cancel the upload process and clean up temporary files"""
    if "zip_info" in context.user_data:
        try:
            import shutil
            shutil.rmtree(context.user_data["zip_info"]["extract_path"])
        except Exception as e:
            logger.error(f"Failed to clean up temp directory: {e}")
        # Clear the stored data
        del context.user_data["zip_info"]
            
    await query.edit_message_text(
        "❌ Upload cancelled.", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
    )

# Store user info when they interact with the bot
async def store_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store basic user information for better UX"""
    user = update.effective_user
    if not user:
        return
        
    user_id = str(user.id)
    user_info = {
        "username": user.username or f"User_{user.id}",
        "first_name": user.first_name,
        "last_name": user.last_name,
        "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    db.child("user_info").child(user_id).update(user_info)
    
    # Continue to the next handler
    return await context.next_handler(update, context)

# Callback button handler
@rate_limit
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries from inline buttons"""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    # Main menu options
    if query.data == "profile":
        await handle_profile(query, user_id)
        
    elif query.data == "delete":
        await handle_delete_menu(query, user_id)
        
    elif query.data.startswith("del:"):
        await handle_delete_confirmation(query, user_id)
        
    elif query.data.startswith("confirmdel:"):
        await handle_confirm_delete(query, user_id)
        
    elif query.data == "leaderboard":
        await handle_leaderboard(query)
        
    elif query.data == "help":
        await handle_help(query)
        
    elif query.data == "upload":
        await query.edit_message_text(
            "📄 Please send a .html or .zip file now.", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
        
    elif query.data == "back":
        await query.edit_message_text("📄 Main Menu:", reply_markup=main_menu())
        
    # HTML file selection from ZIP
    elif query.data.startswith("selecthtml:"):
        html_file = query.data.split(":", 1)[1]
        await handle_html_selection(query, context, html_file)
        
    elif query.data == "cancel_upload":
        await handle_cancel_upload(query, context)
        
    # File pagination
    elif query.data == "show_all_files" or query.data.startswith("files_page:"):
        await handle_show_all_files(query, user_id)
        
    # Delete pagination
    elif query.data == "show_more_delete" or query.data.startswith("delete_page:"):
        await handle_show_more_delete(query, user_id)
        
    else:
        await query.edit_message_text(
            "⚠️ Unknown action.", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )

# Error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""
    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # Try to notify the user about the error
    try:
        if update and hasattr(update, 'effective_user') and update.effective_user:
            user_id = update.effective_user.id
            await context.bot.send_message(
                chat_id=user_id, 
                text="Unfortunately, an error occurred. Please try again later."
            )
    except Exception as e:
        logger.error(f"Failed to send error message to user: {e}")
        
    # Notify admin if needed
    try:
        error_text = f"An error occurred: {context.error}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=error_text)
    except:
        pass

# Scheduled task for file cleanup
async def cleanup_expired_files(context: ContextTypes.DEFAULT_TYPE = None):
    """Delete files that have expired based on FILE_EXPIRY_DAYS"""
    try:
        all_users = db.child("users").get().val() or {}
        current_time = datetime.now()
        logger.info("Starting scheduled file cleanup")
        
        for user_id, files in all_users.items():
            if not files:
                continue
                
            updated_files = []
            for file in files:
                try:
                    file_time = datetime.strptime(file['timestamp'], "%Y%m%d%H%M%S")
                    if (current_time - file_time).days > FILE_EXPIRY_DAYS:
                        # File expired - delete from storage
                        storage.delete(file["path"])
                        logger.info(f"Deleted expired file: {file['path']}")
                    else:
                        updated_files.append(file)
                except Exception as e:
                    logger.error(f"Error processing file during cleanup: {e}")
                    updated_files.append(file)  # Keep file on error
            
            # Update database if files were removed
            if len(updated_files) != len(files):
                db.child("users").child(user_id).set(updated_files)
                
        logger.info("Completed scheduled file cleanup")
    except Exception as e:
        logger.error(f"File cleanup failed: {e}")

# Main function to start the bot
def main():
    """Initialize and start the bot"""
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()
    
    # Add middleware for user tracking
    app.add_handler(MessageHandler(filters.ALL, store_user_info), group=-1)
    
    # Add command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("addslots", add_slots))
    
    # Add message handlers
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    
    # Add callback query handler
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    # Schedule cleanup job (run daily)
    job_queue = app.job_queue
    job_queue.run_repeating(cleanup_expired_files, interval=86400, first=3600)  # First run after 1 hour, then daily
    
    # Start the bot
    logger.info("Bot started successfully.")
    app.run_polling()

if __name__ == '__main__':
    main()
    
