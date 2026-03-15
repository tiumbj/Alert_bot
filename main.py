# ==============================================================================
# Code: Signal Generator for GOLD/XAUUSD
# File: main.py
# Run: python main.py
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
import pandas as pd

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

SYMBOL = "GOLD"
MT5_SYMBOL = "GOLD"
TIMEFRAME_MINUTES = 15
SIGNAL_CHECK_INTERVAL_SEC = 60
TELEGRAM_ENABLED = True

# Risk parameters
RISK_REWARD_MIN = 1.5
ATR_SL_MULTIPLIER = 1.2
ATR_TP_MULTIPLIER = 2.5

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
DASHBOARD_STATE_PATH = RUNTIME_DIR / "dashboard_state.json"


# ==============================================================================
# LOGGING
# ==============================================================================

def ensure_directories():
    os.makedirs("logs", exist_ok=True)
    os.makedirs("runtime", exist_ok=True)


def log_system(message):
    timestamp = datetime.now(timezone.utc).isoformat()
    log_line = f"[{timestamp}] {message}\n"
    with open("logs/system.log", "a", encoding="utf-8") as f:
        f.write(log_line)
    try:
        print(log_line.strip())
    except UnicodeEncodeError:
        safe = log_line.strip().encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        print(safe)


def log_signal(signal_data):
    with open("logs/signals.jsonl", "a") as f:
        f.write(json.dumps(signal_data, ensure_ascii=False) + "\n")


# ==============================================================================
# MARKET DATA
# ==============================================================================

def get_market_data_mt5():
    """Fetch OHLC data via MT5."""
    if not mt5.initialize():
        log_system("ERROR: MT5 initialization failed")
        return None
    
    rates = mt5.copy_rates_from_pos(MT5_SYMBOL, mt5.TIMEFRAME_M15, 0, 100)
    mt5.shutdown()
    
    if rates is None or len(rates) == 0:
        log_system("ERROR: MT5 returned no data")
        return None
    
    import pandas as pd
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'tick_volume': 'Volume'}, inplace=True)
    return df


def get_market_data_fallback():
    """Fallback: fetch GC=F (gold futures) via yfinance."""
    if not YFINANCE_AVAILABLE:
        log_system("ERROR: yfinance not available for fallback")
        return None
    
    try:
        ticker = yf.Ticker("GC=F")
        df = ticker.history(period="5d", interval="15m")
        if df.empty:
            log_system("ERROR: yfinance returned empty data")
            return None
        return df
    except Exception as e:
        log_system(f"ERROR: yfinance fallback failed: {e}")
        return None


def get_market_data():
    """Main entry point for market data."""
    if MT5_AVAILABLE:
        log_system("Fetching market data via MT5")
        data = get_market_data_mt5()
        if data is not None:
            return data
        log_system("MT5 fetch failed, trying fallback")
    
    if YFINANCE_AVAILABLE:
        log_system("Fetching market data via yfinance fallback")
        return get_market_data_fallback()
    
    log_system("ERROR: No market data source available")
    return None


def get_current_price():
    """Get latest price."""
    df = get_market_data()
    if df is None or len(df) == 0:
        return None
    return df['Close'].iloc[-1]


# ==============================================================================
# INDICATORS
# ==============================================================================

def compute_atr(df, period=14):
    """Compute Average True Range."""
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr


def compute_ema(series, period):
    """Compute Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series, period=14):
    """Compute Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ==============================================================================
# SIGNAL LOGIC
# ==============================================================================

def analyze_market(df):
    """
    Simple signal logic:
    - EMA crossover (fast vs slow)
    - RSI confirmation (not overbought/oversold extremes)
    - Momentum check
    
    Returns: "BUY", "SELL", or "NO TRADE"
    """
    
    if len(df) < 50:
        return "NO TRADE", ["ข้อมูลยังไม่พอสำหรับประเมินสัญญาณ"]
    
    close = df['Close']
    
    # Compute indicators
    ema_fast = compute_ema(close, 9)
    ema_slow = compute_ema(close, 21)
    rsi = compute_rsi(close, 14)
    atr = compute_atr(df, 14)
    
    # Current values
    current_close = close.iloc[-1]
    current_ema_fast = ema_fast.iloc[-1]
    current_ema_slow = ema_slow.iloc[-1]
    current_rsi = rsi.iloc[-1]
    current_atr = atr.iloc[-1]
    
    prev_ema_fast = ema_fast.iloc[-2]
    prev_ema_slow = ema_slow.iloc[-2]
    
    reasons = []
    
    # BUY conditions
    if (prev_ema_fast <= prev_ema_slow and current_ema_fast > current_ema_slow):
        if 30 < current_rsi < 70:
            if current_close > current_ema_fast:
                reasons.append("เส้น EMA สั้นตัดขึ้นเหนือ EMA ยาว (โมเมนตัมเริ่มเป็นขาขึ้น)")
                reasons.append(f"RSI อยู่โซนกลาง: {current_rsi:.1f}")
                reasons.append("ราคายืนเหนือ EMA สั้น")
                return "BUY", reasons
    
    # SELL conditions
    if (prev_ema_fast >= prev_ema_slow and current_ema_fast < current_ema_slow):
        if 30 < current_rsi < 70:
            if current_close < current_ema_fast:
                reasons.append("เส้น EMA สั้นตัดลงใต้ EMA ยาว (โมเมนตัมเริ่มเป็นขาลง)")
                reasons.append(f"RSI อยู่โซนกลาง: {current_rsi:.1f}")
                reasons.append("ราคาอยู่ใต้ EMA สั้น")
                return "SELL", reasons
    
    # NO TRADE
    reasons.append("ยังไม่เจอสัญญาณที่ชัดเจน")
    return "NO TRADE", reasons


