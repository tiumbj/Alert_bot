# ==============================================================================
# Code: Telegram Notifier
# File: telegram_notifier.py
# Run: (imported by main.py and trade_monitor.py)
# Version: 1.0
# ==============================================================================

import os
import requests


# ==============================================================================
# CONFIGURATION
# ==============================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ==============================================================================
# SEND MESSAGE
# ==============================================================================

def send_telegram_message(message):
    """
    Send a message via Telegram Bot API.
    
    Args:
        message (str): Message text to send
    
    Returns:
        bool: True if sent successfully, False otherwise
    """
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("WARNING: Telegram credentials not set. Message not sent.")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("Telegram message sent successfully")
            return True
        else:
            print(f"Telegram send failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Telegram send error: {e}")
        return False