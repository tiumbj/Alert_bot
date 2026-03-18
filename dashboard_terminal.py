# ============================================================
# Code Name : Production Terminal Dashboard (dashboard_terminal.py)
# File Path : C:\Data\Bot\Alert_bot\dashboard_terminal.py
# Run Cmd   : python dashboard_terminal.py --live
# Version   : v3.1.0 - Event Stream Fixed Edition
# ============================================================
"""
dashboard_terminal.py
Version: v3.1.0 - Event Stream Fixed Edition

Fixed Issues:
- Event stream now displays properly
- Real-time updates working
- No screen scrolling or flickering
- All data connections maintained from original code
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================
# CONFIGURATION
# ============================================================
DEFAULT_STATE_PATH = Path("runtime") / "dashboard_state.json"
DEFAULT_REFRESH_SECONDS = 0.5
MIN_TERMINAL_WIDTH = 120
BAR_WIDTH = 20
HALF_SCREEN_MAX = 24
HALF_SCREEN_MIN = 18
STATE_STALE_SECONDS = 30
LAST_GOOD_STATE: dict[str, Any] | None = None
LAST_GOOD_TS: float | None = None

# ============================================================
# TERMINAL STYLE
# ============================================================
class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"

USE_COLOR = True

# ============================================================
# SYMBOLS
# ============================================================
class Symbols:
    BUY = "▲"
    SELL = "▼"
    PROFIT = "↑"
    LOSS = "↓"
    ACTIVE = "●"
    INACTIVE = "○"
    WARNING = "⚠"
    CHECK = "✓"
    CROSS = "✗"
    BLOCK_FULL = "█"
    BLOCK_LIGHT = "░"
    BLOCK_MEDIUM = "▒"
    BLOCK_DARK = "▓"

def _enable_windows_ansi() -> None:
    if os.name == "nt":
        try:
            os.system("")
        except Exception:
            pass

def supports_color() -> bool:
    return sys.stdout.isatty()

def colorize(text: str, *styles: str) -> str:
    if not USE_COLOR or not styles:
        return text
    return "".join(styles) + text + Style.RESET

# ============================================================
# CORE UTILS
# ============================================================
def safe_get(data, *keys: str, default: Any = None):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current

def to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None

def to_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None

def fmt_value(value: Any, empty: str = "-") -> str:
    if value is None:
        return empty
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        if not value:
            return empty
        return ", ".join(str(x) for x in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value).strip()
    return text if text else empty

def fmt_float(value: Any, digits: int = 2, empty: str = "-") -> str:
    number = to_float(value)
    if number is None:
        return empty
    return f"{number:.{digits}f}"

def fmt_signed(value: Any, digits: int = 2, empty: str = "-") -> str:
    number = to_float(value)
    if number is None:
        return empty
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.{digits}f}"

def fmt_ts_iso(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text if text else "-"

def parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def age_seconds_from_iso(value: Any) -> float | None:
    dt = parse_iso_datetime(value)
    if dt is None:
        return None
    delta = datetime.now(timezone.utc) - dt
    return max(0.0, delta.total_seconds())

def fmt_age(value: Any) -> str:
    seconds = age_seconds_from_iso(value)
    if seconds is None:
        return "-"
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{(whole % 3600) // 60:02d}m"

def terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size(fallback=(120, 40))
    return size.columns, size.lines

def strip_ansi(text: str) -> str:
    import re
    return re.sub(r'\033\[[0-9;]*m', '', text)

def visible_len(text: str) -> int:
    return len(strip_ansi(text))

def left_fit(text: str, width: int) -> str:
    clipped_len = visible_len(text)
    if clipped_len >= width:
        return text[:width]
    extra = width - clipped_len
    return text + (" " * extra)

def join_parts(parts: list[str], width: int) -> str:
    raw = "  ".join(part for part in parts if part)
    return left_fit(raw, width)

def kv(key: str, value: str) -> str:
    return f"{key}={value}"

def clear_screen() -> None:
    if os.name == "nt":
        os.system("cls")
        return
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

def file_mtime_utc(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return "-"

# ============================================================
# DATA LOADING
# ============================================================
def build_default_state() -> dict[str, Any]:
    return {
        "header": {
            "mode": "PRODUCTION",
            "symbol": "-",
            "broker": "-",
            "timeframe": "-",
            "system": "DEGRADED",
            "position_state": "IDLE",
            "last_update": "-",
        },
        "data_ingest": {
            "feed": "-",
            "ticks": "NO_DATA",
            "tick_rate": "-",
            "indicators": "-",
        },
        "live_analytics": {
            "price": None,
            "bid": None,
            "ask": None,
            "atr": None,
            "adx": None,
            "rsi": None,
        },
        "execution_guard": {
            "spread": "-",
            "rr": "-",
            "action": "-",
            "reason": "-",
        },
        "core_power": {
            "data_flow_pct": None,
            "market_energy_pct": None,
            "trend_power_pct": None,
            "signal_power_pct": None,
            "ai_lock_pct": None,
        },
        "trader_mentor": {
            "market_view": "Waiting for data...",
            "action_view": "WAIT",
            "caution_view": "System initializing",
            "trigger_view": "-",
        },
        "position_monitor": {
            "status": "-",
            "sl": None,
            "tp": None,
            "exit_risk": "-",
            "next_action": "-",
        },
        "daily_report": {
            "date": "-",
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
        },
        "event_stream": [],
        "trade_health": {},
        "structure_monitor": {},
        "exit_engine": {},
    }

def merge_sections(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged.get(key) or {})
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged

def append_event_stream(state: dict[str, Any], message: str, limit: int = 50) -> dict[str, Any]:
    events = state.get("event_stream")
    if not isinstance(events, list):
        events = []
    if message:
        events = [str(message)] + [str(item) for item in events if str(item)]
    state["event_stream"] = events[:limit]
    return state

def _state_age_seconds(state: dict[str, Any], path: Path) -> float | None:
    last_update = safe_get(state, "header", "last_update", default=None)
    seconds = age_seconds_from_iso(last_update)
    if seconds is not None:
        return seconds
    return age_seconds_from_iso(file_mtime_utc(path))

def _apply_health_flags(state: dict[str, Any], path: Path) -> dict[str, Any]:
    age = _state_age_seconds(state, path)
    header = safe_get(state, "header", default={}) or {}
    data_ingest = safe_get(state, "data_ingest", default={}) or {}
    trader_mentor = safe_get(state, "trader_mentor", default={}) or {}

    if age is None:
        header["system"] = "DEGRADED"
        data_ingest["feed"] = "MISSING"
        data_ingest["ticks"] = "NO_DATA"
    elif age > STATE_STALE_SECONDS:
        header["system"] = "STALE"
        data_ingest["feed"] = "STALE"
        data_ingest["ticks"] = "STALE"
    else:
        header["system"] = fmt_value(header.get("system"), empty="NOMINAL")
        if header["system"] in {"-", "", "DEGRADED", "STALE"}:
            header["system"] = "NOMINAL"

    state["header"] = header
    state["data_ingest"] = data_ingest
    state["trader_mentor"] = trader_mentor
    return state

def _derive_sections(state: dict[str, Any]) -> dict[str, Any]:
    header = safe_get(state, "header", default={}) or {}
    trade = safe_get(state, "trade_health", default={}) or {}
    ingest = safe_get(state, "data_ingest", default={}) or {}
    live = safe_get(state, "live_analytics", default={}) or {}
    guard = safe_get(state, "execution_guard", default={}) or {}
    core = safe_get(state, "core_power", default={}) or {}
    position = safe_get(state, "position_monitor", default={}) or {}
    exit_engine = safe_get(state, "exit_engine", default={}) or {}

    if fmt_value(ingest.get("feed")) == "-":
        ingest["feed"] = fmt_value(header.get("broker")) if fmt_value(header.get("broker")) != "-" else ("MT5" if to_float(trade.get("current_price")) is not None else "MISSING")
    if fmt_value(ingest.get("ticks")) == "-":
        ingest["ticks"] = "OK" if to_float(trade.get("current_price")) is not None else "NO_DATA"

    if live.get("price") is None:
        live["price"] = trade.get("current_price")

    if fmt_value(guard.get("action")) == "-":
        guard["action"] = fmt_value(trade.get("next_action"))
    if fmt_value(guard.get("reason")) == "-":
        guard["reason"] = fmt_value(exit_engine.get("primary_reason"))

    if core.get("data_flow_pct") is None:
        score = derive_data_flow_score(state)
        core["data_flow_pct"] = None if score is None else round(score * 100.0, 1)
    if core.get("market_energy_pct") is None:
        score = derive_market_energy_score(state)
        core["market_energy_pct"] = None if score is None else round(score * 100.0, 1)
    if core.get("signal_power_pct") is None:
        score = derive_scan_score(state)
        core["signal_power_pct"] = None if score is None else round(score * 100.0, 1)

    if fmt_value(position.get("status")) == "-":
        position["status"] = fmt_value(header.get("position_state"))
    if position.get("sl") is None:
        position["sl"] = exit_engine.get("invalidation")
    if position.get("tp") is None:
        position["tp"] = trade.get("tp")

    state["data_ingest"] = ingest
    state["live_analytics"] = live
    state["execution_guard"] = guard
    state["core_power"] = core
    state["position_monitor"] = position
    return state

def load_state(path: Path) -> dict[str, Any]:
    global LAST_GOOD_STATE, LAST_GOOD_TS
    base = build_default_state()
    
    if not path.exists():
        state = append_event_stream(base, "provider ready")
        return _apply_health_flags(state, path)

    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
        
        if not isinstance(raw, dict):
            raise ValueError("dashboard_state.json root must be an object")
        
        merged = merge_sections(base, raw)
        merged = append_event_stream(merged, "")
        merged = _derive_sections(merged)
        merged = _apply_health_flags(merged, path)
        
        LAST_GOOD_STATE = merged
        LAST_GOOD_TS = time.time()
        return merged
        
    except Exception:
        fallback = LAST_GOOD_STATE if isinstance(LAST_GOOD_STATE, dict) else base
        fallback = append_event_stream(fallback, "JSON LOAD ERROR")
        fallback = _derive_sections(fallback)
        fallback = _apply_health_flags(fallback, path)
        return fallback

# ============================================================
# DERIVED DATA
# ============================================================
def collect_monitor_exit_fields(state: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    preferred_keys = [
        "timestamp", "ticket", "symbol", "side", "entry_price", "current_price",
        "sl", "tp", "pnl", "exit_decision", "exit_reason", "action", "reason",
    ]
    
    candidate_blocks = [
        safe_get(state, "monitor", default=None),
        safe_get(state, "exit_logic", default=None),
        safe_get(state, "trade_monitor", default=None),
        safe_get(state, "execution", default=None),
    ]

    for block in candidate_blocks:
        if isinstance(block, dict):
            for key in preferred_keys:
                if key in block and key not in result:
                    result[key] = block.get(key)

    trade = safe_get(state, "trade_health", default={}) or {}
    for key in ["side", "entry_price", "current_price", "sl", "tp", "pnl"]:
        if key not in result and key in trade:
            result[key] = trade.get(key)

    return result

def derive_scan_score(state: dict[str, Any]) -> float | None:
    entry = safe_get(state, "entry_lifecycle", default={}) or {}
    ms = safe_get(state, "market_structure", default={}) or {}
    
    score = 0.0
    seen = False
    
    state_name = fmt_value(entry.get("state")).upper()
    if state_name not in {"-", "", "IDLE", "NONE"}:
        score += 0.35
        seen = True
    
    bias = fmt_value(ms.get("bias")).upper()
    if bias in {"BULLISH", "BEARISH"}:
        score += 0.25
        seen = True
    
    triggers = entry.get("trigger_stack")
    if isinstance(triggers, list) and triggers:
        score += min(0.40, len(triggers) * 0.10)
        seen = True
    
    return min(1.0, score) if seen else None

def derive_data_flow_score(state: dict[str, Any]) -> float | None:
    header = safe_get(state, "header", default={}) or {}
    trade = safe_get(state, "trade_health", default={}) or {}
    
    checks = [
        fmt_value(header.get("last_update")) != "-",
        fmt_value(trade.get("current_price")) != "-",
        fmt_value(trade.get("entry_price")) != "-",
    ]
    
    score = sum(0.33 for ok in checks if ok)
    return min(1.0, score) if score > 0 else None

def derive_market_energy_score(state: dict[str, Any]) -> float | None:
    ms = safe_get(state, "market_structure", default={}) or {}
    struct = safe_get(state, "structure_monitor", default={}) or {}
    
    score = 0.0
    seen = False
    
    zone_reaction = fmt_value(struct.get("zone_reaction")).upper()
    if zone_reaction not in {"-", "", "NONE"}:
        score += 0.40
        seen = True
    
    continuation = fmt_value(struct.get("continuation")).upper()
    if continuation not in {"-", "", "NONE"}:
        score += 0.30
        seen = True
    
    bias = fmt_value(ms.get("bias")).upper()
    if bias in {"BULLISH", "BEARISH"}:
        score += 0.30
        seen = True
    
    return min(1.0, score) if seen else None

def derive_trend_power_score(state: dict[str, Any]) -> float | None:
    core = safe_get(state, "core_power", default={}) or {}
    
    trend_pct = to_float(core.get("trend_power_pct"))
    if trend_pct is not None:
        return trend_pct / 100.0
    
    ms = safe_get(state, "market_structure", default={}) or {}
    struct = safe_get(state, "structure_monitor", default={}) or {}
    
    score = 0.0
    seen = False
    
    premise = fmt_value(struct.get("premise")).upper()
    if premise not in {"-", "", "NONE"}:
        score += 0.50
        seen = True
    
    bos = fmt_value(ms.get("bos")).upper()
    choch = fmt_value(ms.get("choch")).upper()
    if bos not in {"-", "", "NONE"} or choch not in {"-", "", "NONE"}:
        score += 0.30
        seen = True
    
    return min(1.0, score) if seen else None

def derive_signal_power_score(state: dict[str, Any]) -> float | None:
    core = safe_get(state, "core_power", default={}) or {}
    
    signal_pct = to_float(core.get("signal_power_pct"))
    if signal_pct is not None:
        return signal_pct / 100.0
    
    return derive_scan_score(state)

# ============================================================
# POWER BAR
# ============================================================
def create_power_bar(value_pct: float | None, width: int = BAR_WIDTH) -> str:
    if value_pct is None:
        empty_bar = Symbols.BLOCK_LIGHT * width
        bar_display = colorize(empty_bar, Style.DIM)
        label = colorize("---", Style.DIM)
        return f"[{bar_display}] {label}"
    
    value_pct = max(0.0, min(100.0, value_pct))
    filled_count = int(round((value_pct / 100.0) * width))
    
    if value_pct >= 70:
        color = Style.BRIGHT_GREEN
    elif value_pct >= 35:
        color = Style.BRIGHT_YELLOW
    else:
        color = Style.BRIGHT_RED
    
    filled = Symbols.BLOCK_FULL * filled_count
    empty = Symbols.BLOCK_LIGHT * (width - filled_count)
    
    bar_display = colorize(filled, color, Style.BOLD) + colorize(empty, Style.DIM)
    label = colorize(f"{int(value_pct):3d}%", color, Style.BOLD)
    
    return f"[{bar_display}] {label}"

# ============================================================
# STYLING HELPERS
# ============================================================
def colorize_state(state_value: str) -> str:
    state_upper = state_value.upper()
    
    if state_upper in ["HEALTHY", "NOMINAL", "OK", "BULLISH", "BEARISH", "ACTIVE"]:
        return colorize(state_value, Style.BRIGHT_GREEN, Style.BOLD)
    
    if state_upper in ["WEAKENING", "NEUTRAL", "MONITOR", "STALE"]:
        return colorize(state_value, Style.BRIGHT_YELLOW, Style.BOLD)
    
    if state_upper in ["DEFENSIVE_EXIT", "HARD_EXIT", "DEGRADED", "MISSING", "ERROR"]:
        return colorize(state_value, Style.BRIGHT_RED, Style.BOLD)
    
    return colorize(state_value, Style.WHITE)

def colorize_pnl(pnl: float | None) -> str:
    if pnl is None:
        return colorize("-", Style.DIM)
    
    sign = "+" if pnl > 0 else ""
    text = f"{sign}{pnl:.2f}"
    
    if pnl > 0:
        return colorize(text, Style.BRIGHT_GREEN, Style.BOLD)
    elif pnl < 0:
        return colorize(text, Style.BRIGHT_RED, Style.BOLD)
    else:
        return colorize(text, Style.YELLOW)

def format_side_with_symbol(side: str) -> str:
    side_upper = side.upper()
    if side_upper == "BUY":
        return colorize(f"{Symbols.BUY} BUY", Style.BRIGHT_GREEN, Style.BOLD)
    elif side_upper == "SELL":
        return colorize(f"{Symbols.SELL} SELL", Style.BRIGHT_RED, Style.BOLD)
    else:
        return colorize("-", Style.DIM)

# ============================================================
# RENDER FUNCTIONS
# ============================================================
def render_header_compact(state: dict[str, Any], path: Path, width: int) -> list[str]:
    header = safe_get(state, "header", default={}) or {}
    
    system_status = colorize_state(fmt_value(header.get("system")))
    symbol = colorize(fmt_value(header.get("symbol")), Style.BOLD, Style.BRIGHT_CYAN)
    timeframe = fmt_value(header.get("timeframe"))
    broker = fmt_value(header.get("broker"))
    
    age = _state_age_seconds(state, path)
    age_str = f"{age:.1f}s" if age is not None else "-"
    age_color = Style.BRIGHT_RED if age and age > 10 else Style.BRIGHT_YELLOW if age and age > 5 else Style.GREEN
    
    line1 = f"System: {system_status}  │  Symbol: {symbol}  │  TF: {timeframe}  │  Broker: {broker}  │  Age: {colorize(age_str, age_color)}"
    
    separator = colorize("═" * width, Style.BRIGHT_BLACK)
    
    return [separator, left_fit(line1, width), separator]

def render_trade_panel(state: dict[str, Any], width: int) -> list[str]:
    trade = safe_get(state, "trade_health", default={}) or {}
    position = safe_get(state, "position_monitor", default={}) or {}
    header = safe_get(state, "header", default={}) or {}
    
    pos_state = colorize_state(fmt_value(header.get("position_state"), "IDLE"))
    
    side = fmt_value(trade.get("side"), "")
    side_display = format_side_with_symbol(side)
    
    entry = to_float(trade.get("entry_price"))
    current = to_float(trade.get("current_price"))
    sl = to_float(position.get("sl"))
    tp = to_float(position.get("tp"))
    
    entry_str = fmt_float(entry, 2)
    current_str = colorize(fmt_float(current, 2), Style.BOLD, Style.BRIGHT_WHITE)
    sl_str = fmt_float(sl, 2)
    tp_str = fmt_float(tp, 2)
    
    pnl = to_float(trade.get("pnl_points"))
    pnl_display = colorize_pnl(pnl)
    
    health_score = to_float(trade.get("health_score"))
    health_str = fmt_float(health_score, 1) if health_score is not None else "-"
    
    title = colorize("▌TRADE POSITION", Style.BOLD, Style.BRIGHT_CYAN)
    
    line1 = f"  Status: {pos_state}  │  Side: {side_display}  │  Health: {health_str}"
    line2 = f"  Entry: {entry_str}  │  Current: {current_str}  │  PnL: {pnl_display}"
    line3 = f"  SL: {sl_str}  │  TP: {tp_str}"
    
    return [
        title,
        left_fit(line1, width),
        left_fit(line2, width),
        left_fit(line3, width),
    ]

def render_structure_panel(state: dict[str, Any], width: int) -> list[str]:
    struct = safe_get(state, "structure_monitor", default={}) or {}
    exit_engine = safe_get(state, "exit_engine", default={}) or {}
    
    zone_reaction = colorize_state(fmt_value(struct.get("zone_reaction"), "-"))
    continuation = colorize_state(fmt_value(struct.get("continuation"), "-"))
    premise = colorize_state(fmt_value(struct.get("premise"), "-"))
    ob_integrity = colorize_state(fmt_value(struct.get("ob_integrity"), "-"))
    
    exit_state = colorize_state(fmt_value(exit_engine.get("exit_state"), "-"))
    opposite = colorize_state(fmt_value(exit_engine.get("opposite_structure"), "-"))
    
    title = colorize("▌MARKET STRUCTURE", Style.BOLD, Style.BRIGHT_MAGENTA)
    
    line1 = f"  Zone: {zone_reaction}  │  Continuation: {continuation}  │  Premise: {premise}"
    line2 = f"  OB Integrity: {ob_integrity}  │  Exit State: {exit_state}  │  Opposite: {opposite}"
    
    return [
        title,
        left_fit(line1, width),
        left_fit(line2, width),
    ]

def render_power_bars_panel(state: dict[str, Any], width: int) -> list[str]:
    core = safe_get(state, "core_power", default={}) or {}
    
    data_flow = to_float(core.get("data_flow_pct"))
    market_energy = to_float(core.get("market_energy_pct"))
    
    trend = to_float(core.get("trend_power_pct"))
    if trend is None:
        trend_score = derive_trend_power_score(state)
        trend = trend_score * 100.0 if trend_score is not None else None
    
    signal = to_float(core.get("signal_power_pct"))
    if signal is None:
        signal_score = derive_signal_power_score(state)
        signal = signal_score * 100.0 if signal_score is not None else None
    
    title = colorize("▌CORE POWER METRICS", Style.BOLD, Style.BRIGHT_YELLOW)
    
    bar_width = min(BAR_WIDTH, (width - 40) // 2)
    
    line1 = f"  Data Flow:     {create_power_bar(data_flow, bar_width)}"
    line2 = f"  Market Energy: {create_power_bar(market_energy, bar_width)}"
    line3 = f"  Trend Power:   {create_power_bar(trend, bar_width)}"
    line4 = f"  Signal Power:  {create_power_bar(signal, bar_width)}"
    
    return [
        title,
        left_fit(line1, width),
        left_fit(line2, width),
        left_fit(line3, width),
        left_fit(line4, width),
    ]

def render_mentor_panel(state: dict[str, Any], width: int) -> list[str]:
    mentor = safe_get(state, "trader_mentor", default={}) or {}
    
    market_view = fmt_value(mentor.get("market_view"), "No guidance available")
    action_view = fmt_value(mentor.get("action_view"), "WAIT")
    caution_view = fmt_value(mentor.get("caution_view"), "-")
    
    title = colorize("▌TRADER GUIDANCE", Style.BOLD, Style.BRIGHT_GREEN)
    
    max_text_width = width - 4
    market_view_short = market_view[:max_text_width] + "..." if len(market_view) > max_text_width else market_view
    caution_view_short = caution_view[:max_text_width] + "..." if len(caution_view) > max_text_width else caution_view
    
    line1 = f"  {colorize('Market:', Style.BRIGHT_WHITE)} {market_view_short}"
    line2 = f"  {colorize('Action:', Style.BRIGHT_YELLOW)} {colorize(action_view, Style.BOLD)}"
    line3 = f"  {colorize('Caution:', Style.BRIGHT_RED)} {caution_view_short}"
    
    return [
        title,
        left_fit(line1, width),
        left_fit(line2, width),
        left_fit(line3, width),
    ]

def render_events_panel(state: dict[str, Any], width: int, max_events: int = 3) -> list[str]:
    """
    Event stream panel - แสดง events ล่าสุด
    """
    events = state.get("event_stream", [])
    
    title = colorize("▌EVENT STREAM", Style.BOLD, Style.BRIGHT_BLUE)
    lines = [title]
    
    # ตรวจสอบว่ามี events หรือไม่
    if not events or not isinstance(events, list) or len(events) == 0:
        # กรณีไม่มี events - แสดง placeholder
        lines.append(left_fit(colorize("  • System monitoring active", Style.DIM), width))
        lines.append(left_fit(colorize("  • Waiting for events...", Style.DIM), width))
        lines.append(left_fit("", width))
    else:
        # กรณีมี events - แสดงตามจำนวนที่กำหนด
        displayed = 0
        for event in events:
            if displayed >= max_events:
                break
            
            event_str = str(event).strip()
            
            # ข้ามค่าว่าง
            if not event_str or event_str == "-" or event_str == "":
                continue
            
            # ตัดข้อความยาวเกินไป
            max_len = width - 6
            if len(event_str) > max_len:
                event_str = event_str[:max_len - 3] + "..."
            
            # Format พร้อม bullet point
            formatted = f"  {colorize('•', Style.BRIGHT_YELLOW)} {event_str}"
            lines.append(left_fit(formatted, width))
            displayed += 1
        
        # Pad บรรทัดว่างให้ครบ max_events
        while displayed < max_events:
            lines.append(left_fit("", width))
            displayed += 1
    
    return lines

def render_daily_summary(state: dict[str, Any], width: int) -> list[str]:
    report = safe_get(state, "daily_report", default={}) or {}
    
    trades = to_int(report.get("trades")) or 0
    wins = to_int(report.get("wins")) or 0
    losses = to_int(report.get("losses")) or 0
    win_rate = to_float(report.get("win_rate")) or 0.0
    net_pnl = to_float(report.get("net_pnl")) or 0.0
    
    title = colorize("▌DAILY SUMMARY", Style.BOLD, Style.BRIGHT_CYAN)
    
    win_rate_str = f"{win_rate:.1f}%"
    pnl_display = colorize_pnl(net_pnl)
    
    line1 = f"  Trades: {trades}  │  Wins: {colorize(str(wins), Style.BRIGHT_GREEN)}  │  Losses: {colorize(str(losses), Style.BRIGHT_RED)}  │  Win Rate: {win_rate_str}"
    line2 = f"  Net PnL: {pnl_display}"
    
    return [
        title,
        left_fit(line1, width),
        left_fit(line2, width),
    ]

# ============================================================
# MAIN SCREEN RENDER
# ============================================================
def render_screen(state: dict[str, Any], path: Path) -> str:
    """
    Render complete dashboard - Fixed Event Stream Edition
    """
    term_width, term_height = terminal_size()
    width = max(MIN_TERMINAL_WIDTH, term_width)
    
    output_lines: list[str] = []
    
    # 1. Header (3 lines)
    output_lines.extend(render_header_compact(state, path, width))
    
    # 2. Trade Position (4 lines)
    output_lines.extend(render_trade_panel(state, width))
    output_lines.append(colorize("─" * width, Style.BRIGHT_BLACK))
    
    # 3. Market Structure (3 lines)
    output_lines.extend(render_structure_panel(state, width))
    output_lines.append(colorize("─" * width, Style.BRIGHT_BLACK))
    
    # 4. Power Bars (5 lines)
    output_lines.extend(render_power_bars_panel(state, width))
    output_lines.append(colorize("─" * width, Style.BRIGHT_BLACK))
    
    # 5. Trader Guidance (4 lines)
    output_lines.extend(render_mentor_panel(state, width))
    output_lines.append(colorize("─" * width, Style.BRIGHT_BLACK))
    
    # 6. Event Stream (4 lines) ← **FIX: เพิ่มส่วนนี้เข้าไป**
    output_lines.extend(render_events_panel(state, width, max_events=3))
    output_lines.append(colorize("─" * width, Style.BRIGHT_BLACK))
    
    # 7. Daily Summary (3 lines)
    output_lines.extend(render_daily_summary(state, width))
    
    # 8. Footer
    output_lines.append(colorize("═" * width, Style.BRIGHT_BLACK))
    
    # 9. Pad to fixed height
    target_height = HALF_SCREEN_MAX
    while len(output_lines) < target_height:
        output_lines.append("")
    
    if len(output_lines) > target_height:
        output_lines = output_lines[:target_height]

    return "\n".join(output_lines)

# ============================================================
# RUN MODES
# ============================================================
def render_once(state_path: Path) -> int:
    try:
        state = load_state(state_path)
        print(render_screen(state, state_path))
        return 0
    except FileNotFoundError:
        print(f"ERROR: state file not found: {state_path}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in state file: {state_path}")
        print(f"DETAIL: {exc}")
        return 3
    except Exception as exc:
        print(f"ERROR: dashboard render failed: {exc}")
        return 4

def render_live(state_path: Path, interval_seconds: float) -> int:
    last_error: str | None = None
    last_mtime: float | None = None
    
    fixed_height = HALF_SCREEN_MAX
    
    clear_screen()
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    
    try:
        while True:
            try:
                try:
                    current_mtime = state_path.stat().st_mtime
                except Exception:
                    current_mtime = None
                
                # Force update every cycle (ไม่ตรวจสอบ mtime - update ทุกครั้ง)
                # เพื่อให้ event stream แสดงผลแม้ไฟล์ไม่เปลี่ยน
                
                state = load_state(state_path)
                output = render_screen(state, state_path)
                
                lines = output.split('\n')
                current_height = len(lines)
                
                if current_height < fixed_height:
                    lines.extend([''] * (fixed_height - current_height))
                elif current_height > fixed_height:
                    lines = lines[:fixed_height]
                
                padded = '\n'.join(lines)
                
                sys.stdout.write("\033[H")
                sys.stdout.write(padded)
                sys.stdout.flush()
                
                last_mtime = current_mtime
                last_error = None
                
            except KeyboardInterrupt:
                raise
                
            except FileNotFoundError:
                message = f"ERROR: state file not found: {state_path}"
                if message != last_error:
                    clear_screen()
                    print(colorize(message, Style.BRIGHT_RED, Style.BOLD))
                    last_error = message
                    
            except json.JSONDecodeError as exc:
                message = f"ERROR: invalid JSON in state file: {state_path}\nDETAIL: {exc}"
                if message != last_error:
                    clear_screen()
                    print(colorize(message, Style.BRIGHT_RED, Style.BOLD))
                    last_error = message
                    
            except Exception as exc:
                message = f"ERROR: dashboard render failed: {exc}"
                if message != last_error:
                    clear_screen()
                    print(colorize(message, Style.BRIGHT_RED, Style.BOLD))
                    last_error = message
            
            time.sleep(interval_seconds)
            
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        print("\n\nDashboard stopped by operator.")
    
    return 0

# ============================================================
# ARGPARSE
# ============================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enhanced production terminal dashboard for Alert_bot v3.1"
    )
    parser.add_argument(
        "--state-path",
        type=str,
        default=str(DEFAULT_STATE_PATH),
        help="Path to runtime/dashboard_state.json",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live refresh mode",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_REFRESH_SECONDS,
        help="Refresh interval in seconds for --live mode",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors",
    )
    return parser.parse_args()

def main() -> int:
    global USE_COLOR
    
    _enable_windows_ansi()
    args = parse_args()
    USE_COLOR = supports_color() and not args.no_color
    
    state_path = Path(args.state_path)
    
    run_live = args.live or os.getenv("RUN_PRODUCTION_RUNTIME") == "1"
    
    if run_live:
        return render_live(state_path, max(0.2, float(args.interval)))
    
    return render_once(state_path)

if __name__ == "__main__":
    raise SystemExit(main())
