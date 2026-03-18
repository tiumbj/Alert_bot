# ============================================================
# Code Name: Trade Structure Monitor + Exit Engine (GOLD/XAUUSD)
# File Path: trade_monitor.py
# Run Command: python trade_monitor.py
# Version: v2.8.1
# ============================================================

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    import MetaTrader5 as mt5

    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

try:
    import yfinance as yf

    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from core.structure_engine import evaluate_trade_structure_health
except Exception:
    evaluate_trade_structure_health = None

try:
    from core.dashboard_state_writer import update_dashboard_state
except Exception:
    try:
        from dashboard_state_writer import update_dashboard_state
    except Exception:
        update_dashboard_state = None

from telegram_notifier import build_telegram_message, send_telegram_message


import logging
from logging.handlers import RotatingFileHandler

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
DASHBOARD_STATE_PATH = RUNTIME_DIR / "dashboard_state.json"
ACTIVE_TRADE_PATH = RUNTIME_DIR / "active_trade.json"
DEDUP_STATE_PATH = RUNTIME_DIR / "monitor_telegram_dedup.json"
ENTRY_DEDUP_STATE_PATH = RUNTIME_DIR / "entry_telegram_dedup.json"

def _load_env_file(path: Path) -> None:
    try:
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or not ("=" in text):
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except (OSError, UnicodeDecodeError, ValueError):
        pass

_load_env_file(PROJECT_ROOT / ".env")

DEFAULT_TIMEFRAME_MINUTES = int(os.environ.get("TIMEFRAME_MINUTES", "15"))
DEFAULT_MONITOR_INTERVAL_SEC = int(os.environ.get("MONITOR_INTERVAL_SEC", "10"))
MAX_RISK_POINTS = float(os.environ.get("MAX_RISK_POINTS", "20.0"))
TELEGRAM_MIN_INTERVAL_HEALTHY_SEC = int(os.environ.get("TELEGRAM_MIN_INTERVAL_HEALTHY_SEC", "180"))
TELEGRAM_MIN_INTERVAL_WEAKENING_SEC = int(os.environ.get("TELEGRAM_MIN_INTERVAL_WEAKENING_SEC", "120"))
TELEGRAM_MIN_INTERVAL_DEFENSIVE_SEC = int(os.environ.get("TELEGRAM_MIN_INTERVAL_DEFENSIVE_SEC", "60"))
ATR_PERIOD = int(os.environ.get("ATR_PERIOD", "14"))
BE_TRIGGER_TP_PROGRESS = float(os.environ.get("BE_TRIGGER_TP_PROGRESS", "0.60"))
EARLY_WARN_RETRACE_R = float(os.environ.get("EARLY_WARN_RETRACE_R", "0.35"))
STARTUP_HEALTH_ALERT = os.environ.get("STARTUP_HEALTH_ALERT", "True").lower() in ("true", "1", "yes")
MIN_REQUIRED_BARS = int(os.environ.get("MIN_REQUIRED_BARS", "80"))

TELEGRAM_ENABLED = os.environ.get("TELEGRAM_ENABLED", "True").lower() in ("true", "1", "yes")

