"""Select entities for MySubaru climate profiles."""

from __future__ import annotations

from typing import Any, Dict, List

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
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
        new_entities: List[MySubaruClimateProfileSelect] = []
        store: Dict[str, Any] = hass.data.get(DOMAIN, {})
        for vin, vehicle in store.get("vehicles", {}).items():
            key = f"{vin}-climate-profile"
            if key in added:
                continue
            added.add(key)
            new_entities.append(MySubaruClimateProfileSelect(vin, vehicle))
        if new_entities:
            async_add_entities(new_entities)

    _maybe_add_entities()
    async_dispatcher_connect(hass, UPDATE_SIGNAL, _maybe_add_entities)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    await async_setup_platform(hass, {}, async_add_entities, None)


class MySubaruClimateProfileSelect(SelectEntity, RestoreEntity):
    _attr_should_poll = False

    def __init__(self, vin: str, vehicle: Dict[str, Any]) -> None:
        self._vin = vin
        base_name = vehicle.get("CarNickname") or vehicle.get("CarName") or vin
        self._attr_unique_id = f"{vin}-climate-profile"
        self._attr_name = f"{base_name} Climate Profile"
        self._name_to_key: Dict[str, str] = {}
        self._key_to_name: Dict[str, str] = {}
        self._attr_options = []
        self._attr_current_option = None
        self._restored_option: str | None = None

    async def async_added_to_hass(self) -> None:
        # Restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            self._restored_option = last_state.state

        self.async_on_remove(async_dispatcher_connect(self.hass, UPDATE_SIGNAL, self._handle_update))
        self._handle_update()

    def _handle_update(self) -> None:
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin)
        if vehicle is None:
            self._attr_available = False
            self.hass.add_job(self.async_write_ha_state)
            return

        profiles: Dict[str, Any] = vehicle.get("ClimateProfiles", {}) or {}
        options: List[str] = []
        name_to_key: Dict[str, str] = {}
        key_to_name: Dict[str, str] = {}
        user_presets: List[str] = []

        for key, profile in profiles.items():
            base_name = profile.get("name") or key
            preset_type = profile.get("presetType")
            type_label = None
            if preset_type == "subaruPreset":
                type_label = "Subaru Preset"
            elif preset_type == "userPreset":
                type_label = "User Preset"
            elif preset_type:
                type_label = str(preset_type)

            label = f"{base_name} ({type_label})" if type_label else base_name
            options.append(label)
            name_to_key[label] = key
            key_to_name[key] = label

            if preset_type == "userPreset":
                user_presets.append(label)

        options.sort()
        user_presets.sort()
        self._name_to_key = name_to_key
        self._key_to_name = key_to_name
        self._attr_options = options
        self._attr_available = bool(options)

        selected_store: Dict[str, str] = store.setdefault("selected_climate_profile", {})
        stored = selected_store.get(self._vin)

        # Priority: 1) in-memory store, 2) restored state from disk, 3) first option
        if stored in key_to_name:
            current = key_to_name[stored]
        elif stored in options:
            current = stored
            selected_store[self._vin] = name_to_key.get(current, stored)
        elif self._restored_option and self._restored_option in options:
            # Restore from saved state after restart
            current = self._restored_option
            selected_store[self._vin] = name_to_key.get(current, current)
        else:
            current = options[0] if options else None
            if current:
                selected_store[self._vin] = name_to_key.get(current, current)

        self._attr_current_option = current

        self._attr_extra_state_attributes = {
            "vin": self._vin,
            "timestamp": store.get("timestamp"),
            "profile_keys": list(profiles.keys()),
            "selected_profile_key": selected_store.get(self._vin),
            "user_presets": user_presets,
        }
        self.hass.add_job(self.async_write_ha_state)

    async def async_select_option(self, option: str) -> None:
        if option not in self._name_to_key:
            raise ValueError(f"Invalid climate profile: {option}")
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        selected_store: Dict[str, str] = store.setdefault("selected_climate_profile", {})
        selected_store[self._vin] = self._name_to_key[option]
        self._attr_current_option = option
        self.hass.add_job(self.async_write_ha_state)

    @property
    def device_info(self):
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin, {})
        base_name = self.name.replace(" Climate Profile", "")
        return build_device_info(self._vin, vehicle, base_name)
