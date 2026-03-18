# ============================================================
# Code Name: Entry Structure Runner (GOLD/XAUUSD)
# File Path: main.py
# Run Command: python main.py
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
from typing import Any


try:
    import MetaTrader5 as mt5

    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

MT5_TIMEFRAME_M15 = mt5.TIMEFRAME_M15 if MT5_AVAILABLE else 15
MT5_TIMEFRAME_H1 = mt5.TIMEFRAME_H1 if MT5_AVAILABLE else 60


import pandas as pd

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


import logging
from logging.handlers import RotatingFileHandler

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
DASHBOARD_STATE_PATH = RUNTIME_DIR / "dashboard_state.json"
DEDUP_STATE_PATH = RUNTIME_DIR / "entry_telegram_dedup.json"

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

SYMBOL = os.environ.get("SYMBOL", "GOLD")
MT5_SYMBOL = os.environ.get("MT5_SYMBOL", "GOLD")
TIMEFRAME_MINUTES = int(os.environ.get("TIMEFRAME_MINUTES", "15"))
SIGNAL_CHECK_INTERVAL_SEC = int(os.environ.get("SIGNAL_CHECK_INTERVAL_SEC", "10"))
TELEGRAM_MIN_INTERVAL_SEC = int(os.environ.get("TELEGRAM_MIN_INTERVAL_SEC", "60"))
TELEGRAM_ENABLED = os.environ.get("TELEGRAM_ENABLED", "True").lower() in ("true", "1", "yes")
MAX_RISK_POINTS = float(os.environ.get("MAX_RISK_POINTS", "20.0"))
ATR_PERIOD = int(os.environ.get("ATR_PERIOD", "14"))
ENTRY_COOLDOWN_MIN_SEC = int(os.environ.get("ENTRY_COOLDOWN_MIN_SEC", "8"))
ENTRY_COOLDOWN_MAX_SEC = int(os.environ.get("ENTRY_COOLDOWN_MAX_SEC", "45"))
PULLBACK_ATR_MULT = float(os.environ.get("PULLBACK_ATR_MULT", "0.85"))
MAX_ALERT_OB_RANGE_POINTS = float(os.environ.get("MAX_ALERT_OB_RANGE_POINTS", "35.0"))
STARTUP_HEALTH_ALERT = os.environ.get("STARTUP_HEALTH_ALERT", "True").lower() in ("true", "1", "yes")
MARKET_REGIME_LOOKBACK_BARS = int(os.environ.get("MARKET_REGIME_LOOKBACK_BARS", "8"))
HIGH_VOL_RANGE_POINTS = float(os.environ.get("HIGH_VOL_RANGE_POINTS", "100.0"))
LOW_VOL_RANGE_POINTS = float(os.environ.get("LOW_VOL_RANGE_POINTS", "40.0"))
CRITICAL_ALERT_REPEAT_SEC = int(os.environ.get("CRITICAL_ALERT_REPEAT_SEC", "20"))
MIN_REQUIRED_BARS = int(os.environ.get("MIN_REQUIRED_BARS", "80"))

