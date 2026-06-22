"""MySubaru websocket integration with push-updated entities."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Dict

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
import homeassistant.helpers.config_validation as cv

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
from .helpers import async_api_call

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    "sensor",
    "binary_sensor",
    "device_tracker",
    "button",
    "select",
    "lock",
    "switch",
]

# Service names registered by this integration, used for cleanup on unload.
SERVICES = (
    "get_trips",
    "get_recalls",
    "get_warning_lights",
    "get_roadside_assistance",
    "get_model_info",
    "get_favorite_pois",
    "get_valet_settings",
    "get_geofence_settings",
    "get_speedfence_settings",
    "get_curfew_settings",
    "get_ev_charge_settings",
    "send_poi",
    "save_favorite_poi",
    "set_geofence",
    "set_speedfence",
    "set_curfew",
    "delete_trip",
    "delete_geofence",
    "request_roadside_assistance",
    "refresh_vehicles",
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


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
        current = (
            {k: str(v) for k, v in troubles.items()}
            if isinstance(troubles, dict)
            else {}
        )

        # Find added/cleared trouble codes
        added = {
            k: v
            for k, v in current.items()
            if k not in prev_troubles or prev_troubles[k] != v
        }
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
        _LOGGER.debug(
            "updated vehicles from websocket", extra={"count": len(vins), "vins": vins}
        )
    else:
        _LOGGER.info(
            "websocket update had no vehicles",
            extra={
                "payload_keys": list(data.keys())[:10],
                "raw_keys": list((data.get("data") or {}).keys())[:10],
            },
        )

    async_dispatcher_send(hass, UPDATE_SIGNAL)
    hass.bus.async_fire(f"{DOMAIN}_updated", {"payload": data})


async def _listen_ws(
    hass: HomeAssistant, ws_url: str, stop_event: asyncio.Event
) -> None:
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
        # Back off before reconnecting, but wake immediately if asked to stop.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=RECONNECT_DELAY)


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
    base_http = (
        ws_url.replace("wss://", "https://")
        .replace("ws://", "http://")
        .rsplit("/", 1)[0]
    )
    try:
        status = await async_api_call(
            hass,
            f"{base_http}/auth/status",
            method="get",
            error_context="Reaching MySubaru server",
        )
    except HomeAssistantError as err:
        raise ConfigEntryNotReady(
            f"Cannot reach MySubaru server at {base_http}"
        ) from err

    if not status.get("authenticated"):
        try:
            await async_api_call(
                hass,
                f"{base_http}/auth/config",
                payload=creds_payload,
                error_context="Configuring MySubaru server",
            )
        except HomeAssistantError as err:
            raise ConfigEntryNotReady(
                f"Cannot configure MySubaru server at {base_http}"
            ) from err

    task = hass.async_create_background_task(
        _listen_ws(hass, ws_url, stop_event), name="mysubaru_websocket"
    )

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

    _register_services(hass, base_http)

    _LOGGER.info("MySubaru websocket listener starting", extra={"url": ws_url})
    return True


def _register_services(hass: HomeAssistant, base_http: str) -> None:
    """Register HA services for parameterized vehicle commands and data queries."""

    VIN_SCHEMA = vol.Schema({vol.Required("vin"): str})

    async def _post(url: str, payload: Any = None) -> Dict[str, Any]:
        return await async_api_call(hass, url, payload=payload)

    async def _get(url: str) -> Any:
        return await async_api_call(hass, url, method="get")

    # ── Data retrieval services ─────────────────────────────────────────

    async def handle_get_trips(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/trips")
        hass.bus.async_fire(f"{DOMAIN}_trips", {"vin": vin, "trips": data})

    async def handle_get_recalls(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/recalls")
        hass.bus.async_fire(f"{DOMAIN}_recalls", {"vin": vin, "recalls": data})

    async def handle_get_warning_lights(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/warning_lights")
        hass.bus.async_fire(
            f"{DOMAIN}_warning_lights", {"vin": vin, "warning_lights": data}
        )

    async def handle_get_roadside_assistance(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/roadside_assistance")
        hass.bus.async_fire(f"{DOMAIN}_roadside_assistance", {"vin": vin, "info": data})

    async def handle_get_model_info(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/model_info")
        hass.bus.async_fire(f"{DOMAIN}_model_info", {"vin": vin, "model_info": data})

    async def handle_get_favorite_pois(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/favorite_pois")
        hass.bus.async_fire(f"{DOMAIN}_favorite_pois", {"vin": vin, "pois": data})

    async def handle_get_valet_settings(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/valet_settings")
        hass.bus.async_fire(f"{DOMAIN}_valet_settings", {"vin": vin, "settings": data})

    async def handle_get_geofence_settings(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/geofence_settings")
        hass.bus.async_fire(
            f"{DOMAIN}_geofence_settings", {"vin": vin, "settings": data}
        )

    async def handle_get_speedfence_settings(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/speedfence_settings")
        hass.bus.async_fire(
            f"{DOMAIN}_speedfence_settings", {"vin": vin, "settings": data}
        )

    async def handle_get_curfew_settings(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/curfew_settings")
        hass.bus.async_fire(f"{DOMAIN}_curfew_settings", {"vin": vin, "settings": data})

    async def handle_get_ev_charge_settings(call: ServiceCall) -> None:
        vin = call.data["vin"]
        data = await _get(f"{base_http}/vehicle/{vin}/ev_charge_settings")
        hass.bus.async_fire(
            f"{DOMAIN}_ev_charge_settings", {"vin": vin, "settings": data}
        )

    # ── Parameterized command services ──────────────────────────────────

    async def handle_send_poi(call: ServiceCall) -> None:
        vin = call.data["vin"]
        poi = {
            "name": call.data["name"],
            "latitude": call.data["latitude"],
            "longitude": call.data["longitude"],
        }
        for opt in ("address", "city", "state", "zip", "category"):
            if opt in call.data:
                poi[opt] = call.data[opt]
        await _post(f"{base_http}/vehicle/{vin}/send_poi", poi)

    async def handle_save_favorite_poi(call: ServiceCall) -> None:
        vin = call.data["vin"]
        poi = {
            "name": call.data["name"],
            "latitude": call.data["latitude"],
            "longitude": call.data["longitude"],
        }
        for opt in ("address", "city", "state", "zip", "category"):
            if opt in call.data:
                poi[opt] = call.data[opt]
        await _post(f"{base_http}/vehicle/{vin}/save_favorite_poi", poi)

    async def handle_set_geofence(call: ServiceCall) -> None:
        vin = call.data["vin"]
        payload = {
            "latitude": call.data["latitude"],
            "longitude": call.data["longitude"],
            "radius": call.data["radius"],
            "name": call.data["name"],
            "enabled": call.data.get("enabled", True),
            "entry_alert": call.data.get("entry_alert", True),
            "exit_alert": call.data.get("exit_alert", True),
        }
        await _post(f"{base_http}/vehicle/{vin}/set_geofence", payload)

    async def handle_set_speedfence(call: ServiceCall) -> None:
        vin = call.data["vin"]
        payload = {
            "speed_limit": call.data["speed_limit"],
            "enabled": call.data.get("enabled", True),
            "persistent": call.data.get("persistent", False),
        }
        await _post(f"{base_http}/vehicle/{vin}/set_speedfence", payload)

    async def handle_set_curfew(call: ServiceCall) -> None:
        vin = call.data["vin"]
        payload = {
            "start_time": call.data["start_time"],
            "end_time": call.data["end_time"],
            "days_of_week": call.data["days_of_week"],
            "enabled": call.data.get("enabled", True),
        }
        await _post(f"{base_http}/vehicle/{vin}/set_curfew", payload)

    async def handle_delete_trip(call: ServiceCall) -> None:
        vin = call.data["vin"]
        await _post(
            f"{base_http}/vehicle/{vin}/delete_trip", {"trip_id": call.data["trip_id"]}
        )

    async def handle_delete_geofence(call: ServiceCall) -> None:
        vin = call.data["vin"]
        await _post(
            f"{base_http}/vehicle/{vin}/delete_geofence",
            {"fence_id": call.data["fence_id"]},
        )

    async def handle_request_roadside(call: ServiceCall) -> None:
        vin = call.data["vin"]
        payload = {
            "latitude": call.data["latitude"],
            "longitude": call.data["longitude"],
            "description": call.data.get("description", ""),
        }
        await _post(f"{base_http}/vehicle/{vin}/request_roadside_assistance", payload)

    async def handle_refresh_vehicles(call: ServiceCall) -> None:
        await _post(f"{base_http}/auth/refresh_vehicles")

    # ── Register all services ───────────────────────────────────────────

    svc = hass.services

    svc.async_register(DOMAIN, "get_trips", handle_get_trips, schema=VIN_SCHEMA)
    svc.async_register(DOMAIN, "get_recalls", handle_get_recalls, schema=VIN_SCHEMA)
    svc.async_register(
        DOMAIN, "get_warning_lights", handle_get_warning_lights, schema=VIN_SCHEMA
    )
    svc.async_register(
        DOMAIN,
        "get_roadside_assistance",
        handle_get_roadside_assistance,
        schema=VIN_SCHEMA,
    )
    svc.async_register(
        DOMAIN, "get_model_info", handle_get_model_info, schema=VIN_SCHEMA
    )
    svc.async_register(
        DOMAIN, "get_favorite_pois", handle_get_favorite_pois, schema=VIN_SCHEMA
    )
    svc.async_register(
        DOMAIN, "get_valet_settings", handle_get_valet_settings, schema=VIN_SCHEMA
    )
    svc.async_register(
        DOMAIN, "get_geofence_settings", handle_get_geofence_settings, schema=VIN_SCHEMA
    )
    svc.async_register(
        DOMAIN,
        "get_speedfence_settings",
        handle_get_speedfence_settings,
        schema=VIN_SCHEMA,
    )
    svc.async_register(
        DOMAIN, "get_curfew_settings", handle_get_curfew_settings, schema=VIN_SCHEMA
    )
    svc.async_register(
        DOMAIN,
        "get_ev_charge_settings",
        handle_get_ev_charge_settings,
        schema=VIN_SCHEMA,
    )

    svc.async_register(
        DOMAIN,
        "send_poi",
        handle_send_poi,
        schema=vol.Schema(
            {
                vol.Required("vin"): str,
                vol.Required("name"): str,
                vol.Required("latitude"): vol.Coerce(float),
                vol.Required("longitude"): vol.Coerce(float),
                vol.Optional("address"): str,
                vol.Optional("city"): str,
                vol.Optional("state"): str,
                vol.Optional("zip"): str,
                vol.Optional("category"): str,
            }
        ),
    )

    svc.async_register(
        DOMAIN,
        "save_favorite_poi",
        handle_save_favorite_poi,
        schema=vol.Schema(
            {
                vol.Required("vin"): str,
                vol.Required("name"): str,
                vol.Required("latitude"): vol.Coerce(float),
                vol.Required("longitude"): vol.Coerce(float),
                vol.Optional("address"): str,
                vol.Optional("city"): str,
                vol.Optional("state"): str,
                vol.Optional("zip"): str,
                vol.Optional("category"): str,
            }
        ),
    )

    svc.async_register(
        DOMAIN,
        "set_geofence",
        handle_set_geofence,
        schema=vol.Schema(
            {
                vol.Required("vin"): str,
                vol.Required("latitude"): vol.Coerce(float),
                vol.Required("longitude"): vol.Coerce(float),
                vol.Required("radius"): int,
                vol.Required("name"): str,
                vol.Optional("enabled", default=True): bool,
                vol.Optional("entry_alert", default=True): bool,
                vol.Optional("exit_alert", default=True): bool,
            }
        ),
    )

    svc.async_register(
        DOMAIN,
        "set_speedfence",
        handle_set_speedfence,
        schema=vol.Schema(
            {
                vol.Required("vin"): str,
                vol.Required("speed_limit"): int,
                vol.Optional("enabled", default=True): bool,
                vol.Optional("persistent", default=False): bool,
            }
        ),
    )

    svc.async_register(
        DOMAIN,
        "set_curfew",
        handle_set_curfew,
        schema=vol.Schema(
            {
                vol.Required("vin"): str,
                vol.Required("start_time"): str,
                vol.Required("end_time"): str,
                vol.Required("days_of_week"): [int],
                vol.Optional("enabled", default=True): bool,
            }
        ),
    )

    svc.async_register(
        DOMAIN,
        "delete_trip",
        handle_delete_trip,
        schema=vol.Schema({vol.Required("vin"): str, vol.Required("trip_id"): str}),
    )

    svc.async_register(
        DOMAIN,
        "delete_geofence",
        handle_delete_geofence,
        schema=vol.Schema({vol.Required("vin"): str, vol.Required("fence_id"): str}),
    )

    svc.async_register(
        DOMAIN,
        "request_roadside_assistance",
        handle_request_roadside,
        schema=vol.Schema(
            {
                vol.Required("vin"): str,
                vol.Required("latitude"): vol.Coerce(float),
                vol.Required("longitude"): vol.Coerce(float),
                vol.Optional("description", default=""): str,
            }
        ),
    )

    svc.async_register(
        DOMAIN, "refresh_vehicles", handle_refresh_vehicles, schema=vol.Schema({})
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    runtime = hass.data.get(DOMAIN, {}).get("runtime")
    if runtime:
        runtime["stop_event"].set()
        runtime["task"].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime["task"]

    for service in SERVICES:
        hass.services.async_remove(DOMAIN, service)

    if unload_ok:
        hass.data.pop(DOMAIN, None)

    return unload_ok
