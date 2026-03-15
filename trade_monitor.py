# ==============================================================================
# Code: Trade Monitor for Active Positions
# File: trade_monitor.py
# Run: python trade_monitor.py
# Version: 1.0
# ==============================================================================

import json
import os
import sys
from pathlib import Path
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

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from core.structure_engine import evaluate_entry_state
except Exception:
    evaluate_entry_state = None

try:
    from core.dashboard_state_writer import update_dashboard_state
except Exception:
    try:
        from dashboard_state_writer import update_dashboard_state
    except Exception:
        update_dashboard_state = None

from telegram_notifier import build_telegram_message, send_telegram_message


# ==============================================================================
# CONFIGURATION
# ==============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
DASHBOARD_STATE_PATH = RUNTIME_DIR / "dashboard_state.json"

ACTIVE_TRADE_FILE = RUNTIME_DIR / "active_trade.json"
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

def get_current_price_mt5(symbol):
    """Fetch current price via MT5."""
    if not mt5.initialize():
        log_system("ERROR: MT5 initialization failed")
        return None
    
    try:
        if not mt5.symbol_select(symbol, True):
            log_system(f"ERROR: MT5 ไม่สามารถเลือกสัญลักษณ์ได้: {symbol}")
            return None

        tick = mt5.symbol_info_tick(symbol)
    finally:
        mt5.shutdown()
    
    if tick is None:
        log_system("ERROR: MT5 returned no tick data")
        return None
    
    return tick.bid


def get_current_price_fallback(symbol):
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


def get_current_price(symbol):
    """Main entry point for current price."""
    if MT5_AVAILABLE:
        price = get_current_price_mt5(symbol)
        if price is not None:
            return price
        log_system("MT5 price fetch failed, trying fallback")
    
    if YFINANCE_AVAILABLE:
        return get_current_price_fallback(symbol)
    
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
        with open(ACTIVE_TRADE_FILE, "r", encoding="utf-8-sig") as f:
            raw = f.read().strip()

        if not raw:
            return None

        trade = None
        try:
            trade = json.loads(raw)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            idx = 0
            objs = []
            while idx < len(raw):
                while idx < len(raw) and raw[idx].isspace():
                    idx += 1
                if idx >= len(raw):
                    break
                try:
                    obj, end = decoder.raw_decode(raw, idx)
                except json.JSONDecodeError:
                    break
                objs.append(obj)
                idx = end
            if objs:
                trade = objs[-1]

        if not isinstance(trade, dict):
            return None

        if trade.get("active", False):
            return trade

        return None
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
        reasons.append("ราคาแตะจุดตัดขาดทุน (SL)")
        return "EXIT NOW", reasons, pnl_points
    
    if side == "SELL" and current_price >= sl:
        reasons.append("ราคาแตะจุดตัดขาดทุน (SL)")
        return "EXIT NOW", reasons, pnl_points
    
    # Reversal detection (simple momentum check)
    if pnl_pct_of_reward > 40:  # In profit zone
        if side == "BUY" and pnl_points < reward_points * 0.3:
            reasons.append("กำไรย่อกลับแรงหลังจากเคยไปต่อได้ดี")
            reasons.append(f"ตอนนี้เหลือประมาณ {pnl_pct_of_reward:.1f}% ของเป้าหมาย")
            return "EXIT NOW", reasons, pnl_points
        
        if side == "SELL" and pnl_points < reward_points * 0.3:
            reasons.append("กำไรย่อกลับแรงหลังจากเคยไปต่อได้ดี")
            reasons.append(f"ตอนนี้เหลือประมาณ {pnl_pct_of_reward:.1f}% ของเป้าหมาย")
            return "EXIT NOW", reasons, pnl_points
    
    # TAKE SMALL PROFIT conditions
    if pnl_pct_of_risk >= 50 and pnl_pct_of_reward < 60:
        reasons.append(f"กำไรประมาณ {pnl_pct_of_risk:.1f}% เมื่อเทียบกับความเสี่ยง (R)")
        reasons.append("ยังไม่ถึงเป้า แต่เริ่มมีสัญญาณชะลอ ควรล็อกกำไรบางส่วน")
        return "TAKE SMALL PROFIT", reasons, pnl_points
    
    # WATCH CLOSELY conditions
    if pnl_pct_of_risk > 20 and pnl_pct_of_risk < 50:
        reasons.append(f"เริ่มมีกำไร: {pnl_pct_of_risk:.1f}% ของความเสี่ยง (R)")
        reasons.append("เฝ้าดูว่าราคาจะกลับตัวหรือไม่")
        return "WATCH CLOSELY", reasons, pnl_points
    
    if pnl_pct_of_risk < 0 and pnl_pct_of_risk > -50:
        reasons.append(f"ติดลบเล็กน้อย: {pnl_pct_of_risk:.1f}% ของความเสี่ยง (R)")
        reasons.append("ดูว่าราคาเริ่มผิดทางจนทำให้แผนเสียหรือยัง")
        return "WATCH CLOSELY", reasons, pnl_points
    
    if pnl_pct_of_risk < -50:
        reasons.append(f"เริ่มเข้าใกล้ SL: {pnl_pct_of_risk:.1f}% ของความเสี่ยง (R)")
        reasons.append("เตรียมรับมือ ถ้าหลุดจุดสำคัญให้ยอมออกตามแผน")
        return "WATCH CLOSELY", reasons, pnl_points
    
    # HOLD conditions (default)
    if pnl_pct_of_reward >= 80:
        reasons.append(f"ใกล้เป้ากำไร: {pnl_pct_of_reward:.1f}% ของ TP")
        reasons.append("พิจารณาทยอยปิดกำไรเพื่อไม่ให้กำไรหาย")
    else:
        reasons.append("ราคายังเดินตามแผนโดยรวม")
        reasons.append(f"P&L ตอนนี้: {pnl_pct_of_risk:.1f}% ของความเสี่ยง (R)")
    
    return "HOLD", reasons, pnl_points