def build_entry_telegram_message(symbol, timeframe_minutes, signal, params, reasons):
    direction = "Buy" if signal == "BUY" else "Sell"
    lines = [
        "แจ้งเตือนเข้าเทรด",
        f"{symbol} ฝั่ง {direction}",
        f"กรอบเวลา: M{timeframe_minutes}",
        "",
        f"จุดเข้า: {params['entry']}",
        f"SL (จุดตัดขาดทุน): {params['sl']}",
        f"TP (เป้ากำไร): {params['tp']}",
        f"RR: {params['rr']}",
        "",
        "เหตุผล:",
    ]
    for r in reasons[:3]:
        lines.append(f"- {r}")
    lines += [
        "",
        "ก่อนเข้า ควรดูเพิ่ม:",
        "- ราคาไม่สวนกลับแรงในแท่งล่าสุด",
        "- สเปรด/ค่าคอมฯ ไม่กว้างผิดปกติ",
        "- ถ้าราคาแกว่งแรง ให้รอแท่งยืนยันก่อนค่อยเข้า",
        "",
        "สิ่งที่ควรทำ:",
        "- ตั้งขนาดไม้ให้รับความเสี่ยงได้ และยึดตาม SL/TP ตามแผน",
    ]
    return "\n".join(lines)


def compute_trade_params(df, signal):
    """Compute entry, SL, TP based on ATR."""
    close = df['Close']
    atr = compute_atr(df, 14)
    
    entry = close.iloc[-1]
    current_atr = atr.iloc[-1]
    
    if signal == "BUY":
        sl = entry - (current_atr * ATR_SL_MULTIPLIER)
        tp = entry + (current_atr * ATR_TP_MULTIPLIER)
    elif signal == "SELL":
        sl = entry + (current_atr * ATR_SL_MULTIPLIER)
        tp = entry - (current_atr * ATR_TP_MULTIPLIER)
    else:
        return None
    
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = reward / risk if risk > 0 else 0
    
    return {
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "rr": round(rr, 2)
    }


# ==============================================================================
# MAIN SIGNAL ENGINE
# ==============================================================================

def generate_signal():
    """Main signal generation function."""
    log_system("Starting signal generation cycle")
    
    df = get_market_data()
    if df is None:
        log_system("ERROR: Failed to fetch market data")
        return
    
    signal, reasons = analyze_market(df)
    
    timestamp = datetime.now(timezone.utc).isoformat()
    
    if signal == "NO TRADE":
        log_system(f"Signal: NO TRADE - {reasons[0]}")
        return
    
    params = compute_trade_params(df, signal)
    
    if params is None:
        log_system("ERROR: Failed to compute trade params")
        return
    
    if params["rr"] < RISK_REWARD_MIN:
        log_system(f"Signal rejected: RR {params['rr']} below minimum {RISK_REWARD_MIN}")
        return
    
    # Build signal data
    signal_data = {
        "ts": timestamp,
        "symbol": SYMBOL,
        "timeframe": f"M{TIMEFRAME_MINUTES}",
        "signal": signal,
        "entry": params["entry"],
        "sl": params["sl"],
        "tp": params["tp"],
        "rr": params["rr"],
        "reasons": reasons
    }
    
    # Log to JSONL
    log_signal(signal_data)
    
    # Build Telegram message
    message = build_entry_telegram_message(SYMBOL, TIMEFRAME_MINUTES, signal, params, reasons)
    message = build_telegram_message(message)
    
    if TELEGRAM_ENABLED:
        send_telegram_message(message)
    
    log_system(f"Signal generated: {signal} @ {params['entry']}")


# ==============================================================================
# MAIN LOOP
# ==============================================================================

def main():
    ensure_directories()
    log_system("=== Signal Generator Started ===")
    log_system(f"Symbol: {SYMBOL}")
    log_system(f"Timeframe: M{TIMEFRAME_MINUTES}")
    log_system(f"Check interval: {SIGNAL_CHECK_INTERVAL_SEC}s")
    
    while True:
        try:
            generate_signal()
        except KeyboardInterrupt:
            log_system("Signal generator stopped by user")
            sys.exit(0)
        except Exception as e:
            log_system(f"ERROR in signal generation: {e}")
        
        time.sleep(SIGNAL_CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
