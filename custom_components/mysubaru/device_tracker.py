"""Device tracker for MySubaru vehicles (GPS)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, UPDATE_SIGNAL
from .device_info import build_device_info


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
            key = f"{vin}-tracker"
            if key in added:
                continue
            added.add(key)
            new_entities.append(MySubaruTracker(vin, vehicle))
        if new_entities:
            async_add_entities(new_entities)

    _maybe_add_entities()
    async_dispatcher_connect(hass, UPDATE_SIGNAL, _maybe_add_entities)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    await async_setup_platform(hass, {}, async_add_entities, None)


class MySubaruTracker(TrackerEntity):
    _attr_should_poll = False
    _attr_source_type = SourceType.GPS

    def __init__(self, vin: str, vehicle: Dict[str, Any]) -> None:
        self._vin = vin
        name = vehicle.get("CarNickname") or vehicle.get("CarName") or vin
        self._attr_unique_id = f"{vin}-tracker"
        self._attr_name = f"{name} Location"

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

        geo = vehicle.get("GeoLocation", {}) or {}
        lat: Optional[float] = geo.get("Latitude") or geo.get("latitude")
        lon: Optional[float] = geo.get("Longitude") or geo.get("longitude")
        heading = geo.get("Heading") if "Heading" in geo else geo.get("heading")

        self._attr_available = lat is not None and lon is not None
        self._attr_latitude = lat
        self._attr_longitude = lon
        self._attr_extra_state_attributes = {
            "vin": self._vin,
            "heading": heading,
            "timestamp": store.get("timestamp"),
        }
        self.hass.add_job(self.async_write_ha_state)

    @property
    def icon(self) -> str:
        """Return icon based on whether vehicle is at home or away."""
        if self.state == "home":
            return "mdi:car"
        return "mdi:car-arrow-right"

    @property
    def device_info(self):
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin, {})
        base_name = self.name.replace(" Location", "")
        return build_device_info(self._vin, vehicle, base_name)
