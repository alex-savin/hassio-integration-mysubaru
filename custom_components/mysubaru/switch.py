"""Switch entities for MySubaru toggle features (valet, geofence, etc.)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import async_timeout
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, UPDATE_SIGNAL
from .device_info import build_device_info
from .helpers import HTTP_TIMEOUT


@dataclass
class SwitchDescription:
    key: str
    name: str
    icon_on: str
    icon_off: str
    action_on: str
    action_off: str
    status_action: str | None = None


SWITCH_DESCRIPTIONS: List[SwitchDescription] = [
    SwitchDescription(
        key="valet_mode",
        name="Valet Mode",
        icon_on="mdi:account-key",
        icon_off="mdi:account-key-outline",
        action_on="valet_start",
        action_off="valet_stop",
        status_action="valet_status",
    ),
    SwitchDescription(
        key="geofence",
        name="GeoFence Alerts",
        icon_on="mdi:map-marker-radius",
        icon_off="mdi:map-marker-radius-outline",
        action_on="geofence_activate",
        action_off="geofence_deactivate",
    ),
    SwitchDescription(
        key="speedfence",
        name="Speed Fence Alerts",
        icon_on="mdi:speedometer",
        icon_off="mdi:speedometer-slow",
        action_on="speedfence_activate",
        action_off="speedfence_deactivate",
    ),
    SwitchDescription(
        key="curfew",
        name="Curfew Alerts",
        icon_on="mdi:clock-alert",
        icon_off="mdi:clock-alert-outline",
        action_on="curfew_activate",
        action_off="curfew_deactivate",
    ),
]


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    added: set[str] = set()

    def _maybe_add_entities() -> None:
        new_entities: list[MySubaruSwitch] = []
        store: Dict[str, Any] = hass.data.get(DOMAIN, {})
        for vin, vehicle in store.get("vehicles", {}).items():
            for desc in SWITCH_DESCRIPTIONS:
                key = f"{vin}-{desc.key}"
                if key in added:
                    continue
                added.add(key)
                new_entities.append(MySubaruSwitch(vin, vehicle, desc))
        if new_entities:
            async_add_entities(new_entities)

    _maybe_add_entities()
    async_dispatcher_connect(hass, UPDATE_SIGNAL, _maybe_add_entities)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    await async_setup_platform(hass, {}, async_add_entities, None)


class MySubaruSwitch(SwitchEntity):
    _attr_should_poll = False

    def __init__(
        self, vin: str, vehicle: Dict[str, Any], description: SwitchDescription
    ) -> None:
        self._vin = vin
        self._description = description
        base_name = vehicle.get("CarNickname") or vehicle.get("CarName") or vin
        self._attr_unique_id = f"{vin}-{description.key}"
        self._attr_name = f"{base_name} {description.name}"
        self._attr_is_on = False

    @property
    def icon(self) -> str:
        if self._attr_is_on:
            return self._description.icon_on
        return self._description.icon_off

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, UPDATE_SIGNAL, self._handle_update)
        )
        self._handle_update()

        # Try to fetch initial status if a status endpoint exists.
        if self._description.status_action:
            self.hass.async_create_task(self._fetch_status())

    async def _fetch_status(self) -> None:
        """Fetch current on/off status from the server."""
        runtime = self.hass.data.get(DOMAIN, {}).get("runtime", {})
        base_http: str | None = runtime.get("base_http")
        if not base_http or not self._description.status_action:
            return

        url = f"{base_http}/vehicle/{self._vin}/{self._description.status_action}"
        session = async_get_clientsession(self.hass)
        try:
            async with async_timeout.timeout(HTTP_TIMEOUT):
                resp = await session.get(url)
            if resp.status < 400:
                data = await resp.json()
                self._attr_is_on = bool(data.get("enabled", False))
                self.async_write_ha_state()
        except Exception:  # noqa: BLE001
            pass  # status polling is best-effort

    def _handle_update(self) -> None:
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin)
        self._attr_available = vehicle is not None
        self.hass.add_job(self.async_write_ha_state)

    async def _send_command(self, action: str) -> None:
        runtime = self.hass.data.get(DOMAIN, {}).get("runtime", {})
        base_http: str | None = runtime.get("base_http")
        if not base_http:
            raise HomeAssistantError("MySubaru server base URL is unavailable")

        url = f"{base_http}/vehicle/{self._vin}/{action}"
        session = async_get_clientsession(self.hass)
        async with async_timeout.timeout(HTTP_TIMEOUT):
            resp = await session.post(url)
        if resp.status >= 400:
            message = await resp.text()
            raise HomeAssistantError(
                f"Command {action} failed ({resp.status}): {message or 'unknown error'}"
            )
        await resp.text()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send_command(self._description.action_on)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_command(self._description.action_off)
        self._attr_is_on = False
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        status = store.get("last_command_status", {}).get(self._vin, {})
        return {
            "vin": self._vin,
            "last_command": status.get("command"),
            "last_command_status": status.get("status"),
            "last_command_time": status.get("time"),
        }

    @property
    def device_info(self):
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin, {})
        base_name = self.name.replace(f" {self._description.name}", "")
        return build_device_info(self._vin, vehicle, base_name)