logger = logging.getLogger("entry_runner")
logger.setLevel(logging.INFO)
if not logger.handlers:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s')
    file_handler = RotatingFileHandler(log_dir / "main.log", maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

def ensure_directories():
    (PROJECT_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_system(message: str) -> None:
    logger.info(message)


LAST_MARKET_REGIME = ""


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
        symbol_ok = mt5.symbol_info(MT5_SYMBOL) is not None
        if symbol_ok:
            return "OK", f"symbol={MT5_SYMBOL}"
        return "WARN", f"symbol not found: {MT5_SYMBOL}"
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
        "structure_engine": ("OK", "evaluate_entry_state loaded") if evaluate_entry_state is not None else ("FAIL", "evaluate_entry_state unavailable"),
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
        "SYSTEM STARTUP HEALTH",
        f"symbol={SYMBOL} timeframe=M{TIMEFRAME_MINUTES}",
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


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _normalize_entry_state(entry_state: str) -> str:
    state = str(entry_state or "").upper()
    if state in {"EARLY", "CONFIRMED"}:
        return "SETUP"
    if state == "ZONE_READY":
        return "READY"
    if state in {"ACTIONABLE", "INVALIDATED"}:
        return state
    return "IDLE"


def _dedup_key(
    symbol: str,
    timeframe_minutes: int,
    entry_payload: dict[str, Any],
    last_close: float | None,
    advisory_tag: str,
) -> str:
    entry_state_raw = str(entry_payload.get("entry_state") or "")
    entry_state = _normalize_entry_state(entry_state_raw)
    bias = str(entry_payload.get("bias") or "")
    choch_state = str((entry_payload.get("choch") or {}).get("state") or "")
    bos_state = str((entry_payload.get("bos") or {}).get("state") or "")
    ob = entry_payload.get("order_block") or {}
    reaction = entry_payload.get("reaction") or {}
    invalidation = entry_payload.get("invalidation") or {}
    ob_low = ob.get("ob_low")
    ob_high = ob.get("ob_high")
    reaction_state = reaction.get("state")
    invalid_level = invalidation.get("level")
    levels = _derive_entry_levels(entry_payload, last_close)
    entry_level = levels.get("entry")
    sl_level = levels.get("sl")
    tp_level = levels.get("tp")
    return "|".join(
        [
            symbol,
            f"M{timeframe_minutes}",
            entry_state,
            entry_state_raw,
            bias,
            choch_state,
            bos_state,
            str(ob_low),
            str(ob_high),
            str(reaction_state),
            str(invalid_level),
            str(entry_level),
            str(sl_level),
            str(tp_level),
            advisory_tag,
        ]
    )


def _setup_key(symbol: str, timeframe_minutes: int, entry_payload: dict[str, Any]) -> str:
    bias = str(entry_payload.get("bias") or "")
    choch_state = str((entry_payload.get("choch") or {}).get("state") or "")
    bos_state = str((entry_payload.get("bos") or {}).get("state") or "")
    ob = entry_payload.get("order_block") or {}
    reaction = entry_payload.get("reaction") or {}
    invalidation = entry_payload.get("invalidation") or {}
    ob_low = ob.get("ob_low")
    ob_high = ob.get("ob_high")
    reaction_state = reaction.get("state")
    invalid_level = invalidation.get("level")
    return "|".join(
        [
            symbol,
            f"M{timeframe_minutes}",
            bias,
            choch_state,
            bos_state,
            str(ob_low),
            str(ob_high),
            str(reaction_state),
            str(invalid_level),
        ]
    )


def _resolve_entry_id(entry_state: str, setup_key: str, last_sent: dict) -> tuple[int, str]:
    entry_id = 0
    try:
        entry_id = int(last_sent.get("entry_id") or 0)
    except Exception:
        entry_id = 0

    actionable_key = str(last_sent.get("actionable_key") or "")
    if _normalize_entry_state(entry_state) == "ACTIONABLE":
        if setup_key and setup_key != actionable_key:
            entry_id += 1
            actionable_key = setup_key
    return entry_id, actionable_key


def _should_send_entry_alert(
    last_sent: dict,
    entry_state: str,
    actionable_key: str,
    dedup_key: str,
    now_ts: float,
    min_interval_sec: int,
) -> bool:
    current_state = _normalize_entry_state(entry_state)
    last_state = _normalize_entry_state(str(last_sent.get("entry_state") or ""))
    last_actionable = str(last_sent.get("actionable_key") or "")
    last_key = str(last_sent.get("key") or "")
    last_ts = float(last_sent.get("ts_epoch") or 0)
    elapsed = now_ts - last_ts

    if current_state in {"ACTIONABLE", "INVALIDATED"}:
        if current_state != last_state or dedup_key != last_key:
            return True
        if elapsed >= CRITICAL_ALERT_REPEAT_SEC:
            return True

    if current_state != last_state or actionable_key != last_actionable:
        if elapsed >= 3:
            return True

    return elapsed >= min_interval_sec


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
    except Exception:
        return None


def _market_regime(df: pd.DataFrame) -> dict[str, Any]:
    close_price = _safe_float(df["Close"].iloc[-1]) if df is not None and len(df) else None
    atr = _atr14(df)
    if df is None or len(df) == 0:
        return {"name": "UNKNOWN", "recent_range_points": None, "atr": atr, "cooldown_mult": 1.0, "pullback_mult": 1.0, "ob_range_mult": 1.0}

    lookback = max(4, min(int(MARKET_REGIME_LOOKBACK_BARS), len(df)))
    highs = df["High"].astype(float).iloc[-lookback:]
    lows = df["Low"].astype(float).iloc[-lookback:]
    recent_range = _safe_float(highs.max() - lows.min())

    if recent_range is None:
        return {"name": "NORMAL", "recent_range_points": None, "atr": atr, "cooldown_mult": 1.0, "pullback_mult": 1.0, "ob_range_mult": 1.0}

    if recent_range >= HIGH_VOL_RANGE_POINTS:
        return {"name": "HIGH_VOL", "recent_range_points": recent_range, "atr": atr, "cooldown_mult": 0.65, "pullback_mult": 1.30, "ob_range_mult": 1.60, "close": close_price}
    if recent_range <= LOW_VOL_RANGE_POINTS:
        return {"name": "LOW_VOL", "recent_range_points": recent_range, "atr": atr, "cooldown_mult": 1.20, "pullback_mult": 0.90, "ob_range_mult": 1.00, "close": close_price}
    return {"name": "NORMAL", "recent_range_points": recent_range, "atr": atr, "cooldown_mult": 1.0, "pullback_mult": 1.0, "ob_range_mult": 1.20, "close": close_price}


def _log_regime_if_changed(regime: dict[str, Any]) -> None:
    global LAST_MARKET_REGIME
    name = str(regime.get("name") or "UNKNOWN")
    if name == LAST_MARKET_REGIME:
        return
    LAST_MARKET_REGIME = name
    recent_range = regime.get("recent_range_points")
    range_txt = f"{float(recent_range):.2f}" if isinstance(recent_range, (int, float)) else "-"
    log_system(f"Market regime switched: {name} | recent_range_points={range_txt}")


def _dynamic_entry_cooldown_sec(df: pd.DataFrame) -> int:
    regime = _market_regime(df)
    atr = _atr14(df)
    if atr is None or df is None or len(df) == 0:
        return TELEGRAM_MIN_INTERVAL_SEC
    close_price = _safe_float(df["Close"].iloc[-1])
    if close_price is None or close_price <= 0:
        return TELEGRAM_MIN_INTERVAL_SEC
    vol_ratio = atr / close_price
    raw = TELEGRAM_MIN_INTERVAL_SEC * (0.0012 / max(vol_ratio, 1e-6)) * float(regime.get("cooldown_mult") or 1.0)
    return int(max(ENTRY_COOLDOWN_MIN_SEC, min(ENTRY_COOLDOWN_MAX_SEC, raw)))


def _is_pullback_ready(df: pd.DataFrame, entry_payload: dict[str, Any]) -> bool:
    if df is None or len(df) == 0:
        return False
    bias = str(entry_payload.get("bias") or "")
    ob = entry_payload.get("order_block") or {}
    if bias not in {"BULLISH", "BEARISH"} or ob.get("state") != "MAPPED":
        return False
    ob_low = _safe_float(ob.get("ob_low"))
    ob_high = _safe_float(ob.get("ob_high"))
    close_price = _safe_float(df["Close"].iloc[-1])
    atr = _atr14(df)
    if ob_low is None or ob_high is None or close_price is None or atr is None:
        return False
    regime = _market_regime(df)
    ob_mid = (ob_low + ob_high) / 2.0
    band = atr * PULLBACK_ATR_MULT * float(regime.get("pullback_mult") or 1.0)
    return (ob_mid - band) <= close_price <= (ob_mid + band)


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


def _interval_from_timeframe(timeframe: int) -> str:
    if MT5_AVAILABLE and timeframe == mt5.TIMEFRAME_H1:
        return "1h"
    return "15m"


def get_market_data_mt5(timeframe: int = MT5_TIMEFRAME_M15) -> pd.DataFrame | None:
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
        if not mt5.symbol_select(MT5_SYMBOL, True):
            log_system(f"ERROR: MT5 cannot select symbol: {MT5_SYMBOL}")
            return None
        for _ in range(2):
            rates = mt5.copy_rates_from_pos(MT5_SYMBOL, timeframe, 0, 300)
            if rates is not None and len(rates) > 0:
                break
            time.sleep(1)
    finally:
        mt5.shutdown()

    if rates is None or len(rates) == 0:
        log_system("ERROR: MT5 returned no data")
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
    return _sanitize_market_data(df, "MT5", _interval_from_timeframe(timeframe))


def get_market_data_fallback(interval: str = "15m") -> pd.DataFrame | None:
    if not YFINANCE_AVAILABLE:
        return None
    try:
        ticker = yf.Ticker("GC=F")
        df = ticker.history(period="5d", interval=interval)
        return _sanitize_market_data(df, "YFINANCE", interval)
    except (OSError, RuntimeError, ValueError) as e:
        log_system(f"ERROR: yfinance fallback failed: {e}")
        return None


def get_market_data(timeframe: int = MT5_TIMEFRAME_M15, interval: str = "15m") -> pd.DataFrame | None:
    data = get_market_data_mt5(timeframe)
    if data is not None:
        get_market_data.used_yfinance = False
        get_market_data.last_source = "MT5"
        return data

    log_system(f"MT5 data unavailable for {interval}, trying yfinance fallback...")
    fallback = get_market_data_fallback(interval)
    if fallback is not None:
        get_market_data.used_yfinance = True
        get_market_data.last_source = "YFINANCE"
        return fallback
    get_market_data.used_yfinance = False
    get_market_data.last_source = "NONE"
    return None

get_market_data.used_yfinance = False
get_market_data.last_source = "NONE"


def _map_dashboard_updates(
    symbol: str,
    timeframe_minutes: int,
    entry_payload: dict[str, Any],
    df: pd.DataFrame,
    entry_id: int,
) -> tuple[dict, str]:
    entry_state_raw = str(entry_payload.get("entry_state") or "IDLE")
    entry_state = _normalize_entry_state(entry_state_raw)
    bias = str(entry_payload.get("bias") or "NEUTRAL")

    swings = entry_payload.get("swings") or {}
    choch = entry_payload.get("choch") or {}
    bos = entry_payload.get("bos") or {}
    ob = entry_payload.get("order_block") or {}
    reaction = entry_payload.get("reaction") or {}
    invalidation = entry_payload.get("invalidation") or {}
    trigger_stack = entry_payload.get("trigger_stack") or []

    last_close = None
    try:
        last_close = float(df["Close"].iloc[-1])
    except (TypeError, ValueError, KeyError, IndexError):
        last_close = None

    ob_low = ob.get("ob_low")
    ob_high = ob.get("ob_high")
    zone_status = "IDLE"
    if reaction.get("state") == "WAITING":
        zone_status = "IDLE"
    elif reaction.get("touched") and reaction.get("valid"):
        zone_status = "REACTED"
    elif reaction.get("touched"):
        zone_status = "IN_ZONE"
    elif reaction.get("state") == "EXPIRED":
        zone_status = "EXPIRED"

    market_view = f"Bias={bias}. CHOCH={choch.get('state','NONE')}, BOS={bos.get('state','NONE')}."
    action_view = "Wait for CHOCH + BOS + valid OB + valid reaction before any manual entry."
    caution_view = "Respect invalidation. If structure flips, stand aside."
    trigger_view = "Triggers: " + (" + ".join(trigger_stack) if isinstance(trigger_stack, list) else "")

    levels = _derive_entry_levels(entry_payload, last_close)
    entry_txt = levels.get("entry")
    sl_txt = levels.get("sl")
    tp_txt = levels.get("tp")
    entry_id_txt = f"ENTRY-{entry_id:03d}" if entry_id > 0 else "ENTRY-000"
    event_message = (
        f"{entry_id_txt} | Entry state: {entry_state} ({entry_state_raw}) | bias={bias} | triggers={','.join(trigger_stack) if isinstance(trigger_stack, list) else ''}"
        f" | entry={entry_txt} | sl={sl_txt} | tp={tp_txt}"
    )

    updates = {
        "header": {
            "mode": "PRODUCTION",
            "symbol": symbol,
            "broker": "MT5" if MT5_AVAILABLE else ("YFINANCE" if YFINANCE_AVAILABLE else ""),
            "timeframe": f"M{timeframe_minutes}",
            "system": "NOMINAL",
            "last_update": _utc_now_iso(),
        },
        "market_structure": {
            "bias": bias,
            "last_swing_high": (swings.get("last_swing_high") or {}).get("price") if isinstance(swings, dict) else None,
            "last_swing_low": (swings.get("last_swing_low") or {}).get("price") if isinstance(swings, dict) else None,
            "choch": choch.get("state") if isinstance(choch, dict) else "NONE",
            "bos": bos.get("state") if isinstance(bos, dict) else "NONE",
            "active_ob_low": ob_low,
            "active_ob_high": ob_high,
            "zone_status": zone_status,
        },
        "entry_lifecycle": {
            "state": entry_state,
            "state_detail": entry_state_raw,
            "trigger_stack": trigger_stack,
            "invalidation": invalidation.get("level") if isinstance(invalidation, dict) else None,
        },
        "trader_mentor": {
            "market_view": market_view,
            "action_view": action_view,
            "caution_view": caution_view,
            "trigger_view": trigger_view,
        },
    }
    return updates, event_message


def _format_entry_thai(
    symbol: str,
    timeframe_minutes: int,
    entry_payload: dict[str, Any],
    last_close: float | None,
    entry_id: int,
    advisory_tag: str,
) -> str:
    entry_state_raw = str(entry_payload.get("entry_state") or "IDLE")
    entry_state = _normalize_entry_state(entry_state_raw)
    bias = str(entry_payload.get("bias") or "NEUTRAL")
    htf_bias = str(entry_payload.get("htf_bias") or "NEUTRAL")
    choch = entry_payload.get("choch") or {}
    bos = entry_payload.get("bos") or {}
    ob = entry_payload.get("order_block") or {}
    reaction = entry_payload.get("reaction") or {}
    invalidation = entry_payload.get("invalidation") or {}
    trigger_stack = entry_payload.get("trigger_stack") or []

    direction_th = "ขาขึ้น" if bias == "BULLISH" else ("ขาลง" if bias == "BEARISH" else "ยังไม่ชัด")
    htf_direction_th = "ขาขึ้น" if htf_bias == "BULLISH" else ("ขาลง" if htf_bias == "BEARISH" else "ยังไม่ชัด")
    side_txt = "Buy" if bias == "BULLISH" else ("Sell" if bias == "BEARISH" else "-")
    entry_id_txt = f"ENTRY-{entry_id:03d}" if entry_id > 0 else "ENTRY-000"
    last_close_txt = f"{float(last_close):.2f}" if isinstance(last_close, (int, float)) else "-"

    ob_low = ob.get("ob_low")
    ob_high = ob.get("ob_high")
    inv_level = invalidation.get("level")
    levels = _derive_entry_levels(entry_payload, last_close)
    entry_level = levels.get("entry")
    sl_level = levels.get("sl")
    tp_level = levels.get("tp")

    ob_txt = "-"
    if ob_low is not None and ob_high is not None:
        ob_txt = f"{ob_low} - {ob_high}"

    inv_txt = "-" if inv_level is None else str(inv_level)

    system_reason = []
    if isinstance(trigger_stack, list):
        for t in trigger_stack:
            system_reason.append(str(t))
    if not system_reason:
        system_reason = ["ยังไม่ครบเงื่อนไข"]

    state_title = {
        "SETUP": "โครงสร้างเริ่มครบ กำลังตั้งค่า (SETUP)",
        "READY": "ราคาเข้าโซนแล้ว รอปฏิกิริยา (READY)",
        "ACTIONABLE": "เข้าเงื่อนไขครบ พร้อมตัดสินใจ (ACTIONABLE)",
        "INVALIDATED": "โครงสร้างเสีย (INVALIDATED)",
    }.get(entry_state, f"อัปเดตโครงสร้าง ({entry_state})")

    chart_watch = []
    if entry_state == "SETUP":
        chart_watch = [
            "ดูว่าแท่งปิดยังยืนเหนือ/ใต้จุดสวิงที่ถูกเบรกได้จริงหรือไม่",
            "รอการยืนยัน BOS ด้วยแท่งปิด (ไม่รีบเข้า)",
        ]
    elif entry_state == "READY":
        chart_watch = [
            f"โซน OB: {ob_txt}",
            f"ใน 3 แท่งถัดไป ให้ดูแท่งปิด ‘ปฏิเสธโซน’ ไปทางเดียวกับโครงสร้าง",
        ]
    elif entry_state == "ACTIONABLE":
        chart_watch = [
            f"โซน OB: {ob_txt}",
            f"จุดกันความเสี่ยง (invalidation): {inv_txt}",
            "ถ้าจะเข้า ให้เข้าแบบมือ และยึดตามจุด invalidation เป็นหลัก",
        ]
    elif entry_state == "INVALIDATED":
        chart_watch = [
            f"จุด invalidation: {inv_txt}",
            "รอให้เกิด CHOCH + BOS + OB ใหม่อีกรอบก่อนค่อยพิจารณา",
        ]

    level_lines = [
        "ระดับราคา",
        f"- Entry: {float(entry_level):.2f}" if isinstance(entry_level, (int, float)) else "- Entry: -",
        f"- SL: {float(sl_level):.2f}" if isinstance(sl_level, (int, float)) else "- SL: -",
        f"- TP: {float(tp_level):.2f}" if isinstance(tp_level, (int, float)) else "- TP: -",
        "- RR: 1:1 (ตามโครงสร้าง)",
    ]

    # Add dynamic position sizing advice based on risk width
    if isinstance(entry_level, (int, float)) and isinstance(sl_level, (int, float)):
        risk_pts = abs(float(entry_level) - float(sl_level))
        if risk_pts > MAX_RISK_POINTS:
            level_lines.append(f"⚠️ คำแนะนำ: โซนกว้าง ({risk_pts:.1f} จุด) ควรลด Lot Size ลงครึ่งหนึ่งเพื่อคุมความเสี่ยง")

    market_state_lines = [
        state_title,
        f"{symbol} | กรอบเวลา M{timeframe_minutes}",
        f"รหัสไม้: {entry_id_txt} ({side_txt})",
        "",
        "สภาวะตลาด",
        f"- เทรนด์หลัก (H1): {htf_direction_th}",
        f"- เทรนด์ย่อย (M15): {direction_th}",
        f"- ราคาใกล้ล่าสุด (Close): {last_close_txt}",
        f"- CHOCH: {choch.get('state','NONE')}",
        f"- BOS: {bos.get('state','NONE')}",
    ]

    why_lines = [
        "",
        "สรุปสัญญาณ",
        f"- สถานะ: {entry_state}",
        f"- สถานะย่อยจากเอนจิน: {entry_state_raw}",
        f"- เงื่อนไขที่เกิด: {' + '.join(system_reason)}",
    ]
    if advisory_tag == "PULLBACK_READY":
        why_lines.append("- แจ้งเตือนเพิ่ม: ราคารีเทสต์โซนในเทรนด์หลัก (Pullback Ready)")

    watch_lines = ["", "สิ่งที่ควรดูบนกราฟ"]
    for line in chart_watch[:4]:
        watch_lines.append(f"- {line}")

    interpretation = []
    if entry_state == "SETUP":
        interpretation = [
            "สรุปการตีความ",
            "- ตอนนี้เป็นช่วงสร้างโครงสร้าง ยังไม่ใช่จุดเข้าที่ปลอดภัย",
            "- รอให้ครบ: CHOCH + BOS + OB + Reaction",
        ]
    elif entry_state == "READY":
        interpretation = [
            "สรุปการตีความ",
            "- ราคาเข้าโซนแล้ว เหลือแค่ดูว่าโซน ‘เอาอยู่’ ไหม",
            "- ถ้าเห็นแท่งปิดปฏิเสธชัดใน 3 แท่ง = มีโอกาสเป็น ACTIONABLE",
        ]
    elif entry_state == "ACTIONABLE":
        interpretation = [
            "สรุปการตีความ",
            "- โครงสร้าง + โซน + ปฏิกิริยา ครบแล้ว",
            "- เทรดเดอร์เป็นคนตัดสินใจเอง: แนะนำเริ่มด้วยขนาดไม้เล็ก",
        ]
    elif entry_state == "INVALIDATED":
        interpretation = [
            "สรุปการตีความ",
            "- โซน/สมมติฐานที่รอไว้เสียแล้ว",
            "- หยุดคิดฝืนทิศ รอเซ็ตอัพชุดใหม่เท่านั้น",
        ]

    lines = market_state_lines + [""] + level_lines + why_lines + watch_lines + [""] + interpretation
    return "\n".join(lines).strip()


def _derive_entry_levels(entry_payload: dict[str, Any], last_close: float | None) -> dict[str, float | None]:
    bias = str(entry_payload.get("bias") or "")
    swings = entry_payload.get("swings") or {}
    ob = entry_payload.get("order_block") or {}
    invalidation = entry_payload.get("invalidation") or {}

    ob_low = ob.get("ob_low")
    ob_high = ob.get("ob_high")
    inv_level = invalidation.get("level")

    entry = None
    sl = None
    tp = None

    if bias == "BULLISH":
        entry = ob_high if ob_high is not None else last_close
        sl_candidates = [level for level in [inv_level, ob_low] if isinstance(level, (int, float))]
        sl = min(sl_candidates) if sl_candidates else None
        if entry is not None and sl is not None:
            pass # Removed rigid risk limit to let structure guide SL
        if isinstance(swings, dict):
            last_high = swings.get("last_swing_high") or {}
            if isinstance(last_high, dict):
                tp_candidate = last_high.get("price")
                try:
                    if tp_candidate is not None and entry is not None and float(tp_candidate) > float(entry):
                        tp = float(tp_candidate)
                except Exception:
                    tp = None
        if tp is None and entry is not None and sl is not None:
            try:
                tp = float(entry) + abs(float(entry) - float(sl))
            except Exception:
                tp = None
    elif bias == "BEARISH":
        entry = ob_low if ob_low is not None else last_close
        sl_candidates = [level for level in [inv_level, ob_high] if isinstance(level, (int, float))]
        sl = max(sl_candidates) if sl_candidates else None
        if entry is not None and sl is not None:
            pass # Removed rigid risk limit to let structure guide SL
        if isinstance(swings, dict):
            last_low = swings.get("last_swing_low") or {}
            if isinstance(last_low, dict):
                tp_candidate = last_low.get("price")
                try:
                    if tp_candidate is not None and entry is not None and float(tp_candidate) < float(entry):
                        tp = float(tp_candidate)
                except Exception:
                    tp = None
        if tp is None and entry is not None and sl is not None:
            try:
                tp = float(entry) - abs(float(entry) - float(sl))
            except Exception:
                tp = None

    return {"entry": entry, "sl": sl, "tp": tp}


def _should_send_entry_telegram(entry_state: str, bias: str) -> bool:
    if bias not in {"BULLISH", "BEARISH"}:
        return False
    return _normalize_entry_state(entry_state) in {"SETUP", "READY", "ACTIONABLE", "INVALIDATED"}


def _is_quality_entry_signal(entry_payload: dict[str, Any], pullback_ready: bool) -> bool:
    entry_state = _normalize_entry_state(str(entry_payload.get("entry_state") or "IDLE"))
    bos_state = str((entry_payload.get("bos") or {}).get("state") or "NONE")
    ob_state = str((entry_payload.get("order_block") or {}).get("state") or "NONE")
    reaction_state = str((entry_payload.get("reaction") or {}).get("state") or "NONE")

    if entry_state == "ACTIONABLE":
        return True
    if entry_state == "INVALIDATED":
        return True
    if entry_state == "READY":
        return ob_state == "MAPPED" and reaction_state in {"WAITING", "TOUCHED", "VALID"}
    if entry_state == "SETUP":
        return bos_state in {"BULLISH_CONFIRMED", "BEARISH_CONFIRMED"}
    return False


def run_cycle() -> None:
    df_m15 = get_market_data(MT5_TIMEFRAME_M15, "15m")
    df_h1 = get_market_data(MT5_TIMEFRAME_H1, "1h")

    if df_m15 is None or len(df_m15) == 0:
        log_system("ERROR: Failed to fetch M15 market data")
        return
    df = df_m15
    regime = _market_regime(df)
    _log_regime_if_changed(regime)

    if evaluate_entry_state is None:
        log_system("ERROR: Structure engine not available (evaluate_entry_state)")
        return

    entry_payload = evaluate_entry_state(df_m15, df_h1)
    if not isinstance(entry_payload, dict):
        log_system("ERROR: Structure engine returned invalid payload")
        return

    last_close = None
    try:
        last_close = float(df["Close"].iloc[-1])
    except (TypeError, ValueError, KeyError, IndexError):
        last_close = None

    entry_state_raw = str(entry_payload.get("entry_state") or "IDLE")
    entry_state = _normalize_entry_state(entry_state_raw)
    setup_key = _setup_key(SYMBOL, TIMEFRAME_MINUTES, entry_payload)
    last_sent = _load_json(DEDUP_STATE_PATH) or {}
    entry_id, actionable_key = _resolve_entry_id(entry_state_raw, setup_key, last_sent)
    pullback_ready = entry_state == "SETUP" and _is_pullback_ready(df, entry_payload)
    quality_signal = _is_quality_entry_signal(entry_payload, pullback_ready)

    ob_low = _safe_float(entry_payload.get("order_block", {}).get("ob_low"))
    ob_high = _safe_float(entry_payload.get("order_block", {}).get("ob_high"))
    if ob_low is not None and ob_high is not None:
        risk = abs(ob_high - ob_low)
        dynamic_ob_limit = MAX_ALERT_OB_RANGE_POINTS * float(regime.get("ob_range_mult") or 1.0)
        if risk > dynamic_ob_limit and entry_state in {"READY", "ACTIONABLE"}:
            quality_signal = False
            pullback_ready = False

    updates, event_message = _map_dashboard_updates(SYMBOL, TIMEFRAME_MINUTES, entry_payload, df, entry_id)
    if update_dashboard_state is not None:
        update_dashboard_state(DASHBOARD_STATE_PATH, updates, event_message=event_message if quality_signal else None)
    else:
        log_system("ERROR: Dashboard writer not available (update_dashboard_state)")

    bias = str(entry_payload.get("bias") or "NEUTRAL")
    advisory_tag = "PULLBACK_READY" if pullback_ready else "BASE"
    should_alert = (_should_send_entry_telegram(entry_state, bias) and quality_signal) or pullback_ready
    if not TELEGRAM_ENABLED or not should_alert:
        return

    now_ts = time.time()
    cooldown_sec = _dynamic_entry_cooldown_sec(df)
    key = _dedup_key(SYMBOL, TIMEFRAME_MINUTES, entry_payload, last_close, advisory_tag)
    if not _should_send_entry_alert(last_sent, entry_state_raw, actionable_key, key, now_ts, cooldown_sec):
        return
    if last_sent.get("key") == key and entry_state not in {"ACTIONABLE", "INVALIDATED"}:
        return

    message = _format_entry_thai(SYMBOL, TIMEFRAME_MINUTES, entry_payload, last_close, entry_id, advisory_tag)
    message = build_telegram_message(message)
    send_ok = send_telegram_message(message)
    if send_ok:
        _atomic_write_json(
            DEDUP_STATE_PATH,
            {
                "key": key,
                "entry_state": entry_state_raw,
                "entry_state_norm": entry_state,
                "ts": _utc_now_iso(),
                "ts_epoch": now_ts,
                "entry_id": entry_id,
                "actionable_key": actionable_key,
            },
        )


def main():
    ensure_directories()
    log_system("=== Entry Structure Runner Started ===")
    log_system(f"Symbol: {SYMBOL}")
    log_system(f"Timeframe: M{TIMEFRAME_MINUTES}")
    log_system(f"Check interval: {SIGNAL_CHECK_INTERVAL_SEC}s")
    _notify_startup_health()

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            log_system("Runner stopped by user")
            sys.exit(0)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError) as e:
            log_system(f"ERROR in runner cycle ({type(e).__name__}): {e}")

        sleep_time = SIGNAL_CHECK_INTERVAL_SEC
        if getattr(get_market_data, "used_yfinance", False):
            sleep_time = max(60, SIGNAL_CHECK_INTERVAL_SEC)
            
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
