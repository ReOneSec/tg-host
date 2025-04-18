# HTML Hosting Bot 🚀

[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Telegram Bot API](https://img.shields.io/badge/Telegram%20Bot%20API-✓-blue.svg)](https://core.telegram.org/bots/api)
[![Firebase](https://img.shields.io/badge/Firebase-Storage%20%26%20Database-orange.svg)](https://firebase.google.com/)

A Telegram bot that allows users to host static websites by uploading HTML or ZIP files, providing instant public links for sharing. Features a referral system to increase your upload capacity.

![Bot Demo](https://via.placeholder.com/800x400.png?text=HTML+Hosting+Bot)

## **Table of Contents:**
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [File Requirements](#file-requirements)
- [Referral System](#referral-system)
- [Code Structure](#code-structure)
- [Health Check Server](#health-check-server)
- [Troubleshooting](#troubleshooting)
- [Admin Features](#admin-features)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgments](#acknowledgments)

## **Features:**

- 📤 **Upload & Host Files:** Upload HTML and ZIP files (up to 5MB)
- 🔗 **Instant Public URLs:** Get shortened public links using TinyURL
- 📂 **File Management:** View and delete your uploaded files
- 👥 **Referral System:** Invite friends to earn extra upload slots
- 🏆 **Leaderboard:** Compete with others for most referrals
- 📨 **Admin Broadcast:** Send messages to all users (admin only)
- 🔄 **Health Check Server:** Built-in server for deployment platforms

## **Prerequisites:**

Before running the bot, ensure you have:

- Python 3.7+
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Firebase project with Storage and Realtime Database enabled
- TinyURL API key (register at [TinyURL](https://tinyurl.com/app))

## **Installation:**

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/yourusername/html-hosting-bot.git
   cd html-hosting-bot
   ```

2. **Create a Virtual Environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install Dependencies:**
   ```bash
   pip install python-telegram-bot pyrebase4 python-dotenv requests
   ```

   Alternatively, create a `requirements.txt` file with these dependencies and run:
   ```bash
   pip install -r requirements.txt
   ```

4. **Set Up Firebase:**
   - Create a Firebase project at [Firebase Console](https://console.firebase.google.com/)
   - Enable Firebase Storage and Realtime Database
   - Set Storage rules to allow public read access to files
   - Get your project configuration details from Project Settings

5. **Set Up TinyURL API:**
   - Register for a TinyURL developer account
   - Generate an API key for URL shortening

## **Configuration:**

Create a `.env` file in the root directory with the following environment variables:

```
# Telegram Configuration
BOT_TOKEN=your_telegram_bot_token
BOT_USERNAME=your_bot_username
ADMIN_ID=your_telegram_user_id

# Firebase Configuration
FIREBASE_API_KEY=your_firebase_api_key
FIREBASE_AUTH_DOMAIN=your_project.firebaseapp.com
FIREBASE_PROJECT_ID=your_project_id
FIREBASE_STORAGE_BUCKET=your_project.appspot.com
FIREBASE_MESSAGING_SENDER_ID=your_messaging_sender_id
FIREBASE_APP_ID=your_app_id
FIREBASE_MEASUREMENT_ID=your_measurement_id
FIREBASE_DATABASE_URL=https://your_project.firebaseio.com

# TinyURL API Key
TINYURL_API_KEY=your_tinyurl_api_key
```

**Important Configuration Notes:**
- `BOT_TOKEN`: Obtain from [@BotFather](https://t.me/BotFather) when creating your bot
- `BOT_USERNAME`: Your bot's username without the '@' symbol
- `ADMIN_ID`: Your Telegram user ID (required for broadcast command)
- Firebase credentials: Found in your Firebase project settings
- `TINYURL_API_KEY`: Your API key from TinyURL developer account

## **Usage:**

1. **Start the Bot:**
   ```bash
   python main.py
   ```

2. **Interact with the Bot on Telegram:**
   - Open Telegram and search for your bot by username
   - Start a conversation with the bot using the `/start` command

### **Bot Commands:**

| Command | Description |
|---------|-------------|
| `/start` | Start the bot and view main menu |
| `/broadcast <message>` | Send a message to all users (Admin only) |

### **User Interface:**

The bot provides an intuitive button-based interface with the following options:

- 📤 **Upload File**: Upload HTML or ZIP files
- 📁 **My Files**: View and access your uploaded files
- ❌ **Delete File**: Remove files you no longer need
- 🏆 **Leaderboard**: View top referrers
- ℹ️ **Help**: Get usage instructions
- 👤 **Contact Owner**: Direct link to contact the bot owner

## **File Requirements:**

- **Supported formats:** `.html` and `.zip` files
- **Maximum file size:** 5MB
- **ZIP files:** Must contain at least one `.html` file
- **Processing:** The first HTML file found in a ZIP will be used as the main file

## **Referral System:**

- Each user starts with **10 upload slots** by default
- Each successful referral adds **3 more upload slots**
- Users can share their unique referral link from the main menu
- Referral links have the format: `https://t.me/YourBotUsername?start=UserID`
- The referral leaderboard shows the top 10 users with the most referrals

## **Code Structure:**

The bot consists of the following main components:

- **Health Check Server:** Simple HTTP server that responds with "OK" on port 8080
- **Telegram Bot:** Handles user interactions and file processing
- **Firebase Integration:**
  - **Storage:** Stores uploaded HTML/ZIP files
  - **Database:** Manages user data, file metadata, and referral information
- **URL Shortening:** Uses TinyURL API to create short links for the hosted files

Key functions:
- `run_fake_server()`: Starts the health check server
- `start()`: Handles the /start command and referral processing
- `handle_file()`: Processes uploaded files
- `button_handler()`: Manages inline keyboard interactions
- `broadcast()`: Sends messages to all users (admin only)

## **Health Check Server:**

The bot includes a simple HTTP server running on port 8080 that responds with "OK" to GET requests. This server is useful for deployment on platforms that require health checks, such as:

- Heroku
- Google Cloud Run
- AWS Elastic Beanstalk

The server runs in a separate thread with the `daemon=True` flag, ensuring it terminates when the main program exits.

## **Troubleshooting:**

### **File Upload Issues:**
- **File Too Large:** Ensure files are under 5MB
- **Format Not Supported:** Only .html and .zip files are accepted
- **Upload Limit Reached:** Delete some files or get more referrals

### **Firebase Issues:**
- **Storage Access Denied:** Check Firebase Storage rules to ensure public read access
- **Database Connection Failed:** Verify Firebase credentials in .env file
- **Authentication Failed:** Ensure API key is correct and has necessary permissions

### **Bot Not Responding:**
- Check if your bot token is valid
- Ensure the script is running without errors
- Verify internet connectivity

## **Admin Features:**

The bot includes an admin broadcast feature to send messages to all users:

```
/broadcast Your message here
```

This command will only work for the Telegram user ID specified in the `ADMIN_ID` environment variable.

## **Contributing:**

Contributions are welcome! To contribute:

1. Fork the repository
2. Create a new branch (`git checkout -b feature/your-feature`)
3. Make your changes
4. Commit your changes (`git commit -m 'Add some feature'`)
5. Push to the branch (`git push origin feature/your-feature`)
6. Open a Pull Request

## **License:**

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## **Acknowledgments:**

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) for the Telegram API wrapper
- [Pyrebase](https://github.com/thisbejim/Pyrebase) for Firebase integration
- [TinyURL](https://tinyurl.com/) for URL shortening services
- [python-dotenv](https://github.com/theskumar/python-dotenv) for environment variable management

---

For questions or support, please contact [@ViperROX](https://t.me/ViperROX) on Telegram.
