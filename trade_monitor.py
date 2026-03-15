# ==============================================================================
# Code: Trade Monitor for Active Positions
# File: trade_monitor.py
# Run: python trade_monitor.py
# Version: 1.0
# ==============================================================================

import json
import os
import sys
from datetime import datetime, timezone
import time

# Attempt MT5 import
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# Fallback import
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

from telegram_notifier import send_telegram_message


# ==============================================================================
# CONFIGURATION
# ==============================================================================

ACTIVE_TRADE_FILE = "runtime/active_trade.json"
MT5_SYMBOL = "XAUUSD"
TELEGRAM_ENABLED = True


# ==============================================================================
# LOGGING
# ==============================================================================

def ensure_directories():
    os.makedirs("logs", exist_ok=True)
    os.makedirs("runtime", exist_ok=True)


def log_system(message):
    timestamp = datetime.now(timezone.utc).isoformat()
    log_line = f"[{timestamp}] {message}\n"
    with open("logs/system.log", "a") as f:
        f.write(log_line)
    print(log_line.strip())


def log_monitor(monitor_data):
    with open("logs/monitor.jsonl", "a") as f:
        f.write(json.dumps(monitor_data) + "\n")


# ==============================================================================
# MARKET DATA
# ==============================================================================

def get_current_price_mt5():
    """Fetch current price via MT5."""
    if not mt5.initialize():
        log_system("ERROR: MT5 initialization failed")
        return None
    
    tick = mt5.symbol_info_tick(MT5_SYMBOL)
    mt5.shutdown()
    
    if tick is None:
        log_system("ERROR: MT5 returned no tick data")
        return None
    
    return tick.bid


def get_current_price_fallback():
    """Fallback: fetch current price via yfinance."""
    if not YFINANCE_AVAILABLE:
        log_system("ERROR: yfinance not available for fallback")
        return None
    
    try:
        ticker = yf.Ticker("GC=F")
        data = ticker.history(period="1d", interval="1m")
        if data.empty:
            log_system("ERROR: yfinance returned empty data")
            return None
        return data['Close'].iloc[-1]
    except Exception as e:
        log_system(f"ERROR: yfinance fallback failed: {e}")
        return None


def get_current_price():
    """Main entry point for current price."""
    if MT5_AVAILABLE:
        price = get_current_price_mt5()
        if price is not None:
            return price
        log_system("MT5 price fetch failed, trying fallback")
    
    if YFINANCE_AVAILABLE:
        return get_current_price_fallback()
    
    log_system("ERROR: No price source available")
    return None


# ==============================================================================
# ACTIVE TRADE MANAGEMENT
# ==============================================================================

def load_active_trade():
    """Load active trade from runtime file."""
    if not os.path.exists(ACTIVE_TRADE_FILE):
        return None
    
    try:
        with open(ACTIVE_TRADE_FILE, "r") as f:
            trade = json.load(f)
        
        if not trade.get("active", False):
            return None
        
        return trade
    except Exception as e:
        log_system(f"ERROR loading active trade: {e}")
        return None


# ==============================================================================
# MONITORING LOGIC
# ==============================================================================

