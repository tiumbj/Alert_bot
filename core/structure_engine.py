# ============================================================
# Code Name: Structure Engine (Entry + Trade Health)
# File Path: core/structure_engine.py
# Run Command: python -m py_compile core/structure_engine.py
# Version: v1.0.0
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd


PivotSide = Literal["HIGH", "LOW"]


PIVOT_LEFT = 2
PIVOT_RIGHT = 2
REACTION_WINDOW_BARS = 3
BREAK_METHOD = "close_only"


@dataclass(frozen=True)
class Pivot:
    kind: PivotSide
    index: int
    price: float


def _has_columns(df: pd.DataFrame) -> bool:
    required = {"Open", "High", "Low", "Close"}
    return required.issubset(set(df.columns))


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def detect_swings(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or len(df) == 0 or not _has_columns(df):
        return {
            "pivot_left": PIVOT_LEFT,
            "pivot_right": PIVOT_RIGHT,
            "swing_highs": [],
            "swing_lows": [],
            "last_swing_high": None,
            "last_swing_low": None,
        }

    highs = df["High"].reset_index(drop=True)
    lows = df["Low"].reset_index(drop=True)

    swing_highs: list[dict[str, Any]] = []
    swing_lows: list[dict[str, Any]] = []

    start = PIVOT_LEFT
    end = len(df) - PIVOT_RIGHT
    for i in range(start, end):
        left_high = highs.iloc[i - PIVOT_LEFT : i]
        right_high = highs.iloc[i + 1 : i + 1 + PIVOT_RIGHT]
        center_high = highs.iloc[i]

        left_low = lows.iloc[i - PIVOT_LEFT : i]
        right_low = lows.iloc[i + 1 : i + 1 + PIVOT_RIGHT]
        center_low = lows.iloc[i]

        is_swing_high = bool((center_high > left_high.max()) and (center_high >= right_high.max()))
        is_swing_low = bool((center_low < left_low.min()) and (center_low <= right_low.min()))

        if is_swing_high:
            swing_highs.append(
                {
                    "index": int(i),
                    "price": float(center_high),
                    "time": df["time"].iloc[i].isoformat() if "time" in df.columns else None,
                }
            )
        if is_swing_low:
            swing_lows.append(
                {
                    "index": int(i),
                    "price": float(center_low),
                    "time": df["time"].iloc[i].isoformat() if "time" in df.columns else None,
                }
            )

    last_high = swing_highs[-1] if swing_highs else None
    last_low = swing_lows[-1] if swing_lows else None

    return {
        "pivot_left": PIVOT_LEFT,
        "pivot_right": PIVOT_RIGHT,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "last_swing_high": last_high,
        "last_swing_low": last_low,
    }


def detect_choch(df: pd.DataFrame, swings: dict[str, Any]) -> dict[str, Any]:
    if df is None or len(df) < 5 or not _has_columns(df):
        return {
            "state": "NONE",
            "direction": "NONE",
            "break_level": None,
            "break_index": None,
            "break_time": None,
            "method": BREAK_METHOD,
        }

    close = df["Close"].reset_index(drop=True)
    last_close = _safe_float(close.iloc[-1])
    if last_close is None:
        return {
            "state": "NONE",
            "direction": "NONE",
            "break_level": None,
            "break_index": None,
            "break_time": None,
            "method": BREAK_METHOD,
        }

    last_swing_high = swings.get("last_swing_high")
    last_swing_low = swings.get("last_swing_low")

    bullish_break = False
    bearish_break = False
    bullish_level = None
    bearish_level = None

    if isinstance(last_swing_high, dict):
        bullish_level = _safe_float(last_swing_high.get("price"))
        if bullish_level is not None and last_close > bullish_level:
            bullish_break = True

    if isinstance(last_swing_low, dict):
        bearish_level = _safe_float(last_swing_low.get("price"))
        if bearish_level is not None and last_close < bearish_level:
            bearish_break = True

    if bullish_break and not bearish_break:
        return {
            "state": "BULLISH_CONFIRMED",
            "direction": "BULLISH",
            "break_level": bullish_level,
            "break_index": int(len(df) - 1),
            "break_time": df["time"].iloc[-1].isoformat() if "time" in df.columns else None,
            "method": BREAK_METHOD,
        }

    if bearish_break and not bullish_break:
        return {
            "state": "BEARISH_CONFIRMED",
            "direction": "BEARISH",
            "break_level": bearish_level,
            "break_index": int(len(df) - 1),
            "break_time": df["time"].iloc[-1].isoformat() if "time" in df.columns else None,
            "method": BREAK_METHOD,
        }

    return {
        "state": "NONE",
        "direction": "NONE",
        "break_level": None,
        "break_index": None,
        "break_time": None,
        "method": BREAK_METHOD,
    }


def _last_pivot_after(swings: dict[str, Any], kind: PivotSide, after_index: int) -> Pivot | None:
    key = "swing_highs" if kind == "HIGH" else "swing_lows"
    pivots = swings.get(key) or []
    if not isinstance(pivots, list):
        return None
    candidates: list[Pivot] = []
    for p in pivots:
        if not isinstance(p, dict):
            continue
        idx = p.get("index")
        price = p.get("price")
        idx_i = int(idx) if isinstance(idx, int) else None
        price_f = _safe_float(price)
        if idx_i is None or price_f is None:
            continue
        if idx_i > after_index:
            candidates.append(Pivot(kind=kind, index=idx_i, price=price_f))
    if not candidates:
        return None
    return candidates[-1]


def detect_bos(df: pd.DataFrame, swings: dict[str, Any], choch_state: dict[str, Any]) -> dict[str, Any]:
    if df is None or len(df) < 10 or not _has_columns(df):
        return {
            "state": "NONE",
            "direction": "NONE",
            "break_level": None,
            "break_index": None,
            "impulse_start_index": None,
            "method": BREAK_METHOD,
        }

    choch_dir = choch_state.get("direction")
    choch_break_idx = choch_state.get("break_index")
    if choch_dir not in {"BULLISH", "BEARISH"} or choch_break_idx is None:
        return {
            "state": "NONE",
            "direction": "NONE",
            "break_level": None,
            "break_index": None,
            "impulse_start_index": None,
            "method": BREAK_METHOD,
        }

    close = df["Close"].reset_index(drop=True)
    last_close = _safe_float(close.iloc[-1])
    if last_close is None:
        return {
            "state": "NONE",
            "direction": "NONE",
            "break_level": None,
            "break_index": None,
            "impulse_start_index": None,
            "method": BREAK_METHOD,
        }

    last_swing_high = swings.get("last_swing_high")
    last_swing_low = swings.get("last_swing_low")
    last_high_level = _safe_float(last_swing_high.get("price")) if isinstance(last_swing_high, dict) else None
    last_low_level = _safe_float(last_swing_low.get("price")) if isinstance(last_swing_low, dict) else None

    if choch_dir == "BULLISH" and last_high_level is not None:
        pullback_low = _last_pivot_after(swings, "LOW", int(choch_break_idx))
        if pullback_low is None:
            return {
                "state": "NONE",
                "direction": "BULLISH",
                "break_level": last_high_level,
                "break_index": None,
                "impulse_start_index": None,
                "method": BREAK_METHOD,
            }
        if last_close > last_high_level:
            return {
                "state": "BULLISH_CONFIRMED",
                "direction": "BULLISH",
                "break_level": last_high_level,
                "break_index": int(len(df) - 1),
                "impulse_start_index": int(pullback_low.index),
                "method": BREAK_METHOD,
            }

    if choch_dir == "BEARISH" and last_low_level is not None:
        pullback_high = _last_pivot_after(swings, "HIGH", int(choch_break_idx))
        if pullback_high is None:
            return {
                "state": "NONE",
                "direction": "BEARISH",
                "break_level": last_low_level,
                "break_index": None,
                "impulse_start_index": None,
                "method": BREAK_METHOD,
            }
        if last_close < last_low_level:
            return {
                "state": "BEARISH_CONFIRMED",
                "direction": "BEARISH",
                "break_level": last_low_level,
                "break_index": int(len(df) - 1),
                "impulse_start_index": int(pullback_high.index),
                "method": BREAK_METHOD,
            }

    return {
        "state": "NONE",
        "direction": choch_dir if choch_dir in {"BULLISH", "BEARISH"} else "NONE",
        "break_level": None,
        "break_index": None,
        "impulse_start_index": None,
        "method": BREAK_METHOD,
    }


def map_order_block(df: pd.DataFrame, bos_state: dict[str, Any]) -> dict[str, Any]:
    if df is None or len(df) < 10 or not _has_columns(df):
        return {
            "state": "NONE",
            "direction": "NONE",
            "ob_index": None,
            "ob_low": None,
            "ob_high": None,
            "invalidation": None,
        }

    bos_dir = bos_state.get("direction")
    bos_confirmed = bos_state.get("state") in {"BULLISH_CONFIRMED", "BEARISH_CONFIRMED"}
    bos_break_idx = bos_state.get("break_index")
    impulse_start = bos_state.get("impulse_start_index")

    if not bos_confirmed or bos_dir not in {"BULLISH", "BEARISH"}:
        return {
            "state": "NONE",
            "direction": bos_dir if bos_dir in {"BULLISH", "BEARISH"} else "NONE",
            "ob_index": None,
            "ob_low": None,
            "ob_high": None,
            "invalidation": None,
        }

    if not isinstance(bos_break_idx, int) or not isinstance(impulse_start, int):
        return {
            "state": "NONE",
            "direction": bos_dir,
            "ob_index": None,
            "ob_low": None,
            "ob_high": None,
            "invalidation": None,
        }

    start = max(0, impulse_start)
    end = min(len(df) - 1, bos_break_idx)
    if end - start < 2:
        return {
            "state": "NONE",
            "direction": bos_dir,
            "ob_index": None,
            "ob_low": None,
            "ob_high": None,
            "invalidation": None,
        }

    opens = df["Open"].reset_index(drop=True)
    closes = df["Close"].reset_index(drop=True)
    highs = df["High"].reset_index(drop=True)
    lows = df["Low"].reset_index(drop=True)

    ob_index: int | None = None

    if bos_dir == "BULLISH":
        for i in range(end - 1, start - 1, -1):
            if _safe_float(closes.iloc[i]) is None or _safe_float(opens.iloc[i]) is None:
                continue
            if float(closes.iloc[i]) < float(opens.iloc[i]):
                ob_index = int(i)
                break
    else:
        for i in range(end - 1, start - 1, -1):
            if _safe_float(closes.iloc[i]) is None or _safe_float(opens.iloc[i]) is None:
                continue
            if float(closes.iloc[i]) > float(opens.iloc[i]):
                ob_index = int(i)
                break

    if ob_index is None:
        return {
            "state": "NONE",
            "direction": bos_dir,
            "ob_index": None,
            "ob_low": None,
            "ob_high": None,
            "invalidation": None,
        }

    ob_low = _safe_float(lows.iloc[ob_index])
    ob_high = _safe_float(highs.iloc[ob_index])
    if ob_low is None or ob_high is None:
        return {
            "state": "NONE",
            "direction": bos_dir,
            "ob_index": None,
            "ob_low": None,
            "ob_high": None,
            "invalidation": None,
        }

    invalidation = ob_low if bos_dir == "BULLISH" else ob_high
    return {
        "state": "MAPPED",
        "direction": bos_dir,
        "ob_index": int(ob_index),
        "ob_low": float(ob_low),
        "ob_high": float(ob_high),
        "invalidation": float(invalidation) if invalidation is not None else None,
    }


def evaluate_reaction(df: pd.DataFrame, ob_state: dict[str, Any]) -> dict[str, Any]:
    if df is None or len(df) < 10 or not _has_columns(df):
        return {
            "state": "NONE",
            "direction": "NONE",
            "touched": False,
            "touch_index": None,
            "touch_time": None,
            "valid": False,
            "expires_at_index": None,
            "window_bars": REACTION_WINDOW_BARS,
        }

    if ob_state.get("state") != "MAPPED":
        return {
            "state": "NONE",
            "direction": ob_state.get("direction") or "NONE",
            "touched": False,
            "touch_index": None,
            "touch_time": None,
            "valid": False,
            "expires_at_index": None,
            "window_bars": REACTION_WINDOW_BARS,
        }

    direction = ob_state.get("direction")
    ob_low = _safe_float(ob_state.get("ob_low"))
    ob_high = _safe_float(ob_state.get("ob_high"))
    if direction not in {"BULLISH", "BEARISH"} or ob_low is None or ob_high is None:
        return {
            "state": "NONE",
            "direction": "NONE",
            "touched": False,
            "touch_index": None,
            "touch_time": None,
            "valid": False,
            "expires_at_index": None,
            "window_bars": REACTION_WINDOW_BARS,
        }

    highs = df["High"].reset_index(drop=True)
    lows = df["Low"].reset_index(drop=True)
    closes = df["Close"].reset_index(drop=True)

    last_index = len(df) - 1
    lookback_start = max(0, last_index - (REACTION_WINDOW_BARS + 5))

    touch_index: int | None = None
    for i in range(last_index, lookback_start - 1, -1):
        hi = _safe_float(highs.iloc[i])
        lo = _safe_float(lows.iloc[i])
        if hi is None or lo is None:
            continue
        touched = (lo <= ob_high) and (hi >= ob_low)
        if touched:
            touch_index = int(i)
            break

    if touch_index is None:
        return {
            "state": "WAITING",
            "direction": direction,
            "touched": False,
            "touch_index": None,
            "touch_time": None,
            "valid": False,
            "expires_at_index": None,
            "window_bars": REACTION_WINDOW_BARS,
        }

    expires_at = touch_index + REACTION_WINDOW_BARS
    if last_index > expires_at:
        return {
            "state": "EXPIRED",
            "direction": direction,
            "touched": True,
            "touch_index": touch_index,
            "touch_time": df["time"].iloc[touch_index].isoformat() if "time" in df.columns else None,
            "valid": False,
            "expires_at_index": int(expires_at),
            "window_bars": REACTION_WINDOW_BARS,
        }

    valid = False
    if direction == "BULLISH":
        for i in range(touch_index, min(last_index, expires_at) + 1):
            c = _safe_float(closes.iloc[i])
            if c is not None and c > ob_high:
                valid = True
                break
    else:
        for i in range(touch_index, min(last_index, expires_at) + 1):
            c = _safe_float(closes.iloc[i])
            if c is not None and c < ob_low:
                valid = True
                break

    return {
        "state": "VALID" if valid else "WATCHING",
        "direction": direction,
        "touched": True,
        "touch_index": touch_index,
        "touch_time": df["time"].iloc[touch_index].isoformat() if "time" in df.columns else None,
        "valid": bool(valid),
        "expires_at_index": int(expires_at),
        "window_bars": REACTION_WINDOW_BARS,
    }


def _detect_invalidation(df: pd.DataFrame, ob_state: dict[str, Any]) -> dict[str, Any]:
    if df is None or len(df) == 0 or not _has_columns(df):
        return {"level": None, "broken": False, "method": BREAK_METHOD}

    direction = ob_state.get("direction")
    ob_low = _safe_float(ob_state.get("ob_low"))
    ob_high = _safe_float(ob_state.get("ob_high"))
    if direction not in {"BULLISH", "BEARISH"} or ob_low is None or ob_high is None:
        return {"level": None, "broken": False, "method": BREAK_METHOD}

    last_close = _safe_float(df["Close"].iloc[-1])
    if last_close is None:
        return {"level": None, "broken": False, "method": BREAK_METHOD}

    if direction == "BULLISH":
        level = float(ob_low)
        return {"level": level, "broken": bool(last_close < level), "method": BREAK_METHOD}
    level = float(ob_high)
    return {"level": level, "broken": bool(last_close > level), "method": BREAK_METHOD}


def evaluate_entry_state(df: pd.DataFrame) -> dict[str, Any]:
    swings = detect_swings(df)
    choch = detect_choch(df, swings)
    bos = detect_bos(df, swings, choch)
    order_block = map_order_block(df, bos)
    reaction = evaluate_reaction(df, order_block)
    invalidation = _detect_invalidation(df, order_block)

    trigger_stack: list[str] = []
    entry_state = "IDLE"
    bias = "NEUTRAL"

    if choch.get("state") == "BULLISH_CONFIRMED":
        bias = "BULLISH"
        trigger_stack.append("CHOCH")
        entry_state = "EARLY"
    elif choch.get("state") == "BEARISH_CONFIRMED":
        bias = "BEARISH"
        trigger_stack.append("CHOCH")
        entry_state = "EARLY"

    if bos.get("state") in {"BULLISH_CONFIRMED", "BEARISH_CONFIRMED"}:
        trigger_stack.append("BOS")
        entry_state = "CONFIRMED"

    if order_block.get("state") == "MAPPED":
        trigger_stack.append("OB")

    if order_block.get("state") == "MAPPED" and reaction.get("state") == "WAITING":
        entry_state = "CONFIRMED"
    elif order_block.get("state") == "MAPPED" and reaction.get("touched") and not reaction.get("valid"):
        entry_state = "ZONE_READY" if reaction.get("state") != "EXPIRED" else "INVALIDATED"
    elif order_block.get("state") == "MAPPED" and reaction.get("valid"):
        trigger_stack.append("REACTION")
        entry_state = "ACTIONABLE"

    if invalidation.get("broken") and order_block.get("state") == "MAPPED" and entry_state != "ACTIONABLE":
        entry_state = "INVALIDATED"

    return {
        "entry_state": entry_state,
        "bias": bias,
        "swings": swings,
        "choch": choch,
        "bos": bos,
        "order_block": order_block,
        "reaction": reaction,
        "invalidation": invalidation,
        "trigger_stack": trigger_stack,
        "trade_health": {
            "side": "",
            "entry_price": None,
            "current_price": None,
            "pnl_points": None,
            "health_score": None,
            "trade_state": "IDLE",
            "exit_risk": "LOW",
            "next_action": "WAIT",
        },
        "trade_state": "IDLE",
        "zone_reaction": "NONE",
        "continuation": "NONE",
        "premise": "UNKNOWN",
        "opposite_shift": "NONE",
        "ob_integrity": "UNKNOWN",
        "exit_state": "NONE",
        "exit_reason": "",
        "urgency": "LOW",
    }


def evaluate_trade_structure_health(df: pd.DataFrame, active_trade: dict[str, Any]) -> dict[str, Any]:
    if df is None or len(df) == 0 or not _has_columns(df) or not isinstance(active_trade, dict):
        return {
            "trade_state": "ENTERED",
            "trade_health": {
                "side": active_trade.get("side") if isinstance(active_trade, dict) else "",
                "entry_price": active_trade.get("entry_price") if isinstance(active_trade, dict) else None,
                "current_price": None,
                "pnl_points": None,
                "health_score": 1.0,
                "trade_state": "ENTERED",
                "exit_risk": "LOW",
                "next_action": "WAIT",
            },
            "structure_monitor": {
                "zone_reaction": "NONE",
                "continuation": "NONE",
                "premise": "UNKNOWN",
                "opposite_shift": "NONE",
                "ob_integrity": "UNKNOWN",
            },
            "exit_engine": {
                "exit_state": "NONE",
                "primary_reason": "",
                "invalidation": active_trade.get("invalidation") if isinstance(active_trade, dict) else None,
                "opposite_structure": "NONE",
                "urgency": "LOW",
            },
        }

    side = (active_trade.get("side") or "").upper()
    entry_price = _safe_float(active_trade.get("entry_price"))
    invalidation_level = _safe_float(active_trade.get("invalidation"))
    ob_low = _safe_float(active_trade.get("active_ob_low"))
    ob_high = _safe_float(active_trade.get("active_ob_high"))

    current_price = _safe_float(df["Close"].iloc[-1])
    if current_price is None:
        current_price = _safe_float(df["Close"].iloc[-1])

    pnl_points = None
    if current_price is not None and entry_price is not None:
        pnl_points = (current_price - entry_price) if side == "BUY" else (entry_price - current_price)

    swings = detect_swings(df)
    choch = detect_choch(df, swings)
    bos = detect_bos(df, swings, choch)

    opposite_shift = "NONE"
    opposite_structure = "NONE"
    if side == "BUY":
        if choch.get("state") == "BEARISH_CONFIRMED":
            opposite_shift = "BEARISH_CHOCH"
        if bos.get("state") == "BEARISH_CONFIRMED":
            opposite_structure = "BEARISH_BOS"
    elif side == "SELL":
        if choch.get("state") == "BULLISH_CONFIRMED":
            opposite_shift = "BULLISH_CHOCH"
        if bos.get("state") == "BULLISH_CONFIRMED":
            opposite_structure = "BULLISH_BOS"

    ob_integrity = "UNKNOWN"
    zone_reaction = "NONE"
    continuation = "NONE"
    premise = "UNKNOWN"

    score = 1.0
    exit_state = "NONE"
    exit_reason = ""
    urgency = "LOW"

    if current_price is not None and invalidation_level is not None:
        if side == "BUY" and current_price < invalidation_level:
            exit_state = "HARD_EXIT"
            exit_reason = "Price closed below invalidation"
            urgency = "HIGH"
            score = 0.0
        if side == "SELL" and current_price > invalidation_level:
            exit_state = "HARD_EXIT"
            exit_reason = "Price closed above invalidation"
            urgency = "HIGH"
            score = 0.0

    if exit_state == "NONE":
        if opposite_shift != "NONE" or opposite_structure != "NONE":
            exit_state = "HARD_EXIT"
            exit_reason = "Opposite structure appeared"
            urgency = "HIGH"
            score = min(score, 0.35)

    if current_price is not None and ob_low is not None and ob_high is not None:
        if side == "BUY":
            ob_integrity = "INTACT" if current_price >= ob_low else "BROKEN"
            in_zone = (current_price >= ob_low) and (current_price <= ob_high)
            if in_zone:
                zone_reaction = "TESTING"
                score -= 0.20
            elif current_price > ob_high:
                zone_reaction = "HOLDING"
            else:
                zone_reaction = "WEAK"
                score -= 0.25
        elif side == "SELL":
            ob_integrity = "INTACT" if current_price <= ob_high else "BROKEN"
            in_zone = (current_price >= ob_low) and (current_price <= ob_high)
            if in_zone:
                zone_reaction = "TESTING"
                score -= 0.20
            elif current_price < ob_low:
                zone_reaction = "HOLDING"
            else:
                zone_reaction = "WEAK"
                score -= 0.25

    if side in {"BUY", "SELL"} and current_price is not None and entry_price is not None:
        if side == "BUY":
            continuation = "GOOD" if current_price > entry_price else "STALL"
        else:
            continuation = "GOOD" if current_price < entry_price else "STALL"
        if continuation == "STALL":
            score -= 0.15

    if ob_integrity == "BROKEN":
        score -= 0.35

    score = max(0.0, min(1.0, float(score)))

    trade_state = "ENTERED"
    if exit_state == "HARD_EXIT":
        trade_state = "HARD_EXIT"
    else:
        if score >= 0.80:
            trade_state = "HEALTHY"
        elif score >= 0.60:
            trade_state = "WEAKENING"
        elif score >= 0.40:
            trade_state = "DEFENSIVE_EXIT"
        else:
            trade_state = "HARD_EXIT"
            exit_state = "HARD_EXIT"
            if not exit_reason:
                exit_reason = "Trade structure degraded"
            urgency = "MEDIUM"

    if trade_state == "HEALTHY":
        premise = "VALID"
    elif trade_state == "WEAKENING":
        premise = "DEGRADING"
    else:
        premise = "BROKEN"

    exit_risk = "LOW"
    if trade_state == "WEAKENING":
        exit_risk = "MEDIUM"
    elif trade_state in {"DEFENSIVE_EXIT", "HARD_EXIT"}:
        exit_risk = "HIGH"

    next_action = "WAIT"
    if trade_state == "HEALTHY":
        next_action = "HOLD"
    elif trade_state == "WEAKENING":
        next_action = "WATCH"
    elif trade_state == "DEFENSIVE_EXIT":
        next_action = "DEFENSIVE_EXIT"
    elif trade_state == "HARD_EXIT":
        next_action = "EXIT"

    return {
        "trade_state": trade_state,
        "trade_health": {
            "side": side,
            "entry_price": entry_price,
            "current_price": current_price,
            "pnl_points": pnl_points,
            "health_score": round(score, 2),
            "trade_state": trade_state,
            "exit_risk": exit_risk,
            "next_action": next_action,
        },
        "structure_monitor": {
            "zone_reaction": zone_reaction,
            "continuation": continuation,
            "premise": premise,
            "opposite_shift": opposite_shift,
            "ob_integrity": ob_integrity,
        },
        "exit_engine": {
            "exit_state": exit_state,
            "primary_reason": exit_reason,
            "invalidation": invalidation_level,
            "opposite_structure": opposite_structure,
            "urgency": urgency,
        },
    }


__all__ = [
    "detect_swings",
    "detect_choch",
    "detect_bos",
    "map_order_block",
    "evaluate_reaction",
    "evaluate_entry_state",
    "evaluate_trade_structure_health",
]