logger = logging.getLogger("trade_monitor")
logger.setLevel(logging.INFO)
if not logger.handlers:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s')
    file_handler = RotatingFileHandler(log_dir / "trade_monitor.log", maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def ensure_directories() -> None:
    (PROJECT_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

def log_system(message: str) -> None:
    logger.info(message)


def _startup_mt5_status() -> tuple[str, str]:
    if not MT5_AVAILABLE:
        return "NOT_AVAILABLE", "MetaTrader5 module not installed"
    try:
        init_ok = mt5.initialize()
    except Exception as e:
        return "FAIL", f"MT5 initialize exception: {e}"
    if not init_ok:
        return "FAIL", f"MT5 initialize failed: {mt5.last_error()}"
    try:
        return "OK", "MT5 initialize ok"
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def _startup_yfinance_status() -> tuple[str, str]:
    if not YFINANCE_AVAILABLE:
        return "NOT_AVAILABLE", "yfinance module not installed"
    try:
        ticker = yf.Ticker("GC=F")
        df = ticker.history(period="1d", interval="15m")
        if df is None or df.empty:
            return "WARN", "yfinance reachable but returned empty data"
        return "OK", "yfinance data fetch ok"
    except Exception as e:
        return "FAIL", f"yfinance fetch failed: {e}"


def _startup_telegram_status() -> tuple[str, str]:
    if not TELEGRAM_ENABLED:
        return "DISABLED", "TELEGRAM_ENABLED=False"
    token = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    chat = bool(os.environ.get("TELEGRAM_CHAT_ID"))
    if token and chat:
        return "OK", "telegram credentials loaded"
    return "FAIL", "missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"


def _startup_component_status() -> dict[str, tuple[str, str]]:
    return {
        "mt5": _startup_mt5_status(),
        "yfinance": _startup_yfinance_status(),
        "telegram": _startup_telegram_status(),
        "structure_engine": ("OK", "evaluate_trade_structure_health loaded") if evaluate_trade_structure_health is not None else ("FAIL", "evaluate_trade_structure_health unavailable"),
        "dashboard_writer": ("OK", "update_dashboard_state loaded") if update_dashboard_state is not None else ("FAIL", "update_dashboard_state unavailable"),
    }


def _notify_startup_health() -> None:
    status = _startup_component_status()
    for name, (state, detail) in status.items():
        log_system(f"Startup health | {name}={state} | {detail}")

    if not STARTUP_HEALTH_ALERT:
        return
    telegram_state = status.get("telegram", ("FAIL", ""))[0]
    if telegram_state != "OK":
        return

    lines = [
        "MONITOR STARTUP HEALTH",
        "mode=trade_monitor",
    ]
    for name in ("mt5", "yfinance", "telegram", "structure_engine", "dashboard_writer"):
        state, detail = status.get(name, ("UNKNOWN", "-"))
        lines.append(f"- {name}: {state} | {detail}")
    message = build_telegram_message("\n".join(lines))
    sent = send_telegram_message(message)
    log_system("Startup health telegram sent" if sent else "Startup health telegram failed")


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, temp_path = tempfile.mkstemp(prefix=path.stem + "_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _load_json(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _parse_timeframe_minutes(active_trade: dict) -> int:
    tf = str(active_trade.get("timeframe") or "").strip().upper()
    if tf.startswith("M") and tf[1:].isdigit():
        minutes = int(tf[1:])
        if minutes > 0:
            return minutes
    return DEFAULT_TIMEFRAME_MINUTES


def _resolve_entry_id(active_trade: dict) -> int:
    raw_id = active_trade.get("entry_id")
    try:
        raw_id = int(raw_id)
        if raw_id > 0:
            return raw_id
    except Exception:
        pass

    entry_time = str(active_trade.get("entry_time") or "")
    digits = "".join(ch for ch in entry_time if ch.isdigit())
    if len(digits) >= 3:
        try:
            return int(digits[-3:])
        except Exception:
            pass

    entry_price = _safe_float(active_trade.get("entry_price"))
    if entry_price is not None:
        try:
            return int(abs(entry_price) * 100) % 1000
        except Exception:
            return 0
    return 0


def _resolve_trade_levels(active_trade: dict) -> tuple[float | None, float | None, float | None]:
    side = str(active_trade.get("side") or "").upper()
    entry = _safe_float(active_trade.get("entry_price"))
    sl = _safe_float(active_trade.get("sl"))
    if sl is None:
        sl = _safe_float(active_trade.get("invalidation"))
    tp = _safe_float(active_trade.get("tp"))
    ob_low = _safe_float(active_trade.get("active_ob_low"))
    ob_high = _safe_float(active_trade.get("active_ob_high"))
    ob_range = None
    if ob_low is not None and ob_high is not None:
        ob_range = abs(ob_high - ob_low)

    if sl is None:
        if side == "BUY":
            sl = ob_low
        elif side == "SELL":
            sl = ob_high

    if tp is None and entry is not None and sl is not None:
        risk = abs(entry - sl)
        if side == "BUY":
            tp = entry + risk
        elif side == "SELL":
            tp = entry - risk

    return entry, sl, tp


def load_active_trade() -> dict | None:
    if not ACTIVE_TRADE_PATH.exists():
        return None
    try:
        raw = ACTIVE_TRADE_PATH.read_text(encoding="utf-8-sig").strip()
        if not raw:
            return None
        trade = json.loads(raw)
        if not isinstance(trade, dict):
            return None
        if trade.get("active") is False:
            return None
        symbol = str(trade.get("symbol") or "").strip()
        side = str(trade.get("side") or "").strip().upper()
        entry_price = trade.get("entry_price")
        if not symbol or side not in {"BUY", "SELL"} or entry_price is None:
            return None
        return trade
    except Exception as e:
        log_system(f"ERROR loading active trade: {e}")
        return None


def active_trade_status() -> tuple[dict | None, str]:
    if not ACTIVE_TRADE_PATH.exists():
        return None, "Waiting for active trade... (active_trade.json not found)"
    try:
        raw = ACTIVE_TRADE_PATH.read_text(encoding="utf-8-sig").strip()
        if not raw:
            return None, "Waiting for active trade... (active_trade.json is empty)"
        trade = json.loads(raw)
        if not isinstance(trade, dict):
            return None, "Waiting for active trade... (active_trade.json root is not object)"
        if trade.get("active") is False:
            close_state = str(trade.get("close_state") or trade.get("exit_state") or "CLOSED")
            return None, f"Waiting for active trade... (last trade closed: {close_state})"
        symbol = str(trade.get("symbol") or "").strip()
        side = str(trade.get("side") or "").strip().upper()
        entry_price = trade.get("entry_price")
        if not symbol or side not in {"BUY", "SELL"} or entry_price is None:
            return None, "Waiting for active trade... (invalid active_trade schema)"
        return trade, ""
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        return None, f"Waiting for active trade... (invalid active_trade.json: {e})"


def _mt5_timeframe(minutes: int):
    if minutes == 60:
        return mt5.TIMEFRAME_H1
    return mt5.TIMEFRAME_M15


def _interval_from_minutes(minutes: int) -> str:
    if minutes == 60:
        return "1h"
    return "15m"


def _sanitize_market_data(df: pd.DataFrame | None, source: str, interval: str) -> pd.DataFrame | None:
    if df is None or len(df) == 0:
        log_system(f"ERROR: {source} returned empty data for {interval}")
        return None
    data = df.copy()
    if "time" not in data.columns:
        data = data.reset_index().rename(columns={"Datetime": "time", "Date": "time"})
    required = ("Open", "High", "Low", "Close")
    missing = [col for col in required if col not in data.columns]
    if missing:
        log_system(f"ERROR: {source} missing required columns: {missing}")
        return None
    for col in required:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna(subset=list(required))
    if "time" in data.columns:
        data["time"] = pd.to_datetime(data["time"], errors="coerce")
        data = data.dropna(subset=["time"]).sort_values("time").drop_duplicates(subset=["time"], keep="last")
    if len(data) < MIN_REQUIRED_BARS:
        log_system(f"ERROR: {source} has insufficient bars ({len(data)}) for {interval}")
        return None
    return data.tail(300).reset_index(drop=True)


def get_market_data_mt5(symbol: str, timeframe_minutes: int) -> pd.DataFrame | None:
    if not MT5_AVAILABLE:
        return None
    try:
        initialized = mt5.initialize()
    except RuntimeError as e:
        log_system(f"ERROR: MT5 initialization runtime error: {e}")
        return None
    except OSError as e:
        log_system(f"ERROR: MT5 initialization OS error: {e}")
        return None
    if not initialized:
        log_system(f"ERROR: MT5 initialization failed: {mt5.last_error()}")
        return None
    rates = None
    try:
        if not mt5.symbol_select(symbol, True):
            log_system(f"ERROR: MT5 ไม่สามารถเลือกสัญลักษณ์ได้: {symbol}")
            return None
        for _ in range(2):
            rates = mt5.copy_rates_from_pos(symbol, _mt5_timeframe(timeframe_minutes), 0, 300)
            if rates is not None and len(rates) > 0:
                break
            time.sleep(1)
    finally:
        mt5.shutdown()

    if rates is None or len(rates) == 0:
        log_system("ERROR: MT5 returned no OHLC data")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "tick_volume": "Volume",
        },
        inplace=True,
    )
    return _sanitize_market_data(df, "MT5", _interval_from_minutes(timeframe_minutes))


def get_market_data_fallback(symbol: str, timeframe_minutes: int) -> pd.DataFrame | None:
    if not YFINANCE_AVAILABLE:
        return None
    try:
        yf_symbol = "GC=F" if symbol.upper() in {"GOLD", "XAUUSD"} else symbol
        interval = _interval_from_minutes(timeframe_minutes)
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period="5d", interval=interval)
        return _sanitize_market_data(df, "YFINANCE", interval)
    except (OSError, RuntimeError, ValueError) as e:
        log_system(f"ERROR: yfinance fallback failed: {e}")
        return None


