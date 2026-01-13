"""MySubaru websocket integration with push-updated entities."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
import aiohttp
import async_timeout
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_REGION,
    CONF_USERNAME,
    CONF_WS_URL,
    DOMAIN,
    RECONNECT_DELAY,
    UPDATE_SIGNAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "device_tracker", "button", "select", "lock"]


def _update_state(hass: HomeAssistant, payload: str) -> None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        _LOGGER.debug("dropping non-JSON payload", extra={"payload": payload[:200]})
        return

    # Handle command status messages separately.
    if data.get("type") == "command_status":
        vin = data.get("vin")
        status_entry = {
            "vin": vin,
            "command": data.get("command"),
            "status": data.get("status"),
            "message": data.get("message"),
            "time": data.get("time"),
        }

        store = hass.data.setdefault(DOMAIN, {})
        last = store.setdefault("last_command_status", {})
        if vin:
            last[vin] = status_entry

        hass.bus.async_fire(f"{DOMAIN}_command_status", status_entry)
        async_dispatcher_send(hass, UPDATE_SIGNAL)
        return

    vehicles = (
        data.get("vehicles")
        or data.get("Vehicles")
        or (data.get("data") or {}).get("vehicles")
        or []
    )
    store: Dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    store.setdefault("vehicles", {})
    troubles_store: Dict[str, Any] = store.setdefault("troubles", {})
    vins = []
    for vehicle in vehicles:
        vin = vehicle.get("Vin") or vehicle.get("vin")
        if not vin:
            continue
        # Track vehicles
        store["vehicles"][vin] = vehicle

        # Track troubles if present
        troubles = vehicle.get("Troubles") or {}
        prev_troubles = troubles_store.get(vin) or {}
        # Normalize to dict[str,str]
        current = {k: str(v) for k, v in troubles.items()} if isinstance(troubles, dict) else {}

        # Find added/cleared trouble codes
        added = {k: v for k, v in current.items() if k not in prev_troubles or prev_troubles[k] != v}
        cleared = {k: v for k, v in prev_troubles.items() if k not in current}

        if added or cleared:
            # Persist latest snapshot
            troubles_store[vin] = current
            vehicle_name = vehicle.get("CarNickname") or vehicle.get("CarName") or vin

            for code, desc in added.items():
                hass.bus.async_fire(
                    f"{DOMAIN}_trouble",
                    {
                        "vin": vin,
                        "vehicle_name": vehicle_name,
                        "event": "added",
                        "code": code,
                        "description": desc,
                        "time": data.get("timestamp") or data.get("Timestamp"),
                    },
                )

            for code, desc in cleared.items():
                hass.bus.async_fire(
                    f"{DOMAIN}_trouble",
                    {
                        "vin": vin,
                        "vehicle_name": vehicle_name,
                        "event": "cleared",
                        "code": code,
                        "description": desc,
                        "time": data.get("timestamp") or data.get("Timestamp"),
                    },
                )
        vins.append(vin)
    store["timestamp"] = data.get("timestamp") or data.get("Timestamp")

    if vins:
        _LOGGER.debug("updated vehicles from websocket", extra={"count": len(vins), "vins": vins})
    else:
        _LOGGER.info(
            "websocket update had no vehicles",
            extra={"payload_keys": list(data.keys())[:10], "raw_keys": list((data.get("data") or {}).keys())[:10]},
        )

    async_dispatcher_send(hass, UPDATE_SIGNAL)
    hass.bus.async_fire(f"{DOMAIN}_updated", {"payload": data})


async def _listen_ws(hass: HomeAssistant, ws_url: str, stop_event: asyncio.Event) -> None:
    import websockets  # imported lazily to keep HA startup quick

    while not stop_event.is_set():
        try:
            async with websockets.connect(ws_url) as websocket:
                _LOGGER.info("connected to MySubaru websocket", extra={"url": ws_url})
                async for message in websocket:
                    _update_state(hass, message)
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosedOK as err:
            _LOGGER.info(
                "websocket closed cleanly; retrying",
                extra={"code": err.code, "reason": err.reason},
            )
        except websockets.exceptions.ConnectionClosedError as err:
            _LOGGER.warning(
                "websocket connection closed unexpectedly; retrying",
                extra={"code": err.code, "reason": err.reason},
            )
        except Exception as err:  # broad catch to allow retries
            _LOGGER.warning("websocket connection dropped; retrying", exc_info=err)
        await asyncio.wait_for(asyncio.sleep(RECONNECT_DELAY), timeout=None)


async def _configure_server(base_http: str, creds_payload: Dict[str, Any]) -> bool:
    config_url = f"{base_http}/auth/config"
    try:
        async with aiohttp.ClientSession() as session:
            with async_timeout.timeout(10):
                async with session.post(config_url, json=creds_payload) as resp:
                    if resp.status >= 400:
                        _LOGGER.warning(
                            "failed to configure MySubaru server",
                            extra={"status": resp.status, "url": config_url},
                        )
                        return False
        return True
    except Exception as err:
        _LOGGER.warning(
            "failed to configure MySubaru server",
            exc_info=err,
            extra={"url": config_url},
        )
        return False


async def _get_status(base_http: str) -> Dict[str, Any]:
    status_url = f"{base_http}/auth/status"
    async with aiohttp.ClientSession() as session:
        with async_timeout.timeout(10):
            async with session.get(status_url) as resp:
                resp.raise_for_status()
                try:
                    return await resp.json()
                except Exception:
                    return {}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # YAML not supported; use config flow
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ws_url: str = entry.data[CONF_WS_URL]
    stop_event: asyncio.Event = asyncio.Event()

    # Send credentials to server on setup
    creds_payload = {
        "username": entry.data[CONF_USERNAME],
        "password": entry.data[CONF_PASSWORD],
        "pin": entry.data[CONF_PIN],
        "device_id": entry.data[CONF_DEVICE_ID],
        "device_name": entry.data[CONF_DEVICE_NAME],
        "region": entry.data.get(CONF_REGION),
    }
    base_http = ws_url.replace("wss://", "https://").replace("ws://", "http://").rsplit("/", 1)[0]
    try:
        status = await _get_status(base_http)
    except Exception as err:
        raise ConfigEntryNotReady(f"Cannot reach MySubaru server at {base_http}") from err

    if not status.get("authenticated"):
        if not await _configure_server(base_http, creds_payload):
            raise ConfigEntryNotReady(f"Cannot configure MySubaru server at {base_http}")

    task = hass.loop.create_task(_listen_ws(hass, ws_url, stop_event))

    hass.data.setdefault(DOMAIN, {})["runtime"] = {
        "stop_event": stop_event,
        "task": task,
        "base_http": base_http,
    }

    async def _stop_ws(event: Any) -> None:
        stop_event.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_ws)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("MySubaru websocket listener starting", extra={"url": ws_url})
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    runtime = hass.data.get(DOMAIN, {}).get("runtime")
    if runtime:
        runtime["stop_event"].set()
        runtime["task"].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime["task"]

    return unload_ok
