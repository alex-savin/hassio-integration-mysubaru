"""Button entities for MySubaru remote actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import async_timeout
from homeassistant.components.button import ButtonEntity
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
class ButtonDescription:
    key: str
    name: str
    icon: str
    action: str


BUTTON_DESCRIPTIONS: List[ButtonDescription] = [
    ButtonDescription(key="lock", name="Lock Doors", icon="mdi:lock", action="lock"),
    ButtonDescription(
        key="unlock", name="Unlock Doors", icon="mdi:lock-open", action="unlock"
    ),
    ButtonDescription(
        key="remote_start", name="Remote Start", icon="mdi:power", action="remote_start"
    ),
    ButtonDescription(
        key="remote_stop",
        name="Remote Stop",
        icon="mdi:stop-circle-outline",
        action="remote_stop",
    ),
    ButtonDescription(
        key="ev_charge",
        name="Start Charging",
        icon="mdi:ev-station",
        action="ev_charge",
    ),
    ButtonDescription(
        key="poll", name="Update Location", icon="mdi:crosshairs-gps", action="poll"
    ),
    # Horn & Lights
    ButtonDescription(
        key="horn_start", name="Horn Start", icon="mdi:bullhorn", action="horn_start"
    ),
    ButtonDescription(
        key="horn_stop",
        name="Horn Stop",
        icon="mdi:bullhorn-outline",
        action="horn_stop",
    ),
    ButtonDescription(
        key="lights_start",
        name="Lights Start",
        icon="mdi:car-light-high",
        action="lights_start",
    ),
    ButtonDescription(
        key="lights_stop",
        name="Lights Stop",
        icon="mdi:car-light-dimmed",
        action="lights_stop",
    ),
    # Cancel commands
    ButtonDescription(
        key="lock_cancel",
        name="Cancel Lock",
        icon="mdi:lock-off",
        action="lock_cancel",
    ),
    ButtonDescription(
        key="unlock_cancel",
        name="Cancel Unlock",
        icon="mdi:lock-off-outline",
        action="unlock_cancel",
    ),
    ButtonDescription(
        key="engine_start_cancel",
        name="Cancel Engine Start",
        icon="mdi:engine-off",
        action="engine_start_cancel",
    ),
    ButtonDescription(
        key="lights_cancel",
        name="Cancel Lights",
        icon="mdi:car-light-dimmed",
        action="lights_cancel",
    ),
    ButtonDescription(
        key="horn_lights_cancel",
        name="Cancel Horn & Lights",
        icon="mdi:cancel",
        action="horn_lights_cancel",
    ),
    # Trip logging
    ButtonDescription(
        key="triplog_start",
        name="Start Trip Log",
        icon="mdi:map-marker-plus",
        action="triplog_start",
    ),
    ButtonDescription(
        key="triplog_stop",
        name="Stop Trip Log",
        icon="mdi:map-marker-off",
        action="triplog_stop",
    ),
]


def _supports_remote_start(vehicle: Dict[str, Any]) -> bool:
    features = vehicle.get("Features") or []
    subscriptions = vehicle.get("SubscriptionFeatures") or []
    return "REMOTE" in subscriptions or any(
        f in features for f in ("RES", "RESCC", "RCC")
    )


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    added: set[str] = set()

    def _maybe_add_entities() -> None:
        new_entities: list[MySubaruButton] = []
        store: Dict[str, Any] = hass.data.get(DOMAIN, {})
        for vin, vehicle in store.get("vehicles", {}).items():
            for desc in BUTTON_DESCRIPTIONS:
                if desc.action in {
                    "remote_start",
                    "remote_stop",
                } and not _supports_remote_start(vehicle):
                    continue
                if desc.key.startswith("ev_") and not vehicle.get("EV", False):
                    continue

                key = f"{vin}-{desc.key}"
                if key in added:
                    continue
                added.add(key)
                new_entities.append(MySubaruButton(vin, vehicle, desc))
        if new_entities:
            async_add_entities(new_entities)

    _maybe_add_entities()
    async_dispatcher_connect(hass, UPDATE_SIGNAL, _maybe_add_entities)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    await async_setup_platform(hass, {}, async_add_entities, None)


class MySubaruButton(ButtonEntity):
    _attr_should_poll = False

    def __init__(
        self, vin: str, vehicle: Dict[str, Any], description: ButtonDescription
    ) -> None:
        self._vin = vin
        self._description = description
        base_name = vehicle.get("CarNickname") or vehicle.get("CarName") or vin
        self._attr_unique_id = f"{vin}-{description.key}"
        self._attr_name = f"{base_name} {description.name}"
        self._attr_icon = description.icon

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, UPDATE_SIGNAL, self._handle_update)
        )
        self._handle_update()

    def _handle_update(self) -> None:
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin)
        self._attr_available = vehicle is not None
        self.hass.add_job(self.async_write_ha_state)

    async def async_press(self) -> None:
        runtime = self.hass.data.get(DOMAIN, {}).get("runtime", {})
        base_http: str | None = runtime.get("base_http")
        if not base_http:
            raise HomeAssistantError("MySubaru server base URL is unavailable")

        url = f"{base_http}/vehicle/{self._vin}/{self._description.action}"
        payload = None
        if self._description.action == "remote_start":
            store = self.hass.data.get(DOMAIN, {})
            selected = store.get("selected_climate_profile", {}).get(self._vin)
            if selected:
                payload = {"profile": selected}
        session = async_get_clientsession(self.hass)
        async with async_timeout.timeout(HTTP_TIMEOUT):
            if payload is None:
                resp = await session.post(url)
            else:
                resp = await session.post(url, json=payload)
        if resp.status >= 400:
            message = await resp.text()
            raise HomeAssistantError(
                f"Command failed ({resp.status}) for {self.name}: {message or 'unknown error'}"
            )
        await resp.text()

    @property
    def extra_state_attributes(self):
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        status = store.get("last_command_status", {}).get(self._vin, {})
        return {
            "last_command": status.get("command"),
            "last_command_status": status.get("status"),
            "last_command_message": status.get("message"),
            "last_command_time": status.get("time"),
        }

    @property
    def device_info(self):
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin, {})
        base_name = self.name.replace(f" {self._description.name}", "")
        return build_device_info(self._vin, vehicle, base_name)