def get_market_data(symbol: str, timeframe_minutes: int) -> pd.DataFrame | None:
    df = get_market_data_mt5(symbol, timeframe_minutes)
    if df is not None:
        get_market_data.used_yfinance = False
        get_market_data.last_source = "MT5"
        return df
    fallback = get_market_data_fallback(symbol, timeframe_minutes)
    if fallback is not None:
        get_market_data.used_yfinance = True
        get_market_data.last_source = "YFINANCE"
        return fallback
    get_market_data.used_yfinance = False
    get_market_data.last_source = "NONE"
    return None
get_market_data.used_yfinance = False
get_market_data.last_source = "NONE"


def _atr14(df: pd.DataFrame) -> float | None:
    if df is None or len(df) < ATR_PERIOD + 2:
        return None
    try:
        high = df["High"].astype(float)
        low = df["Low"].astype(float)
        close = df["Close"].astype(float)
        tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(ATR_PERIOD).mean().iloc[-1]
        if pd.isna(atr):
            return None
        return float(atr)
    except (KeyError, TypeError, ValueError):
        return None


def _monitor_event_message(symbol: str, side: str, result: dict, current_price: float | None, entry_id: int) -> str:
    trade_state = str(result.get("trade_state") or "ENTERED")
    exit_state = str((result.get("exit_engine") or {}).get("exit_state") or "NONE")
    trade_health = result.get("trade_health") or {}
    score = trade_health.get("health_score")
    score_txt = f"{score:.2f}" if isinstance(score, (int, float)) else "-"
    struct = result.get("structure_monitor") or {}
    zone = struct.get("zone_reaction", "NONE")
    cont = struct.get("continuation", "NONE")
    exit_engine = result.get("exit_engine") or {}
    opp = exit_engine.get("opposite_structure", "NONE")
    exit_state = exit_engine.get("exit_state", "NONE")
    price_txt = f"{current_price:.2f}" if isinstance(current_price, (int, float)) else "-"
    entry_id_txt = f"ENTRY-{entry_id:03d}" if entry_id > 0 else "ENTRY-000"
    return f"Monitor: {entry_id_txt} | {trade_state} | {symbol} {side} | price={price_txt} | score={score_txt} | zone={zone} | cont={cont} | opp={opp} | exit={exit_state}"


