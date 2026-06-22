"""Shared helper functions for MySubaru integration."""

from __future__ import annotations

from typing import Any, Dict

import aiohttp
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# Default timeout for HTTP requests in seconds
HTTP_TIMEOUT = 30


async def async_api_call(
    hass: HomeAssistant,
    url: str,
    *,
    method: str = "post",
    payload: Any = None,
    error_context: str = "Request",
) -> Dict[str, Any]:
    """Call the MySubaru bridge using Home Assistant's shared aiohttp session.

    Returns the parsed JSON body, or an empty dict when the body is empty or
    not valid JSON. Raises ``HomeAssistantError`` on timeout, transport error,
    or any HTTP status >= 400.
    """
    session = async_get_clientsession(hass)
    try:
        async with async_timeout.timeout(HTTP_TIMEOUT):
            if method == "get":
                resp = await session.get(url)
            elif payload is not None:
                resp = await session.post(url, json=payload)
            else:
                resp = await session.post(url)
            if resp.status >= 400:
                detail = await resp.text()
                raise HomeAssistantError(
                    f"{error_context} failed ({resp.status}): {detail or 'unknown error'}"
                )
            try:
                return await resp.json(content_type=None)
            except ValueError:
                return {}
    except TimeoutError as err:
        raise HomeAssistantError(
            f"{error_context} timed out after {HTTP_TIMEOUT}s"
        ) from err
    except aiohttp.ClientError as err:
        raise HomeAssistantError(f"{error_context} failed: {err}") from err


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
