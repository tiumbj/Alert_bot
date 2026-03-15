# ============================================================
# ชื่อโค้ด: Dashboard State Writer
# ที่อยู่ไฟล์: core/dashboard_state_writer.py
# คำสั่งรัน: python -m py_compile core/dashboard_state_writer.py
# เวอร์ชัน: v1.0.0
# ============================================================
"""
core/dashboard_state_writer.py
Version: v1.0.0
Purpose:
- Production-safe dashboard state loader / merger / atomic writer
- Shared runtime state manager for main.py and trade_monitor.py
- Supports bounded event stream and resilient default schema

LOCKED RULES
- Production only
- No demo / mock / sample loop
- Atomic write only
- Safe default schema
- Merge by section
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_EVENT_LIMIT = 50
DEFAULT_SYSTEM_STATUS = "MISSING"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_default_dashboard_state() -> dict[str, Any]:
    """Return the full default dashboard schema."""
    now_iso = _utc_now_iso()
    return {
        "header": {
            "mode": "PRODUCTION",
            "symbol": "",
            "broker": "",
            "timeframe": "",
            "system": DEFAULT_SYSTEM_STATUS,
            "position_state": "IDLE",
            "last_update": now_iso,
        },
        "market_structure": {
            "bias": "NEUTRAL",
            "last_swing_high": None,
            "last_swing_low": None,
            "choch": "NONE",
            "bos": "NONE",
            "active_ob_low": None,
            "active_ob_high": None,
            "zone_status": "IDLE",
        },
        "entry_lifecycle": {
            "state": "IDLE",
            "trigger_stack": [],
            "invalidation": None,
        },
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
            "invalidation": None,
            "opposite_structure": "NONE",
            "urgency": "LOW",
        },
        "trader_mentor": {
            "market_view": "",
            "action_view": "",
            "caution_view": "",
            "trigger_view": "",
        },
        "event_stream": [],
        "daily_report": {
            "date": "",
            "signals": 0,
            "entered": 0,
            "exited": 0,
            "wins": 0,
            "losses": 0,
            "open_trades": 0,
        },
        "meta": {
            "schema_version": "1.0.0",
            "source": "dashboard_state_writer",
            "updated_at": now_iso,
            "json_error": False,
        },
    }


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge dictionaries while replacing scalars/lists."""
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_dashboard_state(path: str | Path) -> dict[str, Any]:
    """Load dashboard state; fall back to default schema on missing or bad JSON."""
    state = build_default_dashboard_state()
    file_path = Path(path)

    if not file_path.exists():
        state["header"]["system"] = "MISSING"
        state["meta"]["updated_at"] = _utc_now_iso()
        return state

    try:
        raw = file_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("dashboard state root must be an object")
        state = _deep_merge(state, payload)
        state["header"]["system"] = state["header"].get("system") or "NOMINAL"
        state["meta"]["json_error"] = False
        state["meta"]["updated_at"] = _utc_now_iso()
        return state
    except Exception:
        state["header"]["system"] = "JSON_ERROR"
        state["meta"]["json_error"] = True
        state["meta"]["updated_at"] = _utc_now_iso()
        return state


def merge_dashboard_sections(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Merge only top-level sections that exist in the default schema."""
    default_state = build_default_dashboard_state()
    merged = _deep_merge(default_state, base)

    for section_name, section_payload in updates.items():
        if section_name not in default_state:
            continue
        if isinstance(section_payload, dict) and isinstance(merged.get(section_name), dict):
            merged[section_name] = _deep_merge(merged[section_name], section_payload)
        else:
            merged[section_name] = deepcopy(section_payload)

    merged["meta"]["updated_at"] = _utc_now_iso()
    return merged


def append_event_stream(
    state: dict[str, Any],
    message: str,
    limit: int = DEFAULT_EVENT_LIMIT,
) -> dict[str, Any]:
    """Append a single event to event_stream and keep only the last `limit` entries."""
    updated = deepcopy(state)
    stream = updated.get("event_stream")
    if not isinstance(stream, list):
        stream = []

    message_text = (message or "").strip()
    if not message_text:
        updated["event_stream"] = stream[-limit:]
        return updated

    timestamp = datetime.now().strftime("[%H:%M:%S]")
    stream.append(f"{timestamp} {message_text}")
    updated["event_stream"] = stream[-limit:]
    updated["meta"]["updated_at"] = _utc_now_iso()
    return updated


def atomic_write_dashboard_state(path: str | Path, payload: dict[str, Any]) -> None:
    """Write state JSON atomically to avoid partial/corrupt dashboard files."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    safe_payload = deepcopy(payload)
    safe_payload.setdefault("meta", {})
    safe_payload["meta"]["updated_at"] = _utc_now_iso()

    serialized = json.dumps(safe_payload, ensure_ascii=False, indent=2)

    fd, temp_path = tempfile.mkstemp(
        prefix=file_path.stem + "_",
        suffix=".tmp",
        dir=str(file_path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(serialized)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, file_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def update_dashboard_state(
    path: str | Path,
    section_updates: dict[str, Any],
    event_message: str | None = None,
    event_limit: int = DEFAULT_EVENT_LIMIT,
) -> dict[str, Any]:
    """Load current state, merge updates, optionally append event, and write atomically."""
    state = load_dashboard_state(path)
    state = merge_dashboard_sections(state, section_updates)

    state.setdefault("header", {})
    state["header"]["last_update"] = _utc_now_iso()
    state["header"]["system"] = state["header"].get("system") or "NOMINAL"

    if event_message:
        state = append_event_stream(state, event_message, limit=event_limit)

    atomic_write_dashboard_state(path, state)
    return state


__all__ = [
    "DEFAULT_EVENT_LIMIT",
    "build_default_dashboard_state",
    "load_dashboard_state",
    "merge_dashboard_sections",
    "append_event_stream",
    "atomic_write_dashboard_state",
    "update_dashboard_state",
]