def _build_dashboard_updates(active_trade: dict, df: pd.DataFrame, result: dict) -> tuple[dict, str]:
    symbol = str(active_trade.get("symbol") or "")
    side = str(active_trade.get("side") or "").upper()
    timeframe_minutes = _parse_timeframe_minutes(active_trade)
    current_price = _safe_float((result.get("trade_health") or {}).get("current_price"))

    trade_state = str(result.get("trade_state") or "ENTERED")
    trade_health = result.get("trade_health") or {}
    structure_monitor = result.get("structure_monitor") or {}
    exit_engine = result.get("exit_engine") or {}

    setup_context = active_trade.get("setup_context") if isinstance(active_trade.get("setup_context"), dict) else {}
    entry_state = str(setup_context.get("entry_state") or "")
    setup_choch = str(setup_context.get("choch") or "")
    setup_bos = str(setup_context.get("bos") or "")

    entry_id = _resolve_entry_id(active_trade)
    event_message = _monitor_event_message(symbol, side, result, current_price, entry_id)

    entry_id_txt = f"ENTRY-{entry_id:03d}" if entry_id > 0 else "ENTRY-000"
    market_view = f"{entry_id_txt} Position={trade_state}. Side={side}. Setup={entry_state}."
    action_view = f"{entry_id_txt} Follow the plan. Monitor invalidation and opposite structure. Current={trade_state}."
    caution_view = "If invalidation breaks or opposite CHOCH/BOS appears, prioritize risk reduction."
    trigger_view = f"Setup context: {setup_choch} / {setup_bos}"

    updates = {
        "header": {
            "mode": "PRODUCTION",
            "symbol": symbol,
            "broker": "MT5" if MT5_AVAILABLE else ("YFINANCE" if YFINANCE_AVAILABLE else ""),
            "timeframe": f"M{timeframe_minutes}",
            "system": "NOMINAL",
            "position_state": trade_state,
            "last_update": _utc_now_iso(),
        },
        "trade_health": {
            "side": trade_health.get("side") or side,
            "entry_price": active_trade.get("entry_price"),
            "current_price": trade_health.get("current_price"),
            "pnl_points": trade_health.get("pnl_points"),
            "health_score": trade_health.get("health_score"),
            "trade_state": trade_health.get("trade_state") or trade_state,
            "exit_risk": trade_health.get("exit_risk"),
            "next_action": trade_health.get("next_action"),
        },
        "structure_monitor": {
            "zone_reaction": structure_monitor.get("zone_reaction"),
            "continuation": structure_monitor.get("continuation"),
            "premise": structure_monitor.get("premise"),
            "opposite_shift": structure_monitor.get("opposite_shift"),
            "ob_integrity": structure_monitor.get("ob_integrity"),
        },
        "exit_engine": {
            "exit_state": exit_engine.get("exit_state"),
            "primary_reason": exit_engine.get("primary_reason"),
            "invalidation": active_trade.get("invalidation"),
            "opposite_structure": exit_engine.get("opposite_structure"),
            "urgency": exit_engine.get("urgency"),
        },
        "trader_mentor": {
            "market_view": market_view,
            "action_view": action_view,
            "caution_view": caution_view,
            "trigger_view": trigger_view,
        },
    }
    return updates, event_message


