# config.py
import os

# Anthropic API key — set in your environment or .env file
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# How many recent messages to pull per run
MESSAGE_FETCH_LIMIT = 50

# How many minutes back to look (avoids reprocessing old messages)
LOOKBACK_MINUTES = 60

# Path to iMessage database
CHAT_DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")

# STOP reply text
STOP_REPLY_TEXT = "STOP"

# Dry run mode — if True, logs actions but does NOT send, block, or delete
DRY_RUN = False

# Log file
LOG_FILE = "sms_agent.log"
