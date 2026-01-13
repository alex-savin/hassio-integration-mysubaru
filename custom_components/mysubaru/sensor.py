"""Sensors for MySubaru websocket integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, UPDATE_SIGNAL
from .device_info import build_device_info


_TIRE_PRESSURE_RANGES_PSI: Dict[str, tuple[int, int]] = {
    "ascent": (33, 35),
    "crosstrek": (30, 36),
    "forester": (28, 36),
    "impreza": (29, 36),
    "outback": (30, 35),
    "wrx": (32, 33),
    "wrx sti": (32, 33),
    "legacy": (33, 36),
    "brz": (33, 36),
    "solterra": (38, 42),
}


@dataclass
class SensorDescription:
    key: str
    name: str
    unit: Optional[str]
    icon: str
    value_fn: Callable[[Dict[str, Any]], Any]
    entity_category: Optional[EntityCategory] = None
    device_class: Optional[SensorDeviceClass] = None
    state_class: Optional[SensorStateClass] = None


def _range_kilometers(vehicle: Dict[str, Any]) -> Optional[float]:
    distance = vehicle.get("DistanceToEmpty", {}) or {}

    km = distance.get("Kilometers")
    if km is not None:
        try:
            return float(km)
        except (TypeError, ValueError):
            return None

    km10 = distance.get("Kilometers10s")
    if isinstance(km10, (int, float)) and km10 > 0:
        return float(km10)

    miles = distance.get("Miles")
    if miles is None:
        return None
    try:
        return float(miles) * 1.60934
    except (TypeError, ValueError):
        return None


def _tire_status(vehicle: Dict[str, Any]) -> tuple[Optional[str], Dict[str, Any]]:
    model = (vehicle.get("ModelName") or vehicle.get("CarName") or "").lower()
    tires: Dict[str, Any] = vehicle.get("Tires", {}) or {}
    pressures: Dict[str, Optional[float]] = {}
    for name, tire in tires.items():
        psi = tire.get("PressurePsi")
        try:
            pressures[name] = float(psi) if psi is not None else None
        except (TypeError, ValueError):
            pressures[name] = None

    if not pressures:
        return None, {"model": model, "recommended_min_psi": None, "recommended_max_psi": None, "pressures": {}}

    recommended = None
    for key, rng in _TIRE_PRESSURE_RANGES_PSI.items():
        if key in model:
            recommended = rng
            break

    if recommended is None:
        return None, {
            "model": model,
            "recommended_min_psi": None,
            "recommended_max_psi": None,
            "pressures": pressures,
        }

    low, high = recommended
    status = "Good"
    for psi in pressures.values():
        if psi is None:
            status = None
            break
        if psi < low or psi > high:
            status = "Attention"
            break

    return status, {
        "model": model,
        "recommended_min_psi": low,
        "recommended_max_psi": high,
        "pressures": pressures,
    }


SENSOR_DESCRIPTIONS = [
    SensorDescription(
        key="odometer_miles",
        name="Odometer",
        unit=UnitOfLength.MILES,
        icon="mdi:counter",
        value_fn=lambda v: v.get("Odometer", {}).get("Miles"),
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorDescription(
        key="fuel_level",
        name="Fuel Level",
        unit=PERCENTAGE,
        icon="mdi:gas-station",
        value_fn=lambda v: v.get("DistanceToEmpty", {}).get("Percentage"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorDescription(
        key="range_km",
        name="Range",
        unit=UnitOfLength.KILOMETERS,
        icon="mdi:map-marker-distance",
        value_fn=_range_kilometers,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorDescription(
        key="avg_mpg",
        name="Average MPG",
        unit="mpg",
        icon="mdi:chart-line",
        value_fn=lambda v: v.get("FuelConsumptionAvg", {}).get("MPG"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorDescription(
        key="ev_soc",
        name="EV State of Charge",
        unit=PERCENTAGE,
        icon="mdi:battery",
        value_fn=lambda v: v.get("EVStatus", {}).get("StateOfChargePercent"),
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorDescription(
        key="ev_range_miles",
        name="EV Range",
        unit=UnitOfLength.MILES,
        icon="mdi:car-electric",
        value_fn=lambda v: v.get("EVStatus", {}).get("DistanceToEmptyMiles"),
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorDescription(
        key="tires_status",
        name="Tire Pressure Status",
        unit=None,
        icon="mdi:car-tire-alert",
        value_fn=lambda v: _tire_status(v)[0],
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
            for desc in SENSOR_DESCRIPTIONS:
                if desc.key.startswith("ev_") and not vehicle.get("EV", False):
                    continue
                key = f"{vin}-{desc.key}"
                if key in added:
                    continue
                added.add(key)
                new_entities.append(MySubaruSensor(vin, vehicle, desc))
        if new_entities:
            async_add_entities(new_entities)

    _maybe_add_entities()
    async_dispatcher_connect(hass, UPDATE_SIGNAL, _maybe_add_entities)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    await async_setup_platform(hass, {}, async_add_entities, None)


class MySubaruSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, vin: str, vehicle: Dict[str, Any], description: SensorDescription) -> None:
        self._vin = vin
        self._description = description
        self._attr_unique_id = f"{vin}-{description.key}"
        self._attr_name = f"{vehicle.get('CarNickname') or vehicle.get('CarName') or vin} {description.name}"
        self._attr_icon = description.icon
        self._attr_native_unit_of_measurement = description.unit
        self._attr_entity_category = description.entity_category
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class

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

        self._attr_available = True
        extra: Dict[str, Any] = {
            "vin": self._vin,
            "timestamp": store.get("timestamp"),
        }

        if self._description.key == "tires_status":
            status, details = _tire_status(vehicle)
            self._attr_native_value = status
            extra.update(details)
        else:
            self._attr_native_value = self._description.value_fn(vehicle)

        self._attr_extra_state_attributes = extra
        self.hass.add_job(self.async_write_ha_state)

    @property
    def device_info(self):
        store: Dict[str, Any] = self.hass.data.get(DOMAIN, {})
        vehicle = store.get("vehicles", {}).get(self._vin, {})
        name = self.name.split(self._description.name)[0].strip()
        return build_device_info(self._vin, vehicle, name)