def _apply_take_profit(active_trade: dict, result: dict) -> dict:
    side = str(active_trade.get("side") or "").upper()
    _, _, tp_level = _resolve_trade_levels(active_trade)
    trade_health = result.get("trade_health") or {}
    current_price = _safe_float(trade_health.get("current_price"))
    if side not in {"BUY", "SELL"} or tp_level is None or current_price is None:
        return result
    if result.get("trade_state") == "HARD_EXIT":
        return result

    hit = current_price >= tp_level if side == "BUY" else current_price <= tp_level
    if not hit:
        return result

    trade_health["trade_state"] = "CLOSED"
    trade_health["next_action"] = "TAKE_PROFIT"
    trade_health["exit_risk"] = "LOW"
    result["trade_health"] = trade_health
    result["trade_state"] = "CLOSED"

    exit_engine = result.get("exit_engine") or {}
    exit_engine["exit_state"] = "TP_HIT"
    exit_engine["primary_reason"] = "Take profit target reached"
    exit_engine["urgency"] = "LOW"
    result["exit_engine"] = exit_engine
    return result


def _apply_stop_loss(active_trade: dict, result: dict) -> dict:
    side = str(active_trade.get("side") or "").upper()
    _, sl_level, _ = _resolve_trade_levels(active_trade)
    trade_health = result.get("trade_health") or {}
    current_price = _safe_float(trade_health.get("current_price"))
    if side not in {"BUY", "SELL"} or sl_level is None or current_price is None:
        return result
    if result.get("trade_state") == "HARD_EXIT":
        return result

    hit = current_price <= sl_level if side == "BUY" else current_price >= sl_level
    if not hit:
        return result

    trade_health["trade_state"] = "HARD_EXIT"
    trade_health["next_action"] = "EXIT"
    trade_health["exit_risk"] = "HIGH"
    result["trade_health"] = trade_health
    result["trade_state"] = "HARD_EXIT"

    exit_engine = result.get("exit_engine") or {}
    exit_engine["exit_state"] = "SL_HIT"
    exit_engine["primary_reason"] = "Stop loss breached"
    exit_engine["urgency"] = "HIGH"
    result["exit_engine"] = exit_engine
    return result


def _r_metrics(side: str, entry: float | None, sl: float | None, tp: float | None, current: float | None) -> dict[str, float | None]:
    if side not in {"BUY", "SELL"} or None in {entry, sl, tp, current}:
        return {"risk": None, "tp_progress": None, "r_now": None}
    risk = abs(float(entry) - float(sl))
    if risk <= 0:
        return {"risk": None, "tp_progress": None, "r_now": None}
    tp_dist = abs(float(tp) - float(entry))
    if side == "BUY":
        r_now = (float(current) - float(entry)) / risk
    else:
        r_now = (float(entry) - float(current)) / risk
    tp_progress = min(1.5, abs(float(current) - float(entry)) / tp_dist) if tp_dist > 0 else 0.0
    return {"risk": risk, "tp_progress": tp_progress, "r_now": r_now}


def _early_warning(df: pd.DataFrame, active_trade: dict, result: dict) -> bool:
    if df is None or len(df) < 4:
        return False
    side = str(active_trade.get("side") or "").upper()
    entry, sl, tp = _resolve_trade_levels(active_trade)
    current = _safe_float((result.get("trade_health") or {}).get("current_price"))
    metrics = _r_metrics(side, entry, sl, tp, current)
    r_now = metrics.get("r_now")
    if r_now is None:
        return False
    close = df["Close"].astype(float).reset_index(drop=True)
    if side == "BUY":
        flip = bool(close.iloc[-1] < close.iloc[-2] < close.iloc[-3])
    else:
        flip = bool(close.iloc[-1] > close.iloc[-2] > close.iloc[-3])
    return bool(r_now > 0.15 and flip and r_now < EARLY_WARN_RETRACE_R)


def _breakeven_advice(active_trade: dict, result: dict) -> bool:
    side = str(active_trade.get("side") or "").upper()
    entry, sl, tp = _resolve_trade_levels(active_trade)
    current = _safe_float((result.get("trade_health") or {}).get("current_price"))
    metrics = _r_metrics(side, entry, sl, tp, current)
    tp_progress = metrics.get("tp_progress")
    return bool(tp_progress is not None and tp_progress >= BE_TRIGGER_TP_PROGRESS)


