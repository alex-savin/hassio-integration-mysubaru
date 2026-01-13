"""Binary sensors for MySubaru websocket integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, UPDATE_SIGNAL
from .device_info import build_device_info
from .helpers import get_lock_status, get_door_lock_states


@dataclass
class BinarySensorDescription:
    key: str
    name: str
    device_class: Optional[BinarySensorDeviceClass]
    icon: str
    is_on_fn: Callable[[Dict[str, Any]], Optional[bool]]
    entity_category: Optional[EntityCategory] = None


def _doors_open(vehicle: Dict[str, Any]) -> Optional[bool]:
    doors = vehicle.get("Doors", {}) or {}
    if not doors:
        return None
    return any((d.get("Status") or "").upper() != "CLOSED" for d in doors.values())


def _windows_open(vehicle: Dict[str, Any]) -> Optional[bool]:
    windows = vehicle.get("Windows", {}) or {}
    if not windows:
        return None
    return any((w.get("Status") or "").upper() not in {"CLOSE", "CLOSED"} for w in windows.values())


def _unlocked(vehicle: Dict[str, Any]) -> Optional[bool]:
    locked = get_lock_status(vehicle)
    if locked is None:
        return None
    return not locked


def _has_troubles(hass: HomeAssistant, vin: str) -> Optional[bool]:
    """Return True if vehicle has any active trouble codes."""
    store: Dict[str, Any] = hass.data.get(DOMAIN, {})
    troubles = store.get("troubles", {}).get(vin, {})
    if not troubles:
        return False
    return len(troubles) > 0


BINARY_SENSOR_DESCRIPTIONS = [
    BinarySensorDescription(
        key="doors_open",
        name="Doors Open",
        device_class=BinarySensorDeviceClass.DOOR,
        icon="mdi:car-door",
        is_on_fn=_doors_open,
    ),
    BinarySensorDescription(
        key="windows_open",
        name="Windows Open",
        device_class=BinarySensorDeviceClass.WINDOW,
        icon="mdi:car-door",
        is_on_fn=_windows_open,
    ),
    BinarySensorDescription(
        key="locked",
        name="Locked",
        device_class=BinarySensorDeviceClass.LOCK,
        icon="mdi:car-key",
        is_on_fn=_unlocked,
    ),
    BinarySensorDescription(
        key="troubles",
        name="Troubles",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert-circle",
        is_on_fn=None,  # Handled specially since it needs hass context
        entity_category=EntityCategory.DIAGNOSTIC,
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
        new_entities = []
        store: Dict[str, Any] = hass.data.get(DOMAIN, {})
        for vin, vehicle in store.get("vehicles", {}).items():
            for desc in BINARY_SENSOR_DESCRIPTIONS:
                key = f"{vin}-{desc.key}"
                if key in added:
                    continue
                added.add(key)
                new_entities.append(MySubaruBinarySensor(vin, vehicle, desc))
        if new_entities:
            async_add_entities(new_entities)

    _maybe_add_entities()
    async_dispatcher_connect(hass, UPDATE_SIGNAL, _maybe_add_entities)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    await async_setup_platform(hass, {}, async_add_entities, None)


class MySubaruBinarySensor(BinarySensorEntity):
    _attr_should_poll = False

    def __init__(self, vin: str, vehicle: Dict[str, Any], description: BinarySensorDescription) -> None:
        self._vin = vin
        self._description = description
        self._attr_unique_id = f"{vin}-{description.key}"
        self._attr_name = f"{vehicle.get('CarNickname') or vehicle.get('CarName') or vin} {description.name}"
        self._attr_device_class = description.device_class
        self._attr_icon = description.icon
        self._attr_entity_category = description.entity_category

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, UPDATE_SIGNAL, self._handle_update)
        )
        self._handle_update()

    def _handle_update(self) -> None:
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin)
        if vehicle is None:
            self._attr_available = False
            self.hass.add_job(self.async_write_ha_state)
            return

        doors = vehicle.get("Doors", {}) or {}
        windows = vehicle.get("Windows", {}) or {}

        def _is_open(status: str | None, closed_values: set[str]) -> bool:
            if not status:
                return False
            return status.upper() not in closed_values

        open_doors = {
            name: door.get("Status")
            for name, door in doors.items()
            if _is_open(door.get("Status"), {"CLOSED"})
        }

        open_windows = {
            name: window.get("Status")
            for name, window in windows.items()
            if _is_open(window.get("Status"), {"CLOSE", "CLOSED"})
        }

        window_states = {name: window.get("Status") for name, window in windows.items()}

        door_states = {name: door.get("Status") for name, door in doors.items()}

        locks = get_door_lock_states(vehicle)
        lock_eval = get_lock_status(vehicle)

        self._attr_available = True
        if self._description.is_on_fn is not None:
            self._attr_is_on = self._description.is_on_fn(vehicle)
        attrs: Dict[str, Any] = {
            "vin": self._vin,
            "timestamp": store.get("timestamp"),
        }

        if self._description.key == "doors_open":
            attrs["open_doors"] = open_doors
            attrs["door_states"] = door_states
        elif self._description.key == "windows_open":
            attrs["open_windows"] = open_windows
            attrs["window_states"] = window_states
        elif self._description.key == "locked":
            attrs["locks"] = locks
            attrs["lock_evaluated"] = lock_eval
        elif self._description.key == "troubles":
            troubles = store.get("troubles", {}).get(self._vin, {})
            self._attr_is_on = len(troubles) > 0
            attrs["trouble_count"] = len(troubles)
            attrs["trouble_codes"] = list(troubles.keys()) if troubles else []
            attrs["troubles"] = troubles
            # Human-readable summary
            if troubles:
                descriptions = [f"{code}: {desc}" for code, desc in troubles.items()]
                attrs["trouble_descriptions"] = descriptions
                attrs["trouble_summary"] = "; ".join(descriptions)
            else:
                attrs["trouble_descriptions"] = []
                attrs["trouble_summary"] = "No active troubles"

        self._attr_extra_state_attributes = attrs
        self.hass.add_job(self.async_write_ha_state)

    @property
    def device_info(self):
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin, {})
        name = self.name.split(self._description.name)[0].strip()
        return build_device_info(self._vin, vehicle, name)