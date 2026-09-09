import os

API_ID = int(os.environ.get("30668518", "0"))
API_HASH = os.environ.get("21074cfa32d2d0d8e18c95c0c0a72054", "")
BOT_TOKEN = os.environ.get("8916346588:AAGhkQiSoo8_5xch_v7grqKkoCPHV3ZsYSw", "")
MONGO_URI = os.environ.get("mongodb+srv://onkarsahu224_db_user:Flu2dncFuAZtfRfF@cluster0.i2mi0k4.mongodb.net", "")
MONGO_DB_NAME = os.environ.get("onkarsahu224_db_user", "guessbot")

# Owner user_id (Telegram numeric ID) - has full control, can add other admins
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

SUPPORT_GROUP_LINK = os.environ.get("SUPPORT_GROUP_LINK", "https://t.me/rareholds")
DEVELOPER_USERNAME = os.environ.get("DEVELOPER_USERNAME", "ownerxaura07")

# Game timing (seconds)
GUESS_WINDOW_SECONDS = int(os.environ.get("GUESS_WINDOW_SECONDS", "20"))
GAP_BETWEEN_DROPS_SECONDS = int(os.environ.get("GAP_BETWEEN_DROPS_SECONDS", "2"))

# How many recent drops to remember per group to avoid quick repeats
NO_REPEAT_HISTORY = int(os.environ.get("NO_REPEAT_HISTORY", "50"))

# Auto web-fetched cards (/startweb) — always Rare, fixed points
AUTO_CARD_POINTS = int(os.environ.get("AUTO_CARD_POINTS", "5"))
AUTO_CARD_INTERVAL_SECONDS = int(os.environ.get("AUTO_CARD_INTERVAL_SECONDS", "10"))

# Health check server port (for Render web service / uptime pings)
PORT = int(os.environ.get("PORT", "8080"))