def _monitor_dedup_key(active_trade: dict, result: dict, advisory_tag: str) -> str:
    symbol = str(active_trade.get("symbol") or "")
    side = str(active_trade.get("side") or "")
    trade_state = str(result.get("trade_state") or "")
    trade_health = result.get("trade_health") or {}
    struct = result.get("structure_monitor") or {}
    exit_engine = result.get("exit_engine") or {}
    entry_id = _resolve_entry_id(active_trade)
    entry_level, sl_level, tp_level = _resolve_trade_levels(active_trade)
    parts = [
        symbol,
        side,
        f"{entry_id:03d}",
        trade_state,
        str(exit_engine.get("exit_state") or ""),
        str(exit_engine.get("opposite_structure") or ""),
        str(struct.get("zone_reaction") or ""),
        str(struct.get("continuation") or ""),
        str(struct.get("ob_integrity") or ""),
        str(struct.get("premise") or ""),
        str(entry_level),
        str(active_trade.get("entry_time") or ""),
        str(tp_level),
        advisory_tag,
    ]
    return "|".join(parts)


def _format_monitor_thai(active_trade: dict, result: dict, advisory_tag: str) -> str:
    symbol = str(active_trade.get("symbol") or "GOLD")
    side = str(active_trade.get("side") or "").upper()
    direction_th = "Buy" if side == "BUY" else "Sell"
    entry_id = _resolve_entry_id(active_trade)
    entry_id_txt = f"ENTRY-{entry_id:03d}" if entry_id > 0 else "ENTRY-000"

    trade_state = str(result.get("trade_state") or "ENTERED")
    trade_health = result.get("trade_health") or {}
    struct = result.get("structure_monitor") or {}
    exit_engine = result.get("exit_engine") or {}

    current_price = trade_health.get("current_price")
    price_txt = f"{float(current_price):.2f}" if isinstance(current_price, (int, float)) else "-"
    score = trade_health.get("health_score")
    score_txt = f"{float(score):.2f}" if isinstance(score, (int, float)) else "-"

    ob_low = active_trade.get("active_ob_low")
    ob_high = active_trade.get("active_ob_high")
    ob_txt = "-"
    if ob_low is not None and ob_high is not None:
        ob_txt = f"{ob_low} - {ob_high}"

    entry_level, sl_level, tp_level = _resolve_trade_levels(active_trade)
    inv_txt = "-" if sl_level is None else str(sl_level)
    tp_txt = "-" if tp_level is None else str(tp_level)
    entry_txt = "-" if entry_level is None else str(entry_level)

    market_state = []
    if trade_state == "HEALTHY":
        market_state = ["โครงสร้างยังดี (HEALTHY)", "ราคายังรักษาโซน/สมมติฐานได้"]
    elif trade_state == "WEAKENING":
        market_state = ["โครงสร้างเริ่มอ่อน (WEAKENING)", "แรงไปต่อเริ่มไม่ชัด ต้องเฝ้าใกล้ชิด"]
    elif trade_state == "DEFENSIVE_EXIT":
        market_state = ["โครงสร้างเสียบางส่วน (DEFENSIVE_EXIT)", "เริ่มเสี่ยง ควรคิดเรื่องลดความเสี่ยง/ออกเชิงป้องกัน"]
    elif trade_state == "HARD_EXIT":
        market_state = ["โครงสร้างกลับฝั่ง/หลุดจุดสำคัญ (HARD_EXIT)", "ควรโฟกัสการปกป้องทุนเป็นหลัก"]
    elif trade_state == "CLOSED":
        market_state = ["ถึงเป้ากำไรแล้ว (CLOSED)", "จุด TP ถูกแตะแล้ว ควรปิดกำไรตามแผน"]
    else:
        market_state = ["กำลังเฝ้าโครงสร้างหลังเข้า (ENTERED)", "ยังรอให้โครงสร้างชัดเจนขึ้น"]

    chart_watch = [
        f"Entry: {entry_txt} | SL: {inv_txt} | TP: {tp_txt}",
        f"โซน OB ที่ใช้อ้างอิง: {ob_txt}",
        f"จุด invalidation: {inv_txt}",
        f"สัญญาณตรงข้าม: {exit_engine.get('opposite_structure','NONE')}",
        f"ความแข็งแรงโซน (OB integrity): {struct.get('ob_integrity','UNKNOWN')}",
    ]

    why = [
        f"zone reaction: {struct.get('zone_reaction','NONE')}",
        f"continuation: {struct.get('continuation','NONE')}",
        f"premise: {struct.get('premise','UNKNOWN')}",
        f"opposite shift: {struct.get('opposite_shift','NONE')}",
    ]
    if advisory_tag == "EARLY_WARNING":
        why.append("advisory: EARLY_WARNING")
    elif advisory_tag == "MOVE_SL_BE":
        why.append("advisory: MOVE_SL_BE")

    interpretation = []
    if trade_state == "HEALTHY":
        interpretation = ["ถือต่อได้ แต่ยังต้องเฝ้าจุด invalidation และดูว่ามีโครงสร้างตรงข้ามโผล่ไหม"]
    elif trade_state == "WEAKENING":
        interpretation = ["ระวังใกล้ชิด ถ้าเริ่มหลุดโซน/เกิด CHOCH ตรงข้าม ให้เตรียมลดความเสี่ยง"]
    elif trade_state == "DEFENSIVE_EXIT":
        interpretation = ["โหมดป้องกัน: ถ้าราคาไม่กลับมายืนยันโครงสร้างเดิม ให้พิจารณาลดไม้/ออกบางส่วน"]
    elif trade_state == "HARD_EXIT":
        interpretation = ["ควรออกก่อน/ลดความเสี่ยงทันที ตามแผนและจุด invalidation"]
    elif trade_state == "CLOSED":
        interpretation = ["ปิดกำไรตามแผน และรอโครงสร้างชุดใหม่"]

    lines = [
        "อัปเดตเฝ้าโครงสร้างหลังเข้าเทรด",
        f"{symbol} ฝั่ง {direction_th}",
        f"รหัสไม้: {entry_id_txt}",
        "",
        "สภาวะตลาด",
        f"- สถานะ: {trade_state}",
        f"- ราคาปัจจุบัน (Close): {price_txt}",
        f"- Health score: {score_txt}",
        f"- {market_state[1]}",
        "",
        "ระดับราคา",
        f"- Entry: {entry_txt}",
        f"- SL: {inv_txt}",
        f"- TP: {tp_txt}",
        "",
        "สิ่งที่ trader ควรดูบนกราฟ",
    ]
    for item in chart_watch[:4]:
        lines.append(f"- {item}")

    lines += ["", "เหตุผลที่ระบบแจ้ง"]
    for item in why[:4]:
        lines.append(f"- {item}")

    lines += ["", "สรุปการตีความ"]
    for item in interpretation[:2]:
        lines.append(f"- {item}")

    if trade_state == "HARD_EXIT" and exit_engine.get("primary_reason"):
        lines.append(f"- เหตุผลหลัก: {exit_engine.get('primary_reason')}")

    return "\n".join(lines).strip()


