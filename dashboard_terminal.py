# ============================================================
# Code Name : Production Terminal Dashboard (dashboard_terminal.py)
# File Path : C:\Data\Bot\Alert_bot\dashboard_terminal.py
# Run Cmd   : python dashboard_terminal.py
# Live Cmd  : python dashboard_terminal.py --live
# Version   : v2.3.0
# ============================================================
"""
dashboard_terminal.py
Version: v2.3.0

Purpose:
- Production-only fixed-layout terminal dashboard for Alert_bot
- Reads live state from runtime/dashboard_state.json
- Snapshot mode is default (single screen, no scrolling)
- Live mode is optional via --live
- Uses only the upper half of the terminal
- Dense 2-column layout with compact operator panels
- Block-style power bars similar to operator console dashboards
- No demo, no mock, no sample data
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
# CHANGELOG
# ============================================================
# v2.3.0
# - Constrained dashboard to upper half of terminal height
# - Rebuilt layout into compact 2-column half-screen operator console
# - Power bars now use dense block-style segments
# - Increased information density inside half-screen budget
# - Event stream limited to fit the half-screen area
#
# v2.2.0
# - Changed layout to fixed 2-column operator screen
#
# v2.1.0
# - Shortened power bars
#
# v2.0.0
# - Rebuilt dashboard into fixed-layout operator console style
# ============================================================


DEFAULT_STATE_PATH = Path("runtime") / "dashboard_state.json"
DEFAULT_REFRESH_SECONDS = 1.0
MIN_TERMINAL_WIDTH = 100
BAR_WIDTH = 18
HALF_SCREEN_MAX = 22
HALF_SCREEN_MIN = 16


# ============================================================
# TERMINAL STYLE
# ============================================================
class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"


USE_COLOR = True


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
def safe_get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = data
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


def truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def strip_ansi(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\033":
            i += 1
            while i < len(text) and text[i] != "m":
                i += 1
            if i < len(text):
                i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def visible_len(text: str) -> int:
    return len(strip_ansi(text))


def clip_visible(text: str, width: int) -> str:
    if width <= 0:
        return ""
    raw = strip_ansi(text)
    if len(raw) <= width:
        return text if visible_len(text) <= width else raw[:width]
    if width <= 3:
        return raw[:width]
    return raw[: width - 3] + "..."


def left_fit(text: str, width: int) -> str:
    clipped = clip_visible(text, width)
    extra = width - visible_len(clipped)
    return clipped + (" " * max(0, extra))


def join_parts(parts: list[str], width: int) -> str:
    raw = "  ".join(part for part in parts if part)
    return left_fit(raw, width)


def kv(key: str, value: str) -> str:
    return f"{key}={value}"


def clear_screen() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def file_mtime_utc(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return "-"


def load_state(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("dashboard_state.json root must be an object")
    return data


# ============================================================
# BAR STYLE
# ============================================================
def value_color(number: float | None) -> str:
    if number is None:
        return Style.WHITE
    if number > 0:
        return Style.GREEN
    if number < 0:
        return Style.RED
    return Style.YELLOW


def color_number(text: str, number: float | None) -> str:
    return colorize(text, Style.BOLD, value_color(number))


def block_bar(value: float | None, width: int = BAR_WIDTH, label: str | None = None) -> str:
    if value is None:
        return f"[{' ' * width}]  -"

    clamped = max(0.0, min(1.0, value))
    filled = int(round(clamped * width))
    filled = max(0, min(width, filled))

    if clamped >= 0.70:
        color = Style.GREEN
    elif clamped >= 0.35:
        color = Style.YELLOW
    else:
        color = Style.RED

    left = "■" * filled
    right = "·" * (width - filled)

    body = colorize(left, color, Style.BOLD) + right
    suffix = label if label is not None else f"{int(round(clamped * 100)):>3d}%"
    return f"[{body}]  {suffix}"


# ============================================================
# DERIVED DATA
# ============================================================
def collect_monitor_exit_fields(state: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}

    preferred_keys = [
        "timestamp",
        "ticket",
        "symbol",
        "side",
        "entry_price",
        "current_price",
        "sl",
        "tp",
        "pnl",
        "exit_decision",
        "exit_reason",
        "close_execution_enabled",
        "close_attempted",
        "close_result",
        "close_error",
        "dry_run",
        "action",
        "reason",
        "event",
        "count",
        "status",
        "vol",
        "cascade_last_event",
    ]

    candidate_blocks = [
        safe_get(state, "monitor", default=None),
        safe_get(state, "exit_logic", default=None),
        safe_get(state, "risk_exit", default=None),
        safe_get(state, "trade_monitor", default=None),
        safe_get(state, "execution", default=None),
        safe_get(state, "report", default=None),
        safe_get(state, "meta", default=None),
    ]

    for block in candidate_blocks:
        if isinstance(block, dict):
            for key in preferred_keys:
                if key in block and key not in result:
                    result[key] = block.get(key)

    for key in preferred_keys:
        if key in state and key not in result:
            result[key] = state.get(key)

    trade = safe_get(state, "trade_health", default={}) or {}
    for key in ["side", "entry_price", "current_price", "sl", "tp", "pnl"]:
        if key not in result and key in trade:
            result[key] = trade.get(key)

    header = safe_get(state, "header", default={}) or {}
    if "symbol" not in result and "symbol" in header:
        result["symbol"] = header.get("symbol")

    return result


def derive_state_age_sec(state: dict[str, Any], path: Path) -> float | None:
    last_update = safe_get(state, "header", "last_update", default=None)
    seconds = age_seconds_from_iso(last_update)
    if seconds is not None:
        return seconds
    return age_seconds_from_iso(file_mtime_utc(path))


def derive_mt5_latency_ms(state: dict[str, Any], path: Path) -> int | None:
    age = derive_state_age_sec(state, path)
    if age is None:
        return None
    return int(max(1.0, min(999.0, round(age * 1000.0))))


def derive_pos_count(state: dict[str, Any]) -> int | None:
    direct = safe_get(state, "header", "position_count", default=None)
    if direct is not None:
        return to_int(direct)

    side = fmt_value(safe_get(state, "trade_health", "side", default=None)).upper()
    position_state = fmt_value(safe_get(state, "header", "position_state", default=None)).upper()

    if side in {"BUY", "SELL"}:
        return 1
    if position_state in {"HEALTHY", "OPEN", "ACTIVE"}:
        return 1
    return 0


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
    elif bias == "NEUTRAL":
        score += 0.10
        seen = True

    triggers = entry.get("trigger_stack")
    if isinstance(triggers, list):
        if triggers:
            score += min(0.40, len(triggers) * 0.10)
        seen = True

    if not seen:
        return None
    return min(1.0, score)


def derive_spread_points(state: dict[str, Any]) -> float | None:
    for path in [("execution", "spread_pts"), ("monitor", "spread_pts")]:
        val = safe_get(state, *path, default=None)
        if val is not None:
            return to_float(val)
    if state.get("spread_pts") is not None:
        return to_float(state.get("spread_pts"))
    return None


def derive_data_flow_score(state: dict[str, Any]) -> float | None:
    header = safe_get(state, "header", default={}) or {}
    ms = safe_get(state, "market_structure", default={}) or {}
    entry = safe_get(state, "entry_lifecycle", default={}) or {}
    trade = safe_get(state, "trade_health", default={}) or {}

    checks = [
        fmt_value(header.get("last_update")) != "-",
        fmt_value(ms.get("bias")) != "-",
        fmt_value(entry.get("state")) != "-",
        fmt_value(trade.get("current_price")) != "-",
        fmt_value(trade.get("entry_price")) != "-",
    ]

    score = sum(0.20 for ok in checks if ok)
    return min(1.0, score) if score > 0 else None


def derive_market_energy_score(state: dict[str, Any]) -> float | None:
    ms = safe_get(state, "market_structure", default={}) or {}
    swing_high = to_float(ms.get("last_swing_high"))
    swing_low = to_float(ms.get("last_swing_low"))
    bias = fmt_value(ms.get("bias")).upper()

    if swing_high is None or swing_low is None:
        return None

    span = abs(swing_high - swing_low)
    score = min(1.0, span / 60.0)

    if bias in {"BULLISH", "BEARISH"}:
        score = min(1.0, score + 0.15)
    elif bias == "NEUTRAL":
        score = min(1.0, score + 0.05)

    return score


def derive_trend_power_score(state: dict[str, Any]) -> float | None:
    ms = safe_get(state, "market_structure", default={}) or {}
    entry = safe_get(state, "entry_lifecycle", default={}) or {}

    score = 0.0
    seen = False

    bias = fmt_value(ms.get("bias")).upper()
    if bias in {"BULLISH", "BEARISH"}:
        score += 0.45
        seen = True
    elif bias == "NEUTRAL":
        score += 0.15
        seen = True

    bos = fmt_value(ms.get("bos")).upper()
    if bos not in {"-", "", "NONE"}:
        score += 0.25
        seen = True

    choch = fmt_value(ms.get("choch")).upper()
    if choch not in {"-", "", "NONE"}:
        score += 0.15
        seen = True

    triggers = entry.get("trigger_stack")
    if isinstance(triggers, list):
        score += min(0.15, len(triggers) * 0.05)
        seen = True

    return min(1.0, score) if seen else None


def derive_signal_power_score(state: dict[str, Any]) -> float | None:
    entry = safe_get(state, "entry_lifecycle", default={}) or {}
    trade = safe_get(state, "trade_health", default={}) or {}

    score = 0.0
    seen = False

    state_name = fmt_value(entry.get("state")).upper()
    if state_name not in {"-", "", "IDLE", "NONE"}:
        score += 0.35
        seen = True

    triggers = entry.get("trigger_stack")
    if isinstance(triggers, list):
        score += min(0.40, len(triggers) * 0.10)
        seen = True

    side = fmt_value(trade.get("side")).upper()
    if side in {"BUY", "SELL"}:
        score += 0.25
        seen = True

    return min(1.0, score) if seen else None


def derive_ai_lock_score(state: dict[str, Any], monitor: dict[str, Any]) -> float | None:
    decision = fmt_value(monitor.get("exit_decision")).upper()
    result = fmt_value(monitor.get("close_result")).upper()
    dry_run = monitor.get("dry_run")

    score = 0.0
    seen = False

    if decision not in {"-", "", "NONE"}:
        score += 0.40
        seen = True
    if result not in {"-", "", "NONE"}:
        score += 0.35
        seen = True
    if dry_run is not None:
        score += 0.25
        seen = True

    return min(1.0, score) if seen else None


def derive_exit_risk_label(monitor: dict[str, Any]) -> str:
    decision = fmt_value(monitor.get("exit_decision")).upper()
    result = fmt_value(monitor.get("close_result")).upper()
    reason = fmt_value(monitor.get("exit_reason")).upper()

    if decision in {"FORCE_EXIT", "EXIT", "CLOSE", "CASCADE_EXIT"}:
        return "EXIT_TRIGGERED"
    if result in {"FAILED", "ERROR"}:
        return "EXECUTION_ERROR"
    if reason not in {"-", "", "NONE"}:
        return reason
    return "NONE"


def derive_feed_status(state: dict[str, Any]) -> str:
    trade = safe_get(state, "trade_health", default={}) or {}
    return "MT5" if to_float(trade.get("current_price")) is not None else "-"


def derive_ticks_status(state: dict[str, Any]) -> str:
    trade = safe_get(state, "trade_health", default={}) or {}
    return "OK" if to_float(trade.get("current_price")) is not None else "-"


def derive_bid_ask(state: dict[str, Any]) -> tuple[float | None, float | None]:
    trade = safe_get(state, "trade_health", default={}) or {}
    current_price = to_float(trade.get("current_price"))
    spread_pts = derive_spread_points(state)

    if current_price is None or spread_pts is None:
        return None, None

    half = spread_pts / 200.0
    return current_price - half, current_price + half


def derive_market_condition(state: dict[str, Any]) -> dict[str, str]:
    ms = safe_get(state, "market_structure", default={}) or {}
    entry = safe_get(state, "entry_lifecycle", default={}) or {}
    trade = safe_get(state, "trade_health", default={}) or {}

    bias = fmt_value(ms.get("bias")).upper()
    zone_status = fmt_value(ms.get("zone_status")).upper()
    bos = fmt_value(ms.get("bos")).upper()
    choch = fmt_value(ms.get("choch")).upper()
    entry_state = fmt_value(entry.get("state")).upper()
    side = fmt_value(trade.get("side")).upper()

    regime = "RANGE"
    if bias in {"BULLISH", "BEARISH"}:
        regime = "TREND"
    if bos not in {"-", "", "NONE"} or choch not in {"-", "", "NONE"}:
        regime = "STRUCTURE_SHIFT"

    pressure = "LOW"
    energy = derive_market_energy_score(state)
    if energy is not None:
        if energy >= 0.70:
            pressure = "HIGH"
        elif energy >= 0.35:
            pressure = "MEDIUM"

    execution_mode = "MONITOR_ONLY"
    if entry_state not in {"-", "", "IDLE", "NONE"}:
        execution_mode = "ENTRY_ACTIVE"
    if side in {"BUY", "SELL"}:
        execution_mode = "POSITION_OPEN"

    return {
        "regime": regime,
        "bias": bias if bias != "-" else "UNKNOWN",
        "pressure": pressure,
        "zone_status": zone_status if zone_status != "-" else "UNKNOWN",
        "execution_mode": execution_mode,
    }


def build_event_lines(state: dict[str, Any], monitor: dict[str, Any], max_lines: int) -> list[str]:
    header = safe_get(state, "header", default={}) or {}
    entry = safe_get(state, "entry_lifecycle", default={}) or {}
    ms = safe_get(state, "market_structure", default={}) or {}
    trade = safe_get(state, "trade_health", default={}) or {}

    lines: list[str] = []

    ts = fmt_ts_iso(monitor.get("timestamp"))
    ticket = fmt_value(monitor.get("ticket"))
    exit_decision = fmt_value(monitor.get("exit_decision"))
    exit_reason = fmt_value(monitor.get("exit_reason"))
    close_result = fmt_value(monitor.get("close_result"))
    close_error = fmt_value(monitor.get("close_error"))
    cascade_last_event = fmt_value(monitor.get("cascade_last_event"))

    if exit_decision != "-":
        lines.append(f"[monitor] {ts} decision={exit_decision} ticket={ticket} reason={exit_reason}")
    if close_result != "-":
        lines.append(f"[exec]    {ts} close_result={close_result} ticket={ticket} error={close_error}")
    if cascade_last_event != "-":
        lines.append(f"[cascade] {ts} event={cascade_last_event}")

    lines.append(
        f"[state]   {fmt_ts_iso(header.get('last_update'))} "
        f"mode={fmt_value(header.get('mode'))} pos={fmt_value(header.get('position_state'))}"
    )
    lines.append(
        f"[entry]   state={fmt_value(entry.get('state'))} triggers={fmt_value(entry.get('trigger_stack'))}"
    )
    lines.append(
        f"[market]  bias={fmt_value(ms.get('bias'))} choch={fmt_value(ms.get('choch'))} bos={fmt_value(ms.get('bos'))}"
    )
    lines.append(
        f"[health]  side={fmt_value(trade.get('side'))} entry={fmt_float(trade.get('entry_price'))} "
        f"current={fmt_float(trade.get('current_price'))} pnl={fmt_signed(trade.get('pnl'))}"
    )

    return lines[:max_lines]


# ============================================================
# BOX / LAYOUT RENDER
# ============================================================
def make_box(title: str, width: int, body_lines: list[str], body_height: int) -> list[str]:
    inner_width = width - 2

    if len(body_lines) < body_height:
        body_lines = body_lines + [""] * (body_height - len(body_lines))
    else:
        body_lines = body_lines[:body_height]

    clean_title = f" {title} "
    top = "┌" + clean_title + ("─" * max(0, width - len(clean_title) - 2)) + "┐"
    bottom = "└" + ("─" * (width - 2)) + "┘"

    out = [top]
    for line in body_lines:
        out.append("│" + left_fit(line, inner_width) + "│")
    out.append(bottom)
    return out


def hstack(left_lines: list[str], right_lines: list[str], total_width: int, gap: int = 2) -> list[str]:
    left_width = visible_len(left_lines[0]) if left_lines else (total_width - gap) // 2
    right_width = total_width - left_width - gap

    rows = max(len(left_lines), len(right_lines))
    out: list[str] = []

    for i in range(rows):
        left = left_lines[i] if i < len(left_lines) else " " * left_width
        right = right_lines[i] if i < len(right_lines) else " " * right_width
        out.append(left_fit(left, left_width) + (" " * gap) + left_fit(right, right_width))
    return out


def render_header_box(state: dict[str, Any], path: Path, width: int) -> list[str]:
    inner = width - 2
    header = safe_get(state, "header", default={}) or {}
    monitor = collect_monitor_exit_fields(state)

    line1 = join_parts(
        [
            kv("SYSTEM", fmt_value(header.get("mode"))),
            kv("SYMBOL", fmt_value(header.get("symbol"))),
            kv("POS", fmt_value(derive_pos_count(state))),
            kv("AGE", fmt_float(derive_state_age_sec(state, path), 1)),
            kv("MT5_LATENCY_MS", fmt_value(derive_mt5_latency_ms(state, path))),
            kv("EXIT_RISK", derive_exit_risk_label(monitor)),
        ],
        inner,
    )

    line2 = join_parts(
        [
            kv("UTC", fmt_ts_iso(header.get("last_update"))),
            kv("SCAN", block_bar(derive_scan_score(state), BAR_WIDTH)),
        ],
        inner,
    )

    return make_box("HIM - CONTROL CORE", width, [line1, line2], body_height=2)


def render_data_ingest_box(state: dict[str, Any], width: int) -> list[str]:
    inner = width - 2
    spread_pts = derive_spread_points(state)
    spread_score = min(1.0, spread_pts / 250.0) if spread_pts is not None else None

    body = [
        join_parts(
            [
                kv("feed", derive_feed_status(state)),
                kv("ticks", derive_ticks_status(state)),
                kv("spread_pts", "-" if spread_pts is None else str(int(round(spread_pts)))),
            ],
            inner,
        ),
        join_parts([kv("DATA_FLOW", block_bar(derive_data_flow_score(state), BAR_WIDTH))], inner),
        join_parts([kv("MARKET_ENERGY", block_bar(derive_market_energy_score(state), BAR_WIDTH))], inner),
        join_parts([kv("SPREAD_METER", block_bar(spread_score, BAR_WIDTH, "-" if spread_pts is None else f"{int(round(spread_pts))} pts"))], inner),
    ]
    return make_box("DATA INGEST", width, body, body_height=4)


def render_execution_guard_box(state: dict[str, Any], monitor: dict[str, Any], width: int) -> list[str]:
    inner = width - 2

    action = fmt_value(monitor.get("exit_decision"))
    if action == "-":
        action = fmt_value(monitor.get("action"))

    reason = fmt_value(monitor.get("exit_reason"))
    if reason == "-":
        reason = fmt_value(monitor.get("reason"))

    body = [
        join_parts([kv("action", action), kv("reason", reason)], inner),
        join_parts([kv("TREND_POWER", block_bar(derive_trend_power_score(state), BAR_WIDTH))], inner),
        join_parts([kv("SIGNAL_POWER", block_bar(derive_signal_power_score(state), BAR_WIDTH))], inner),
        join_parts(
            [
                kv("AI_LOCK", block_bar(derive_ai_lock_score(state, monitor), BAR_WIDTH)),
                kv("exit_risk", derive_exit_risk_label(monitor)),
            ],
            inner,
        ),
    ]
    return make_box("EXECUTION GUARD", width, body, body_height=4)


def render_position_monitor_box(state: dict[str, Any], monitor: dict[str, Any], width: int) -> list[str]:
    inner = width - 2
    trade = safe_get(state, "trade_health", default={}) or {}
    header = safe_get(state, "header", default={}) or {}

    body = [
        join_parts(
            [
                kv("status", fmt_value(header.get("position_state"))),
                kv("count", fmt_value(derive_pos_count(state))),
                kv("ticket", fmt_value(monitor.get("ticket"))),
                kv("side", fmt_value(trade.get("side"))),
            ],
            inner,
        ),
        join_parts(
            [
                kv("open", fmt_float(trade.get("entry_price"))),
                kv("current", fmt_float(trade.get("current_price"))),
                kv("sl", fmt_float(trade.get("sl"))),
                kv("tp", fmt_float(trade.get("tp"))),
            ],
            inner,
        ),
        join_parts(
            [
                kv("close_result", fmt_value(monitor.get("close_result"))),
                kv("dry_run", fmt_value(monitor.get("dry_run"))),
                kv("event", fmt_value(monitor.get("cascade_last_event"))),
            ],
            inner,
        ),
    ]
    return make_box("POSITION MONITOR", width, body, body_height=3)


def render_live_analytics_box(state: dict[str, Any], width: int) -> list[str]:
    inner = width - 2
    trade = safe_get(state, "trade_health", default={}) or {}
    bid, ask = derive_bid_ask(state)
    pnl = to_float(trade.get("pnl"))
    spread_pts = derive_spread_points(state)
    spread_score = min(1.0, spread_pts / 250.0) if spread_pts is not None else None

    body = [
        join_parts(
            [
                kv("price", fmt_float(trade.get("current_price"))),
                kv("bid", fmt_float(bid)),
                kv("ask", fmt_float(ask)),
            ],
            inner,
        ),
        join_parts(
            [
                kv("entry", fmt_float(trade.get("entry_price"))),
                kv("sl", fmt_float(trade.get("sl"))),
                kv("tp", fmt_float(trade.get("tp"))),
                kv("pnl", color_number(fmt_signed(pnl), pnl)),
            ],
            inner,
        ),
        join_parts([kv("SPREAD_METER", block_bar(spread_score, BAR_WIDTH, "-" if spread_pts is None else f"{int(round(spread_pts))} pts"))], inner),
    ]
    return make_box("LIVE ANALYTICS", width, body, body_height=3)


def render_market_condition_box(state: dict[str, Any], width: int) -> list[str]:
    inner = width - 2
    market = derive_market_condition(state)
    ms = safe_get(state, "market_structure", default={}) or {}
    entry = safe_get(state, "entry_lifecycle", default={}) or {}

    body = [
        join_parts(
            [
                kv("regime", market["regime"]),
                kv("bias", market["bias"]),
                kv("pressure", market["pressure"]),
                kv("zone", market["zone_status"]),
            ],
            inner,
        ),
        join_parts(
            [
                kv("exec_mode", market["execution_mode"]),
                kv("bos", fmt_value(ms.get("bos"))),
                kv("choch", fmt_value(ms.get("choch"))),
                kv("entry_state", fmt_value(entry.get("state"))),
            ],
            inner,
        ),
    ]
    return make_box("MARKET VIEW", width, body, body_height=2)


def render_status_summary_box(state: dict[str, Any], width: int) -> list[str]:
    inner = width - 2
    header = safe_get(state, "header", default={}) or {}
    trade = safe_get(state, "trade_health", default={}) or {}
    ms = safe_get(state, "market_structure", default={}) or {}

    body = [
        join_parts(
            [
                kv("mode", fmt_value(header.get("mode"))),
                kv("pos", fmt_value(header.get("position_state"))),
                kv("side", fmt_value(trade.get("side"))),
            ],
            inner,
        ),
        join_parts(
            [
                kv("bias", fmt_value(ms.get("bias"))),
                kv("swing_high", fmt_float(ms.get("last_swing_high"))),
                kv("swing_low", fmt_float(ms.get("last_swing_low"))),
            ],
            inner,
        ),
    ]
    return make_box("STATUS SUMMARY", width, body, body_height=2)


def render_event_stream_box(state: dict[str, Any], monitor: dict[str, Any], width: int, body_height: int) -> list[str]:
    lines = build_event_lines(state, monitor, body_height)
    return make_box("EVENT STREAM", width, lines, body_height=body_height)


def render_footer_line(state: dict[str, Any], path: Path, width: int) -> str:
    header = safe_get(state, "header", default={}) or {}
    ms = safe_get(state, "market_structure", default={}) or {}
    entry = safe_get(state, "entry_lifecycle", default={}) or {}
    trade = safe_get(state, "trade_health", default={}) or {}
    monitor = collect_monitor_exit_fields(state)

    return join_parts(
        [
            kv("mode", fmt_value(header.get("mode"))),
            kv("symbol", fmt_value(header.get("symbol"))),
            kv("bias", fmt_value(ms.get("bias"))),
            kv("entry_state", fmt_value(entry.get("state"))),
            kv("trade_side", fmt_value(trade.get("side"))),
            kv("position", fmt_value(header.get("position_state"))),
            kv("exit_decision", fmt_value(monitor.get("exit_decision"))),
            kv("mtime", file_mtime_utc(path)),
        ],
        width,
    )


# ============================================================
# FULL SCREEN RENDER
# ============================================================
def render_screen(state: dict[str, Any], path: Path) -> str:
    term_width, term_height = terminal_size()
    width = max(MIN_TERMINAL_WIDTH, term_width)

    target_height = max(HALF_SCREEN_MIN, min(HALF_SCREEN_MAX, max(HALF_SCREEN_MIN, term_height // 2)))

    gap = 2
    left_width = (width - gap) // 2
    right_width = width - gap - left_width

    monitor = collect_monitor_exit_fields(state)

    output: list[str] = []

    header_box = render_header_box(state, path, width)
    output.extend(header_box)

    left_top = render_data_ingest_box(state, left_width)
    right_top = render_live_analytics_box(state, right_width)
    output.extend(hstack(left_top, right_top, width, gap=gap))

    left_mid = render_execution_guard_box(state, monitor, left_width)
    right_mid = render_market_condition_box(state, right_width)
    output.extend(hstack(left_mid, right_mid, width, gap=gap))

    left_low = render_position_monitor_box(state, monitor, left_width)
    right_low = render_status_summary_box(state, right_width)
    output.extend(hstack(left_low, right_low, width, gap=gap))

    used_before_event = len(output) + 1
    remaining_lines = max(6, target_height - used_before_event)
    event_body_height = max(4, remaining_lines - 2)

    event_box = render_event_stream_box(state, monitor, width, body_height=event_body_height)
    output.extend(event_box)

    output.append(left_fit(render_footer_line(state, path, width), width))
    return "\n".join(output)


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

    while True:
        try:
            state = load_state(state_path)
            output = render_screen(state, state_path)
            clear_screen()
            print(output)
            last_error = None
        except KeyboardInterrupt:
            print("\nDashboard stopped by operator.")
            return 0
        except FileNotFoundError:
            message = f"ERROR: state file not found: {state_path}"
            if message != last_error:
                clear_screen()
                print(message)
                last_error = message
        except json.JSONDecodeError as exc:
            message = f"ERROR: invalid JSON in state file: {state_path}\nDETAIL: {exc}"
            if message != last_error:
                clear_screen()
                print(message)
                last_error = message
        except Exception as exc:
            message = f"ERROR: dashboard render failed: {exc}"
            if message != last_error:
                clear_screen()
                print(message)
                last_error = message

        time.sleep(interval_seconds)


# ============================================================
# ARGPARSE
# ============================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Production half-screen fixed-layout terminal dashboard for Alert_bot"
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

    if args.live:
        return render_live(state_path, max(0.2, float(args.interval)))

    return render_once(state_path)


if __name__ == "__main__":
    raise SystemExit(main())