def get_thai_action(status):
    return {
        "HOLD": "ถือต่อ",
        "WATCH CLOSELY": "ระวังใกล้ชิด",
        "TAKE SMALL PROFIT": "ทยอยปิดกำไร",
        "EXIT NOW": "ควรออกก่อน",
    }.get(status, "ระวังใกล้ชิด")


def build_monitor_telegram_message(trade, current_price, status, reasons, pnl_points):
    direction = "Buy" if trade["side"] == "BUY" else "Sell"
    action = get_thai_action(status)
    entry = trade["entry_price"]
    sl = trade["sl"]
    tp = trade["tp"]

    if trade["side"] == "BUY":
        to_tp = tp - current_price
        to_sl = current_price - sl
        price_now = (
            "ราคายืนเหนือจุดเข้า ยังพอไปต่อได้"
            if current_price >= entry
            else "ราคาหลุดต่ำกว่าจุดเข้า โมเมนตัมเริ่มอ่อน"
        )
        momentum_now = "โมเมนตัมยังสนับสนุนฝั่ง Buy" if pnl_points > 0 else "โมเมนตัมฝั่ง Buy อ่อนลง ควรระวัง"
    else:
        to_tp = current_price - tp
        to_sl = sl - current_price
        price_now = (
            "ราคาลงต่ำกว่าจุดเข้า ยังพอไปต่อได้"
            if current_price <= entry
            else "ราคาดันกลับเหนือจุดเข้า โมเมนตัมเริ่มอ่อน"
        )
        momentum_now = "โมเมนตัมยังสนับสนุนฝั่ง Sell" if pnl_points > 0 else "โมเมนตัมฝั่ง Sell อ่อนลง ควรระวัง"

    if status == "EXIT NOW":
        price_now = "ราคาไปผิดทาง/หลุดจุดสำคัญแล้ว"
        momentum_now = "โมเมนตัมเสีย ควรลดความเสี่ยงทันที"
    elif status == "TAKE SMALL PROFIT":
        momentum_now = "ยังมีกำไร แต่แรงเริ่มชะลอ เหมาะกับการล็อกกำไรบางส่วน"

    lines = [
        "อัปเดตการเฝ้าไม้",
        f"{trade['symbol']} ฝั่ง {direction}",
        "",
        "สถานะ",
        f"- {action}",
        "",
        "ราคา",
        f"- ราคาปัจจุบัน: {current_price:.2f}",
        f"- จุดเข้า: {entry} | SL: {sl} | TP: {tp}",
        "",
        "ภาพรวมตอนนี้",
        f"- {price_now}",
        f"- {momentum_now}",
        "",
        "เหตุผล",
    ]
    for r in reasons[:3]:
        lines.append(f"- {r}")

    watch_lines = []
    if to_sl is not None:
        if to_sl <= 0:
            watch_lines.append("- ราคาอยู่ใกล้ SL มาก ให้เข้มงวดตามแผน")
        else:
            watch_lines.append(f"- ระยะถึง SL ประมาณ {abs(to_sl):.2f} จุด")
    if to_tp is not None:
        watch_lines.append(f"- ระยะถึง TP ประมาณ {abs(to_tp):.2f} จุด")

    lines += [
        "",
        "สิ่งที่ควรทำ",
        f"- {action}",
    ]
    lines += watch_lines[:2]
    return "\n".join(lines)


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
    
    current_price = get_current_price(trade["symbol"])
    
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
        message = build_monitor_telegram_message(trade, current_price, status, reasons, pnl_points)
        message = build_telegram_message(message)
        
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