def _should_send_monitor_telegram(trade_state: str) -> bool:
    return trade_state in {"HEALTHY", "WEAKENING", "DEFENSIVE_EXIT", "HARD_EXIT", "CLOSED"}


def _min_interval_for_state(trade_state: str) -> int:
    if trade_state == "HEALTHY":
        return TELEGRAM_MIN_INTERVAL_HEALTHY_SEC
    if trade_state == "WEAKENING":
        return TELEGRAM_MIN_INTERVAL_WEAKENING_SEC
    if trade_state == "DEFENSIVE_EXIT":
        return TELEGRAM_MIN_INTERVAL_DEFENSIVE_SEC
    return 0


def _should_send_monitor_alert(last_sent: dict, trade_state: str, exit_state: str, advisory_tag: str, now_ts: float) -> bool:
    last_state = str(last_sent.get("trade_state") or "")
    last_exit = str(last_sent.get("exit_state") or "")
    last_adv = str(last_sent.get("advisory") or "NONE")
    last_ts = float(last_sent.get("ts_epoch") or 0)
    if trade_state != last_state or exit_state != last_exit or advisory_tag != last_adv:
        return True
    min_interval = _min_interval_for_state(trade_state)
    return (now_ts - last_ts) >= min_interval


def _mark_trade_inactive(active_trade: dict, result: dict, trade_state: str, exit_state: str) -> None:
    if trade_state not in {"HARD_EXIT", "CLOSED"}:
        return
    payload = dict(active_trade)
    payload["active"] = False
    payload["close_state"] = trade_state
    payload["exit_state"] = exit_state
    payload["close_time"] = _utc_now_iso()
    trade_health = result.get("trade_health") or {}
    payload["close_price"] = trade_health.get("current_price")
    exit_engine = result.get("exit_engine") or {}
    payload["close_reason"] = exit_engine.get("primary_reason")
    _atomic_write_json(ACTIVE_TRADE_PATH, payload)
    close_key = "|".join(
        [
            str(payload.get("symbol") or ""),
            str(payload.get("side") or ""),
            str(payload.get("entry_time") or ""),
            str(payload.get("entry_price") or ""),
            str(payload.get("close_state") or ""),
        ]
    )
    _atomic_write_json(
        DEDUP_STATE_PATH,
        {
            "closed_trade_key": close_key,
            "trade_state": trade_state,
            "exit_state": exit_state,
            "ts": _utc_now_iso(),
            "ts_epoch": time.time(),
        },
    )
    try:
        if ENTRY_DEDUP_STATE_PATH.exists():
            ENTRY_DEDUP_STATE_PATH.unlink()
    except Exception:
        pass