def evaluate_trade(trade, current_price):
    """
    Evaluate active trade and return status recommendation.
    
    Returns: status (str), reasons (list)
    
    Possible statuses:
    - HOLD
    - WATCH CLOSELY
    - TAKE SMALL PROFIT
    - EXIT NOW
    """
    
    side = trade["side"]
    entry = trade["entry_price"]
    sl = trade["sl"]
    tp = trade["tp"]
    
    # Calculate P&L in points
    if side == "BUY":
        pnl_points = current_price - entry
        risk_points = entry - sl
        reward_points = tp - entry
    else:  # SELL
        pnl_points = entry - current_price
        risk_points = sl - entry
        reward_points = entry - tp
    
    # Calculate percentages
    pnl_pct_of_risk = (pnl_points / risk_points * 100) if risk_points > 0 else 0
    pnl_pct_of_reward = (pnl_points / reward_points * 100) if reward_points > 0 else 0
    
    reasons = []
    
    # EXIT NOW conditions
    if side == "BUY" and current_price <= sl:
        reasons.append("Stop loss hit")
        return "EXIT NOW", reasons, pnl_points
    
    if side == "SELL" and current_price >= sl:
        reasons.append("Stop loss hit")
        return "EXIT NOW", reasons, pnl_points
    
    # Reversal detection (simple momentum check)
    if pnl_pct_of_reward > 40:  # In profit zone
        if side == "BUY" and pnl_points < reward_points * 0.3:
            # Was in good profit, now retracing significantly
            reasons.append("Significant retracement from peak")
            reasons.append(f"Profit reduced to {pnl_pct_of_reward:.1f}% of target")
            return "EXIT NOW", reasons, pnl_points
        
        if side == "SELL" and pnl_points < reward_points * 0.3:
            reasons.append("Significant retracement from peak")
            reasons.append(f"Profit reduced to {pnl_pct_of_reward:.1f}% of target")
            return "EXIT NOW", reasons, pnl_points
    
    # TAKE SMALL PROFIT conditions
    if pnl_pct_of_risk >= 50 and pnl_pct_of_reward < 60:
        # Decent profit but not near target, and showing weakness
        reasons.append(f"Profit at {pnl_pct_of_risk:.1f}% of risk")
        reasons.append("Target not reached but profit secured")
        return "TAKE SMALL PROFIT", reasons, pnl_points
    
    # WATCH CLOSELY conditions
    if pnl_pct_of_risk > 20 and pnl_pct_of_risk < 50:
        reasons.append(f"In profit: {pnl_pct_of_risk:.1f}% of risk")
        reasons.append("Monitor for reversal signs")
        return "WATCH CLOSELY", reasons, pnl_points
    
    if pnl_pct_of_risk < 0 and pnl_pct_of_risk > -50:
        reasons.append(f"Small drawdown: {pnl_pct_of_risk:.1f}% of risk")
        reasons.append("Watch for setup invalidation")
        return "WATCH CLOSELY", reasons, pnl_points
    
    if pnl_pct_of_risk < -50:
        reasons.append(f"Approaching stop: {pnl_pct_of_risk:.1f}% of risk")
        reasons.append("Prepare for stop loss")
        return "WATCH CLOSELY", reasons, pnl_points
    
    # HOLD conditions (default)
    if pnl_pct_of_reward >= 80:
        reasons.append(f"Near target: {pnl_pct_of_reward:.1f}% of TP")
        reasons.append("Consider taking profit manually")
    else:
        reasons.append("Trade progressing normally")
        reasons.append(f"P&L: {pnl_pct_of_risk:.1f}% of risk")
    
    return "HOLD", reasons, pnl_points


# ==============================================================================
# MONITOR CYCLE
# ==============================================================================

def monitor_cycle():
    """Single monitoring cycle."""
    trade = load_active_trade()
    
    if trade is None:
        log_system("No active trade to monitor")
        return
    
    log_system(f"Monitoring active {trade['side']} trade on {trade['symbol']}")
    
    current_price = get_current_price()
    
    if current_price is None:
        log_system("ERROR: Failed to fetch current price")
        return
    
    status, reasons, pnl_points = evaluate_trade(trade, current_price)
    
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Build monitor data
    monitor_data = {
        "ts": timestamp,
        "symbol": trade["symbol"],
        "side": trade["side"],
        "price": round(current_price, 2),
        "pnl_points": round(pnl_points, 2),
        "status": status,
        "reasons": reasons
    }
    
    # Log to JSONL
    log_monitor(monitor_data)
    
    # Send Telegram alert for important statuses
    if status in ["WATCH CLOSELY", "TAKE SMALL PROFIT", "EXIT NOW"]:
        emoji_map = {
            "WATCH CLOSELY": "👀",
            "TAKE SMALL PROFIT": "💰",
            "EXIT NOW": "🚨"
        }
        
        emoji = emoji_map.get(status, "📊")
        
        message = f"{emoji} *TRADE ALERT*\n\n"
        message += f"Symbol: {trade['symbol']}\n"
        message += f"Side: {trade['side']}\n"
        message += f"Status: *{status}*\n"
        message += f"Current Price: {current_price:.2f}\n"
        message += f"Entry: {trade['entry_price']}\n"
        message += f"P&L Points: {pnl_points:+.2f}\n\n"
        message += f"Reasons:\n"
        for r in reasons:
            message += f"  • {r}\n"
        message += f"\n⚠️ Manual action required"
        
        if TELEGRAM_ENABLED:
            send_telegram_message(message)
    
    log_system(f"Monitor status: {status} | P&L: {pnl_points:+.2f} points")


# ==============================================================================
# MAIN LOOP
# ==============================================================================

def main():
    ensure_directories()
    log_system("=== Trade Monitor Started ===")
    
    while True:
        try:
            trade = load_active_trade()
            
            if trade is None:
                log_system("Waiting for active trade...")
                time.sleep(30)
                continue
            
            interval = trade.get("monitor_interval_sec", 20)
            
            monitor_cycle()
            time.sleep(interval)
            
        except KeyboardInterrupt:
            log_system("Trade monitor stopped by user")
            sys.exit(0)
        except Exception as e:
            log_system(f"ERROR in monitor cycle: {e}")
            time.sleep(20)


if __name__ == "__main__":
    main()