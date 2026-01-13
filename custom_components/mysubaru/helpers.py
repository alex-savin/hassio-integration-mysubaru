"""Shared helper functions for MySubaru integration."""

from __future__ import annotations

from typing import Any, Dict

# Default timeout for HTTP requests in seconds
HTTP_TIMEOUT = 30


def get_lock_status(vehicle: Dict[str, Any]) -> bool | None:
    """Determine if vehicle is locked based on door lock states.
    
    Returns:
        True if all doors are locked
        False if any door is unlocked
        None if lock status cannot be determined
    """
    doors = vehicle.get("Doors", {}) or {}
    if not doors:
        return None
    
    locks = []
    for door in doors.values():
        val = door.get("Lock")
        if not val:
            continue
        norm = val.strip().upper()
        if norm in {"UNKNOWN", "NOT_EQUIPPED"}:
            continue
        locks.append(norm)
    
    if not locks:
        return None
    return all(lock == "LOCKED" for lock in locks)


def get_door_lock_states(vehicle: Dict[str, Any]) -> Dict[str, str]:
    """Get individual door lock states for display in entity attributes.
    
    Returns a dict like:
        {
            "door_boot": "LOCKED",
            "door_front_left": "LOCKED",
            "door_front_right": "LOCKED",
            "door_rear_left": "LOCKED",
            "door_rear_right": "LOCKED",
        }
    """
    doors = vehicle.get("Doors", {}) or {}
    result: Dict[str, str] = {}
    
    for name, door in doors.items():
        lock_val = door.get("Lock")
        if not lock_val:
            continue
        norm = lock_val.strip().upper()
        if norm in {"UNKNOWN", "NOT_EQUIPPED"}:
            continue
        result[name] = norm
    
    return result