def monitor_cycle(active_trade: dict | None = None) -> None:
    if active_trade is None:
        active_trade = load_active_trade()
    if active_trade is None:
        log_system("Waiting for active trade...")
        return

    if evaluate_trade_structure_health is None:
        log_system("ERROR: Structure engine not available (evaluate_trade_structure_health)")
        return

    symbol = str(active_trade.get("symbol") or "").strip()
    timeframe_minutes = _parse_timeframe_minutes(active_trade)

    df = get_market_data(symbol, timeframe_minutes)
    if df is None or len(df) == 0:
        log_system("ERROR: Failed to fetch market data for active trade")
        return

    result = evaluate_trade_structure_health(df, active_trade)
    if not isinstance(result, dict):
        log_system("ERROR: Structure engine returned invalid monitor payload")
        return

    result = _apply_stop_loss(active_trade, result)
    result = _apply_take_profit(active_trade, result)

    updates, event_message = _build_dashboard_updates(active_trade, df, result)
    if update_dashboard_state is not None:
        update_dashboard_state(DASHBOARD_STATE_PATH, updates, event_message=event_message)
    else:
        log_system("ERROR: Dashboard writer not available (update_dashboard_state)")

    trade_state = str(result.get("trade_state") or "ENTERED")
    exit_state = str((result.get("exit_engine") or {}).get("exit_state") or "NONE")
    advisory_tag = "NONE"
    if trade_state not in {"HARD_EXIT", "CLOSED"}:
        if _early_warning(df, active_trade, result):
            advisory_tag = "EARLY_WARNING"
        elif _breakeven_advice(active_trade, result):
            advisory_tag = "MOVE_SL_BE"
    trade_health = result.get("trade_health") or {}
    price = trade_health.get("current_price")
    price_txt = f"{float(price):.2f}" if isinstance(price, (int, float)) else "-"
    score = trade_health.get("health_score")
    score_txt = f"{float(score):.2f}" if isinstance(score, (int, float)) else "-"
    entry_id = _resolve_entry_id(active_trade)
    entry_id_txt = f"ENTRY-{entry_id:03d}" if entry_id > 0 else "ENTRY-000"
    log_system(f"Monitor: {entry_id_txt} | {symbol} {active_trade.get('side')} | state={trade_state} | price={price_txt} | score={score_txt}")

    _mark_trade_inactive(active_trade, result, trade_state, exit_state)

    if not TELEGRAM_ENABLED or not _should_send_monitor_telegram(trade_state):
        return

    last_sent = _load_json(DEDUP_STATE_PATH) or {}
    key = _monitor_dedup_key(active_trade, result, advisory_tag)
    if last_sent.get("key") == key:
        return

    now_ts = time.time()
    if not _should_send_monitor_alert(last_sent, trade_state, exit_state, advisory_tag, now_ts):
        return

    message = _format_monitor_thai(active_trade, result, advisory_tag)
    message = build_telegram_message(message)
    send_ok = send_telegram_message(message)
    if send_ok:
        _atomic_write_json(
            DEDUP_STATE_PATH,
            {
                "key": key,
                "trade_state": trade_state,
                "exit_state": exit_state,  # ใช้ exit_state ที่กำหนดด้านบน
                "advisory": advisory_tag,
                "ts": _utc_now_iso(),
                "ts_epoch": now_ts,
            },
        )


def main() -> None:
    ensure_directories()
    log_system("=== Trade Structure Monitor Started ===")
    _notify_startup_health()

    while True:
        try:
            active_trade, wait_message = active_trade_status()
            if active_trade is None:
                log_system(wait_message)
                time.sleep(30)
                continue

            interval = active_trade.get("monitor_interval_sec", DEFAULT_MONITOR_INTERVAL_SEC)
            try:
                interval = int(interval)
            except (TypeError, ValueError):
                interval = DEFAULT_MONITOR_INTERVAL_SEC

            monitor_cycle(active_trade)
            
            sleep_time = max(5, interval)
            if getattr(get_market_data, "used_yfinance", False):
                sleep_time = max(60, sleep_time)
                
            time.sleep(sleep_time)
        except KeyboardInterrupt:
            log_system("Trade monitor stopped by user")
            sys.exit(0)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError) as e:
            log_system(f"ERROR in monitor cycle ({type(e).__name__}): {e}")
            time.sleep(20)


if __name__ == "__main__":
    main()
