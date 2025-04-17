
# Telegram HTML Hosting Bot

A feature-rich Telegram bot for hosting static HTML content with Firebase integration. Users can upload, manage, and share HTML/ZIP files with instant public URLs, while admins can broadcast messages to all users.

![Bot Demo](https://via.placeholder.com/800x400.png?text=Bot+Interface+Demo)

## Features

- 🚀 Instant HTML file hosting with public URLs
- 📤 Support for both HTML files and ZIP archives
- 🔒 User-specific storage with limits (10 files/user)
- 📊 File management interface with delete functionality
- 🔔 Admin broadcast system
- ⚡ Firebase Realtime Database integration
- 🛡️ Secure configuration with environment variables
- 📱 Responsive inline keyboard interface

## Tech Stack

- **Python 3.10+**
- python-telegram-bot
- Firebase (Storage + Realtime Database)
- pyrebase
- python-dotenv

## Installation

1. Clone the repository:
```bash
git clone https://github.com/ReOneSec/tg-host.git
cd tg-host
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up Firebase:
- Create a Firebase project
- Enable Storage and Realtime Database
- Generate service account credentials

4. Configure environment variables (see `.env.example`)

## Configuration

Create a `.env` file with the following variables:

```ini
BOT_TOKEN=your_telegram_bot_token
ADMIN_ID=your_telegram_user_id

# Firebase Configuration
FIREBASE_API_KEY=your_firebase_api_key
FIREBASE_AUTH_DOMAIN=your_project.firebaseapp.com
FIREBASE_PROJECT_ID=your_project_id
FIREBASE_STORAGE_BUCKET=your_bucket.appspot.com
FIREBASE_MESSAGING_SENDER_ID=your_sender_id
FIREBASE_APP_ID=your_app_id
FIREBASE_MEASUREMENT_ID=optional_measurement_id
FIREBASE_DATABASE_URL=https://your_project.firebaseio.com
```

## Usage

### User Commands
- `/start` - Show main menu
- Upload HTML/ZIP file directly to chat (max 5MB)
- 📁 My Files - List all hosted files
- ❌ Delete File - Remove files from storage
- ℹ️ Help - Usage instructions

### Admin Commands
- `/broadcast <message>` - Send message to all users

### File Requirements
- HTML files: Direct upload
- ZIP archives:
  - Must contain at least one `.html` file
  - `index.html` will be prioritized
  - Max 5MB compressed size

## Firebase Setup Guide

1. Create new Firebase project at [Firebase Console](https://console.firebase.google.com/)
2. Enable **Storage** with these rules:
```json
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /uploads/{userId}/{allPaths=**} {
      allow read, write: if request.auth != null;
    }
  }
}
```

3. Enable **Realtime Database** with these rules:
```json
{
  "rules": {
    "users": {
      "$uid": {
        ".read": "$uid === auth.uid",
        ".write": "$uid === auth.uid"
      }
    }
  }
}
```

## Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## License

Distributed under the MIT License. See `LICENSE` for more information.

---
