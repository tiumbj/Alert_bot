# ==============================================================================
# Code: Telegram Notifier
# File: telegram_notifier.py
# Run: (imported by main.py and trade_monitor.py)
# Version: 1.2
# ==============================================================================

import os
import time
from pathlib import Path
import requests


# ==============================================================================
# CONFIGURATION
# ==============================================================================

def _load_env_file(path: Path) -> None:
    try:
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            if "=" not in text:
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return


_load_env_file(Path.cwd() / ".env")
_load_env_file(Path(__file__).resolve().parent / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ==============================================================================
# SEND MESSAGE
# ==============================================================================

def build_telegram_message(message):
    return message


def send_telegram_message(message):
    """
    Send a message via Telegram Bot API with retry mechanism.
    
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
    }
    
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                if attempt > 1:
                    print(f"Telegram message sent successfully on attempt {attempt}")
                else:
                    print("Telegram message sent successfully")
                return True
            else:
                print(f"Telegram send failed (Attempt {attempt}/{max_retries}): {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Telegram send error (Attempt {attempt}/{max_retries}): {e}")
        
        if attempt < max_retries:
            time.sleep(2)
            
    return False
