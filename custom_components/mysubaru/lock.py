"""Lock entity for MySubaru vehicles with remote commands."""

from __future__ import annotations

from typing import Any, Dict

from homeassistant.components.lock import LockEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, UPDATE_SIGNAL
from .device_info import build_device_info
from .helpers import async_api_call, get_lock_status, get_door_lock_states


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    added: set[str] = set()

    @callback
    def _maybe_add_entities() -> None:
        new_entities = []
        store: Dict[str, Any] = hass.data.get(DOMAIN, {})
        for vin, vehicle in store.get("vehicles", {}).items():
            key = f"{vin}-lock"
            if key in added:
                continue
            added.add(key)
            new_entities.append(MySubaruLock(vin, vehicle))
        if new_entities:
            async_add_entities(new_entities)

    _maybe_add_entities()
    async_dispatcher_connect(hass, UPDATE_SIGNAL, _maybe_add_entities)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    await async_setup_platform(hass, {}, async_add_entities, None)


class MySubaruLock(LockEntity):
    _attr_should_poll = False
    _attr_supported_features = 0
    _attr_has_entity_name = True

    def __init__(self, vin: str, vehicle: Dict[str, Any]) -> None:
        self._vin = vin
        self._attr_unique_id = f"{vin}-lock"
        self._base_name = vehicle.get("CarNickname") or vehicle.get("CarName") or vin
        self._attr_name = "Lock"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, UPDATE_SIGNAL, self._handle_update)
        )
        self._handle_update()

    @callback
    def _handle_update(self) -> None:
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin)
        if vehicle is None:
            self._attr_available = False
            self.async_write_ha_state()
            return

        self._attr_available = True

        # Get individual door lock statuses
        door_locks = get_door_lock_states(vehicle)

        # Determine overall lock status: locked only if ALL doors are locked
        # If any door is unlocked, the vehicle is considered unlocked
        self._attr_is_locked = get_lock_status(vehicle)

        status = store.get("last_command_status", {}).get(self._vin, {})

        # Build attributes with individual door lock statuses
        attrs: Dict[str, Any] = {
            "vin": self._vin,
            "timestamp": store.get("timestamp"),
        }

        # Add individual door lock statuses (e.g., door_front_left: LOCKED)
        for door_name, lock_status in door_locks.items():
            attrs[door_name] = lock_status

        # Add command status info
        attrs["last_command"] = status.get("command")
        attrs["last_command_status"] = status.get("status")
        attrs["last_command_message"] = status.get("message")
        attrs["last_command_time"] = status.get("time")

        self._attr_extra_state_attributes = attrs
        self.async_write_ha_state()

    async def async_lock(self, **kwargs) -> None:  # type: ignore[override]
        runtime = self.hass.data.get(DOMAIN, {}).get("runtime", {})
        base_http: str | None = runtime.get("base_http")
        if not base_http:
            raise HomeAssistantError("MySubaru server base URL is unavailable")

        await async_api_call(
            self.hass, f"{base_http}/vehicle/{self._vin}/lock", error_context="Lock"
        )

    async def async_unlock(self, **kwargs) -> None:  # type: ignore[override]
        runtime = self.hass.data.get(DOMAIN, {}).get("runtime", {})
        base_http: str | None = runtime.get("base_http")
        if not base_http:
            raise HomeAssistantError("MySubaru server base URL is unavailable")

        await async_api_call(
            self.hass, f"{base_http}/vehicle/{self._vin}/unlock", error_context="Unlock"
        )

    @property
    def device_info(self):
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin, {})
        return build_device_info(self._vin, vehicle, self._base_name)
